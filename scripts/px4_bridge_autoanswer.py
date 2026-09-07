#!/usr/bin/env python3
"""Auto-answer LLM dev_bridge requests for natural-language PX4 drone
missions, by actually parsing the prompt text into a plan -- unlike
scripts/grocery_bridge_autoanswer.py's one fixed PLAN_TEMPLATE, different
prompts here genuinely produce different plans (docs/PX4_MAC_DOCKER.md /
this session's prompt-driven PX4 demo).

This stands in for a real LLM call only because this sandboxed environment
has none configured (MODEL_BACKEND=dev_bridge, the same mechanism
scripts/grocery_bridge_autoanswer.py already relies on for the grocery
demo) -- it is not a second execution path. The plan this script writes
into *.response.txt is parsed by the SAME kernel/pipeline/llm_planner.py
that would parse a real LLM's response, and every step it proposes still
goes through the real ActionExecutor -> ensure_governed ->
run_ros_action_if_governed -> Px4RosExecutionAdapter path untouched by
this script. Only actions the PX4 capability bus actually supports
(kernel/domains/robot.py: Arm/Takeoff/Waypoint/Land) are ever proposed;
anything this parser cannot map to one of those becomes a single step
naming an action that is NOT registered on the capability bus, so the
real ActionExecutor genuinely reports "Capability not found" -- a real
rejection, not a fabricated one.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

BRIDGE_DIR = Path(os.environ.get("LLM_BRIDGE_DIR", "/tmp/mb-llm-bridge"))

_DRONE_KEYWORDS = ("drone", "fly", "flight", "takeoff", "take off", "waypoint", "arm the", "land the", "vehicle")

_TAKEOFF_RE = re.compile(r"take[\s-]*off\s*(?:to|at)?\s*(\d+(?:\.\d+)?)\s*m(?:eters?)?\b", re.IGNORECASE)
_WAYPOINT_RE = re.compile(r"x\s*=\s*(-?\d+(?:\.\d+)?)\D+y\s*=\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_RETURN_RE = re.compile(r"\breturn(?:s|ing|ed)?\b", re.IGNORECASE)
_LAND_RE = re.compile(r"\bland\b", re.IGNORECASE)
_SQUARE_RE = re.compile(r"\bsquare\b", re.IGNORECASE)
_SQUARE_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*m(?:eters?)?\s*square", re.IGNORECASE)


def _should_answer(prompt: str) -> bool:
    # Same convention as grocery_bridge_autoanswer.py: match only the
    # CURRENT tick's actual "Goal:" line, never any substring elsewhere in
    # the prompt (memory/history lines would otherwise cause a stale
    # mission to be re-answered on an unrelated tick).
    goal_line = ""
    for line in prompt.splitlines():
        if line.startswith("Goal:"):
            goal_line = line.lower()
            break
    return any(kw in goal_line for kw in _DRONE_KEYWORDS)


def _step(action: str, description: str, parameters: dict[str, Any], depends_on: list[int],
          *, cost: float = 0.0, confidence: float = 0.9) -> dict[str, Any]:
    return {
        "action": action, "description": description, "expected_outcome": f"{action} completed",
        "cost": cost, "confidence": confidence, "required_permission": "",
        "parameters": parameters, "depends_on": depends_on,
    }


def _extract_goal_text(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("Goal:"):
            return line[len("Goal:"):].strip()
    return prompt.strip()


def build_plan(mission_text: str) -> dict[str, Any]:
    """The actual natural-language -> plan translation. Real parsing, not
    a hardcoded template -- different mission_text genuinely produces a
    different plan, and a mission this parser cannot map to a real,
    registered PX4 capability produces a plan the real ActionExecutor
    will genuinely reject (never silently reinterpreted)."""
    takeoff_match = _TAKEOFF_RE.search(mission_text)
    waypoint_match = _WAYPOINT_RE.search(mission_text)
    wants_square = bool(_SQUARE_RE.search(mission_text))
    wants_return = bool(_RETURN_RE.search(mission_text))
    wants_land = bool(_LAND_RE.search(mission_text))

    if takeoff_match is None and waypoint_match is None and not wants_square:
        # No supported mission content found at all -- name an action
        # that does not exist on the capability bus (kernel/domains/
        # grocery.py::build_default_capability_bus never registers this),
        # so ActionExecutor's own discover() genuinely fails to find it.
        return {
            "steps": [_step(
                "UnsupportedMission",
                f"Could not map {mission_text!r} to a supported PX4 capability "
                "(Arm/Takeoff/Waypoint/Land)",
                {"requested": mission_text}, [],
            )],
            "summary": f"Rejected: no supported PX4 capability for {mission_text!r}",
            "confidence": 0.0,
        }

    height_m = float(takeoff_match.group(1)) if takeoff_match else 2.0
    steps: list[dict[str, Any]] = [_step("Arm", "Arm the vehicle", {}, [])]
    steps.append(_step("Takeoff", f"Take off to {height_m}m", {"height_m": height_m}, [0]))

    if wants_square:
        size_match = _SQUARE_SIZE_RE.search(mission_text)
        side = float(size_match.group(1)) if size_match else 5.0
        corners = [(side, 0.0), (side, side), (0.0, side)]
        for x, y in corners:
            steps.append(_step(
                "Waypoint", f"Fly to ({x}, {y})", {"x": x, "y": y, "height_m": height_m}, [len(steps) - 1],
            ))
    elif waypoint_match:
        x, y = float(waypoint_match.group(1)), float(waypoint_match.group(2))
        steps.append(_step(
            "Waypoint", f"Fly to ({x}, {y})", {"x": x, "y": y, "height_m": height_m}, [len(steps) - 1],
        ))

    if wants_return:
        steps.append(_step(
            "Waypoint", "Return to the starting point", {"x": 0.0, "y": 0.0, "height_m": height_m},
            [len(steps) - 1],
        ))

    if wants_land:
        steps.append(_step("Land", "Land the vehicle", {}, [len(steps) - 1]))

    return {
        "steps": steps,
        "summary": mission_text,
        "confidence": 0.9,
    }


def main() -> None:
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Watching {BRIDGE_DIR} for PX4 drone-mission planner requests...")
    seen: set[str] = set()
    while True:
        for req in BRIDGE_DIR.glob("*.request.json"):
            rid = req.stem.replace(".request", "")
            if rid in seen:
                continue
            try:
                payload = json.loads(req.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            prompt = payload.get("prompt", "")
            if not _should_answer(prompt):
                continue
            resp = BRIDGE_DIR / f"{rid}.response.txt"
            if resp.exists():
                seen.add(rid)
                continue
            mission_text = _extract_goal_text(prompt)
            plan = build_plan(mission_text)
            resp.write_text(json.dumps(plan, indent=2))
            seen.add(rid)
            print(f"answered {rid}: {mission_text!r} -> "
                  f"{[s['action'] for s in plan['steps']]}")
        time.sleep(0.3)


if __name__ == "__main__":
    main()
