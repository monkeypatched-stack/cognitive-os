#!/usr/bin/env python3
"""Gradio chat over the local llama.cpp server that can also fly PX4 missions.

Ordinary questions get a streamed chat reply from llama-server. A flight
request ("fly to 15m then x=6 y=7 and return") is turned into a plan of
Arm/Takeoff/Waypoint/Land steps and executed against the edge bridge, with
each step's result streamed back into the conversation as it lands.

The bridge is the same one the drone actor uses (ros_bridge_server ->
Px4RosExecutionAdapter), so every step here is confirmed against real PX4
telemetry rather than just published -- a "ok" below means the vehicle
actually reached that state.

Run:

    /home/varun/.venvs/mission-chat/bin/python scripts/mission_chatbot.py

Then open the printed URL. Requires llama-server on :8090 and at least one
ros_bridge_server (which in turn needs the PX4 sim running).

One bridge serves exactly one drone, because an adapter binds to a single
PX4_NAMESPACE. To fly a fleet, name the bridges:

    MISSION_FLEET="drone1=http://127.0.0.1:9010,drone2=http://127.0.0.1:9011"

Selecting several in the UI flies one plan across all of them in lockstep.
"""

from __future__ import annotations

import concurrent.futures as _futures
import json
import os
import re
from typing import Any, Iterator

import gradio as gr
import requests

LLAMA_URL = os.environ.get("LLAMA_URL", "http://localhost:8090/v1/chat/completions")
LLAMA_MODEL = os.environ.get("LLAMA_MODEL", "gemma-3-4b-it-Q4_K_M")
BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://localhost:9002/invoke")
BRIDGE_LIVE_URL = os.environ.get("BRIDGE_LIVE_URL", "http://localhost:9002/live")


# The fleet, as "label=base_url,label=base_url" in MISSION_FLEET, or just
# the single BRIDGE_URL above. One bridge process serves exactly one drone
# because an adapter binds to one PX4_NAMESPACE, so flying N drones means
# addressing N bases -- a single module-level BRIDGE_URL cannot do it.
def _parse_fleet() -> "dict[str, str]":
    raw = os.environ.get("MISSION_FLEET", "").strip()
    if not raw:
        return {"drone": BRIDGE_URL.rsplit("/", 1)[0]}
    fleet = {}
    for part in raw.split(","):
        label, _, base = part.partition("=")
        label, base = label.strip(), base.strip()
        if label and base:
            fleet[label] = base.rstrip("/")
    return fleet or {"drone": BRIDGE_URL.rsplit("/", 1)[0]}


FLEET = _parse_fleet()

# Mirrors kernel/domains/robot.py and Px4RosExecutionAdapter.invoke: these
# four are the only capabilities the PX4 bus actually implements, so anything
# else in a plan is rejected here rather than sent and failed downstream.
_ALLOWED: dict[str, tuple[str, ...]] = {
    "Arm": (),
    "Takeoff": ("height_m",),
    "Waypoint": ("x", "y", "height_m"),
    "Land": (),
}

# The adapter's own limits (px4_ros_adapter.py): a waypoint must be reached
# within _WAYPOINT_TIMEOUT_S = 25s, so a very long leg can time out even
# though nothing is wrong. Warn rather than refuse -- PX4 cruises fast enough
# for ~55m in practice, and the failure is clean and reported either way.
_LONG_LEG_M = 45.0
_MAX_HEIGHT_M = 120.0
_MAX_RANGE_M = 200.0

_PLAN_SYSTEM_PROMPT = """You control a PX4 quadrotor in a simulator.

If the user is asking you to FLY, respond with ONLY a JSON object, no prose
and no code fences:

{"mission": [ {"capability": "...", "parameters": {...}}, ... ]}

Allowed capabilities and parameters, and nothing else:
  {"capability": "Arm"}
  {"capability": "Takeoff",  "parameters": {"height_m": <metres>}}
  {"capability": "Waypoint", "parameters": {"x": <m>, "y": <m>, "height_m": <m>}}
  {"capability": "Land"}

A Waypoint may instead be written RELATIVE to wherever the vehicle already
is, which is what you should use whenever the user speaks in relative terms
("3m forward", "5m higher", "go up 2m", "come back 4m"):

  {"capability": "Waypoint", "parameters": {"forward": 3}}
  {"capability": "Waypoint", "parameters": {"up": 5}}
  {"capability": "Waypoint", "parameters": {"forward": 3, "right": 2}}
  {"capability": "Waypoint", "parameters": {"home": true}}

Relative keys: forward, back, left, right, up, down. Use "home": true for
"return home"/"back to the start". Never do the addition yourself -- emit one
relative hop per movement the user described, in order, and the arithmetic is
handled for you. Do not mix relative keys with x/y in the same waypoint.

Rules:
- Absolute coordinates are metres in a launch-relative local frame; the
  launch point and "return"/"go back"/"home" all mean x=0, y=0.
- Always start with Arm, then Takeoff.
- An absolute Waypoint should repeat the cruise height in height_m. A
  relative one does not need height_m -- it keeps the current height unless
  you give it up/down.
- Use Land as the last step unless the user explicitly wants to stay hovering.

Worked example. User: "takeoff to 3m, go 3m forward, 5m above, 3m forward
and return to home". Correct answer -- one hop per described movement, no
arithmetic done by you:
{"mission": [{"capability": "Arm"}, {"capability": "Takeoff", "parameters": {"height_m": 3}}, {"capability": "Waypoint", "parameters": {"forward": 3}}, {"capability": "Waypoint", "parameters": {"up": 5}}, {"capability": "Waypoint", "parameters": {"forward": 3}}, {"capability": "Waypoint", "parameters": {"home": true}}, {"capability": "Land"}]}

Worked example. User: "take off to 10m, fly to x=5 y=5, then come back to
the start and land". Correct answer -- note the return leg is x=0 y=0, NOT
the negation of the outbound leg:
{"mission": [{"capability": "Arm"}, {"capability": "Takeoff", "parameters": {"height_m": 10}}, {"capability": "Waypoint", "parameters": {"x": 5, "y": 5, "height_m": 10}}, {"capability": "Waypoint", "parameters": {"x": 0, "y": 0, "height_m": 10}}, {"capability": "Land"}]}

If the user is NOT asking you to fly, do not output JSON at all. Just answer
them normally and conversationally."""

# "come back", "return to the start", "go home" -- all mean the launch point.
# Observed failure: asked to "come back to the start" from (5,5), the model
# planned Waypoint(-5,-5), mirroring the outbound leg. That is a legal
# waypoint, so nothing downstream objects and the mission reports success
# while having flown the wrong path. _ensure_return below is the backstop.
_RETURN_RE = re.compile(r"\b(return|returns|returning|back|home|launch|start|starting)\b", re.IGNORECASE)

# Coordinates the user named explicitly, as "x=5 y=7", "x = 5, y = 7" or
# "(5,7)". Needed to tell a botched return leg from a real destination: if
# the plan's last waypoint is a coordinate the user actually asked for, the
# return leg is simply missing and must be added after it; if it is a
# coordinate the user never mentioned, it IS the mangled return leg and
# should be replaced. Getting this backwards either skips the destination or
# flies to the wrong place.
_XY_RE = re.compile(r"x\s*=?\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*y\s*=?\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_PAIR_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")


# Relative hops, resolved against a running cursor below. The vehicle spawns
# at yaw 0 in PX4's local frame (x North, y East), so with no in-flight yaw
# changes "forward" is +x and "right" is +y for the whole mission.
_REL_KEYS: dict[str, tuple[str, float]] = {
    "forward": ("x", 1.0),
    "back": ("x", -1.0),
    "backward": ("x", -1.0),
    "right": ("y", 1.0),
    "left": ("y", -1.0),
    "up": ("h", 1.0),
    "above": ("h", 1.0),
    "climb": ("h", 1.0),
    "down": ("h", -1.0),
    "descend": ("h", -1.0),
}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_home(params: dict[str, Any]) -> bool:
    return bool(params.get("home") or params.get("return"))


def _user_coords(message: str) -> set[tuple[float, float]]:
    found: set[tuple[float, float]] = set()
    for pattern in (_XY_RE, _PAIR_RE):
        for m in pattern.finditer(message):
            found.add((float(m.group(1)), float(m.group(2))))
    return found


def _chat(messages: list[dict[str, str]], *, stream: bool, max_tokens: int = 512, temperature: float = 0.2) -> Any:
    payload = {
        "model": LLAMA_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }
    return requests.post(LLAMA_URL, json=payload, timeout=300, stream=stream)


def _stream_reply(messages: list[dict[str, str]]) -> Iterator[str]:
    """Yield the reply incrementally from llama.cpp's SSE stream."""
    with _chat(messages, stream=True) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            blob = raw[len("data: ") :].strip()
            if blob == "[DONE]":
                return
            try:
                delta = json.loads(blob)["choices"][0].get("delta", {})
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            piece = delta.get("content")
            if piece:
                yield piece


def _extract_plan(text: str) -> list[dict[str, Any]] | None:
    """Pull a {"mission": [...]} object out of a model reply, if there is one.

    Tolerates the model wrapping JSON in prose or a ```json fence, which it
    does intermittently however firmly the system prompt forbids it.
    """
    if "mission" not in text:
        return None
    for match in re.finditer(r"\{", text):
        snippet = text[match.start() :]
        decoder = json.JSONDecoder()
        try:
            obj, _ = decoder.raw_decode(snippet)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("mission"), list):
            return obj["mission"]
    return None


def _validate(
    plan: list[Any],
    *,
    auto_land: bool,
    wants_return: bool = False,
    user_coords: set[tuple[float, float]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Reject anything the PX4 bus can't do, and coerce params to floats."""
    steps: list[dict[str, Any]] = []
    notes: list[str] = []
    user_coords = user_coords or set()

    for raw in plan:
        if not isinstance(raw, dict):
            notes.append(f"skipped non-object step: {raw!r}")
            continue
        cap = raw.get("capability")
        if cap not in _ALLOWED:
            notes.append(f"skipped unsupported capability: {cap!r}")
            continue
        given = raw.get("parameters") or {}
        if not isinstance(given, dict):
            notes.append(f"{cap}: parameters was not an object")
            continue
        if cap == "Waypoint":
            # Left raw on purpose: a waypoint may be absolute (x/y), relative
            # ("forward": 3, "up": 5), or home. The cursor walk below turns
            # all three into the absolute x/y/height_m the PX4 capability
            # actually takes.
            steps.append({"capability": cap, "raw": given})
            continue
        params: dict[str, float] = {}
        for key in _ALLOWED[cap]:
            value = _num(given.get(key))
            if value is None:
                notes.append(f"{cap}: missing or non-numeric {key}")
                continue
            params[key] = value
        steps.append({"capability": cap, "parameters": params})

    if not steps:
        return [], notes

    cruise = next(
        (
            s.get("parameters", {}).get("height_m")
            for s in steps
            if s["capability"] == "Takeoff" and "height_m" in s.get("parameters", {})
        ),
        None,
    )

    # Walk the plan carrying a cursor so a chain like "3m forward, 5m above,
    # 3m forward, return home" accumulates correctly. This is done in code
    # rather than asked of the model, which cannot reliably chain the
    # arithmetic -- and a wrong sum here is a drone in the wrong place.
    cx, cy, ch = 0.0, 0.0, cruise
    resolved: list[dict[str, Any]] = []
    for step in steps:
        if step["capability"] != "Waypoint":
            if step["capability"] == "Takeoff":
                ch = step["parameters"].get("height_m", ch)
            resolved.append(step)
            continue

        given = step["raw"]
        tx, ty, th = cx, cy, ch
        moves: list[str] = []

        if _is_home(given):
            tx, ty = 0.0, 0.0
            moves.append("home")
        for key, (axis, sign) in _REL_KEYS.items():
            amount = _num(given.get(key)) if key in given else None
            if amount is None:
                if key in given:
                    notes.append(f"Waypoint: {key} is not a number")
                continue
            moves.append(f"{key} {amount:g}m")
            if axis == "x":
                tx += sign * amount
            elif axis == "y":
                ty += sign * amount
            elif th is None:
                notes.append(f"cannot apply '{key}' with no takeoff height to build on")
            else:
                th += sign * amount

        # An explicit absolute value always wins over the cursor.
        for key, slot in (("x", "x"), ("y", "y"), ("height_m", "h")):
            if key in given:
                value = _num(given[key])
                if value is None:
                    notes.append(f"Waypoint: {key} is not a number")
                elif slot == "x":
                    tx = value
                elif slot == "y":
                    ty = value
                else:
                    th = value

        if th is None:
            notes.append("waypoint had no height and no takeoff to infer it from")
            continue
        if moves:
            notes.append(f"{' + '.join(moves)} → ({tx:g},{ty:g}) @ {th:g}m")

        cx, cy, ch = tx, ty, th
        resolved.append(
            {
                "capability": "Waypoint",
                "parameters": {"x": tx, "y": ty, "height_m": th},
                # A relative or home hop is unambiguously deliberate, so the
                # return-leg backstop must never rewrite it.
                "intended": bool(moves),
            }
        )

    steps = resolved
    if not steps:
        return [], notes

    for step in steps:
        height = step["parameters"].get("height_m")
        if height is not None and not 0 < height <= _MAX_HEIGHT_M:
            notes.append(f"refusing height_m={height:g} (allowed 0-{_MAX_HEIGHT_M:g}m)")
            return [], notes
        if step["capability"] == "Waypoint":
            x, y = step["parameters"].get("x", 0.0), step["parameters"].get("y", 0.0)
            if max(abs(x), abs(y)) > _MAX_RANGE_M:
                notes.append(f"refusing waypoint ({x:g},{y:g}) beyond {_MAX_RANGE_M:g}m")
                return [], notes

    if wants_return:
        waypoints = [s for s in steps if s["capability"] == "Waypoint"]
        last = waypoints[-1] if waypoints else None
        here = (last["parameters"].get("x", 0.0), last["parameters"].get("y", 0.0)) if last is not None else None
        if here != (0.0, 0.0):
            height = (last or {}).get("parameters", {}).get("height_m") or cruise
            if height is None:
                notes.append("asked to return home but there is no height to do it at")
            else:
                deliberate = last is not None and (last.get("intended") or here in user_coords)
                if here is not None and not deliberate:
                    # A coordinate the user never named, sitting where the
                    # return leg should be: the mangled return itself.
                    notes.append(
                        f"final leg was ({here[0]:g},{here[1]:g}), which is neither the launch "
                        f"point nor anywhere you asked for; replaced it with (0,0)"
                    )
                    steps = [s for s in steps if s is not last]
                else:
                    notes.append("added the missing return leg to (0,0)")
                home = {"capability": "Waypoint", "parameters": {"x": 0.0, "y": 0.0, "height_m": float(height)}}
                land_at = next((i for i, s in enumerate(steps) if s["capability"] == "Land"), len(steps))
                steps.insert(land_at, home)

    if steps[0]["capability"] != "Arm":
        steps.insert(0, {"capability": "Arm", "parameters": {}})
        notes.append("prepended Arm (PX4 must be armed first)")
    if auto_land and steps[-1]["capability"] != "Land":
        steps.append({"capability": "Land", "parameters": {}})
        notes.append("appended Land (uncheck 'auto-land' to end hovering)")

    # Flag legs the adapter's 25s waypoint timeout may not cover.
    prev = (0.0, 0.0)
    for step in steps:
        if step["capability"] != "Waypoint":
            continue
        here = (step["parameters"].get("x", 0.0), step["parameters"].get("y", 0.0))
        leg = ((here[0] - prev[0]) ** 2 + (here[1] - prev[1]) ** 2) ** 0.5
        if leg > _LONG_LEG_M:
            notes.append(
                f"leg to ({here[0]:g},{here[1]:g}) is {leg:.1f}m; the adapter allows "
                f"25s per waypoint, so this one is tight"
            )
        prev = here

    return steps, notes


def _describe(step: dict[str, Any]) -> str:
    cap, params = step["capability"], step["parameters"]
    if cap == "Takeoff":
        return f"Takeoff {params['height_m']:g}m"
    if cap == "Waypoint":
        x, y = params.get("x", 0.0), params.get("y", 0.0)
        label = "Return (0,0)" if (x, y) == (0.0, 0.0) else f"Waypoint ({x:g},{y:g})"
        return f"{label} @ {params.get('height_m', 0.0):g}m"
    return cap


def _bridge_ready(base: str | None = None) -> tuple[bool, str]:
    url = f"{base}/live" if base else BRIDGE_LIVE_URL
    try:
        body = requests.get(url, timeout=8).json()
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        return False, f"bridge unreachable at {url}: {exc}"
    if not body.get("adapter_ready"):
        return False, f"bridge is up but adapter is not ready: {body}"
    return True, ""


def _invoke(base: str, step: dict[str, Any]) -> dict[str, Any]:
    resp = requests.post(
        f"{base}/invoke",
        json={"capability": step["capability"], "parameters": step["parameters"]},
        timeout=240,
    )
    return resp.json()


def _fly(steps: list[dict[str, Any]], notes: list[str], targets: list[str] | None = None) -> Iterator[str]:
    """Execute the plan on one or more drones, yielding the transcript.

    With several drones this runs step N on all of them together and waits
    for the slowest before step N+1. Flying them as independent missions
    lets them drift apart in time -- one cruising while another is still
    climbing -- which is not a formation. A drone that fails a step is
    dropped and the remaining ones carry on.
    """
    labels = [l for l in (targets or list(FLEET)) if l in FLEET] or list(FLEET)[:1]

    lines = ["**Plan**", ""]
    lines += [f"{i}. {_describe(s)}" for i, s in enumerate(steps, 1)]
    if notes:
        lines += ["", "_" + "; ".join(notes) + "_"]
    lines += ["", f"**Flying** — {', '.join(labels)}", ""]
    yield "\n".join(lines)

    grounded = []
    for label in labels:
        ok, why = _bridge_ready(FLEET[label])
        if not ok:
            lines.append(f"- {label}: not ready — {why}")
            grounded.append(label)
    flying = [l for l in labels if l not in grounded]
    if not flying:
        lines += ["", "Nothing to fly."]
        yield "\n".join(lines)
        return
    if grounded:
        yield "\n".join(lines)

    failed: set[str] = set()
    for step in steps:
        label_txt = _describe(step)
        lines.append(f"- {label_txt} …")
        yield "\n".join(lines)

        results: dict[str, Any] = {}
        active = [l for l in flying if l not in failed]
        with _futures.ThreadPoolExecutor(max_workers=len(active)) as pool:
            jobs = {pool.submit(_invoke, FLEET[l], step): l for l in active}
            for job in _futures.as_completed(jobs):
                label = jobs[job]
                try:
                    results[label] = job.result()
                except Exception as exc:  # noqa: BLE001
                    results[label] = {"success": False, "error": f"bridge error: {exc}"}

        marks = []
        for label in active:
            res = results.get(label, {})
            if res.get("success"):
                extra = {k: v for k, v in res.items() if k not in ("success", "actor_id", "namespace")}
                marks.append(f"{label} ✅" + (f" `{json.dumps(extra)}`" if extra else ""))
            else:
                marks.append(f"{label} ❌ {res.get('error', res)}")
                failed.add(label)
        lines[-1] = f"- {label_txt} — " + " · ".join(marks)
        yield "\n".join(lines)

        if len(failed) >= len(flying):
            lines += ["", "Mission aborted — no drones left flying."]
            yield "\n".join(lines)
            return

    done = [l for l in flying if l not in failed]
    lines += [
        "",
        f"**Mission complete** — {len(done)}/{len(flying)} "
        f"({', '.join(done)}); every step confirmed against PX4 telemetry.",
    ]
    if failed:
        lines.append(f"Dropped mid-mission: {', '.join(sorted(failed))}.")
    yield "\n".join(lines)


def respond(
    message: str, history: list[dict[str, str]], auto_land: bool, dry_run: bool, drones: list[str] | None = None
) -> Iterator[str]:
    convo = [{"role": "system", "content": _PLAN_SYSTEM_PROMPT}]
    for turn in history:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            convo.append({"role": turn["role"], "content": turn["content"]})
    convo.append({"role": "user", "content": message})

    # One non-streamed call so a plan can be inspected whole before anything
    # flies; a partial JSON object is not safe to act on.
    try:
        resp = _chat(convo, stream=False)
        resp.raise_for_status()
        reply = resp.json()["choices"][0]["message"]["content"] or ""
    except Exception as exc:  # noqa: BLE001
        yield f"llama-server error at {LLAMA_URL}: {exc}"
        return

    plan = _extract_plan(reply)
    if plan is None:
        # Not a flight request - answer it properly, streamed.
        convo_chat = [c for c in convo if c["role"] != "system"]
        streamed = ""
        try:
            for piece in _stream_reply(convo_chat):
                streamed += piece
                yield streamed
        except Exception as exc:  # noqa: BLE001
            yield streamed + f"\n\n_(stream failed: {exc})_"
        if not streamed.strip():
            yield reply or "_(empty reply)_"
        return

    steps, notes = _validate(
        plan,
        auto_land=auto_land,
        wants_return=bool(_RETURN_RE.search(message)),
        user_coords=_user_coords(message),
    )
    if not steps:
        yield (
            "I read that as a flight request but could not build a valid plan.\n\n"
            + ("\n".join(f"- {n}" for n in notes) if notes else f"Model said:\n\n{reply}")
        )
        return

    if dry_run:
        picked = [d for d in (drones or list(FLEET)) if d in FLEET] or list(FLEET)[:1]
        out = [f"**Plan** (dry run — nothing sent to {', '.join(picked)})", ""]
        out += [f"{i}. {_describe(s)}" for i, s in enumerate(steps, 1)]
        if notes:
            out += ["", "_" + "; ".join(notes) + "_"]
        yield "\n".join(out)
        return

    yield from _fly(steps, notes, targets=drones)


def main() -> None:
    states = {label: _bridge_ready(base) for label, base in FLEET.items()}
    up = [l for l, (ok, _) in states.items() if ok]
    down = {l: why for l, (ok, why) in states.items() if not ok}
    banner = f"llama.cpp on :8090 · drones ready: {', '.join(up) or 'none'}"
    if down:
        banner += "\n\n" + "\n".join(
            f"⚠️ **{l}** not ready — chat works, it will not fly. ({why})" for l, why in down.items()
        )

    with gr.Blocks(title="Drone mission chat") as demo:
        gr.Markdown(
            "# Drone mission chat\n"
            f"{banner}\n\n"
            "Ask anything, or tell it to fly — *“take off to 20m, go to x=10 y=10, "
            "then come back”*. Coordinates are metres from the launch point. "
            "Every step is confirmed against real PX4 telemetry."
        )
        with gr.Row():
            auto_land = gr.Checkbox(value=True, label="Auto-append Land")
            dry_run = gr.Checkbox(value=False, label="Dry run (show plan, don't fly)")
        drones = gr.CheckboxGroup(
            choices=list(FLEET),
            value=list(FLEET),
            label=("Drones — several fly the same plan in lockstep, each step waiting for the slowest"),
        )
        # gradio >=6 dropped ChatInterface's `type` argument and always uses
        # the openai-style {"role", "content"} message format that `respond`
        # already reads, so there is nothing to pass here.
        gr.ChatInterface(
            fn=respond,
            additional_inputs=[auto_land, dry_run, drones],
            examples=[
                ["fly to a height of 15m then go to x=6 y=7 then return back to x=0 y=0"],
                ["take off to 20m, fly a 15m square, come home and land"],
                ["what altitude did we just fly to?"],
            ],
        )

    demo.launch(
        server_name=os.environ.get("GRADIO_HOST", "127.0.0.1"), server_port=int(os.environ.get("GRADIO_PORT", "7860"))
    )


if __name__ == "__main__":
    main()
