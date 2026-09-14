"""Shared demo-side orchestration helpers for the Game-Theoretic
Reasoning benchmark suite (MB-3300 through MB-3310) — real HTTP calls
only, no runtime internals, no capability logic. Given eleven
benchmarks share the same "force one action per round, relay real
results forward" pattern demo/dialogue proved works, this module
exists purely to avoid duplicating that orchestration eleven times,
the same way each demo/coordination/bootstrap_mb31XX.py already
duplicates its own `_call`/`ApiError` boilerplate — just consolidated
here given the larger count this time.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

BASE_URL = os.getenv("DEMO_BASE_URL", "http://localhost:8031/api/v1/agentos")
TIMEOUT = 180.0
WIDTH = 56


class ApiError(RuntimeError):
    """A production API call returned a non-2xx response."""


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)


def call(c: httpx.Client, method: str, path: str, **kwargs: Any) -> dict:
    r = c.request(method, path, **kwargs)
    if r.status_code >= 300:
        raise ApiError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
    if not r.text:
        return {}
    try:
        return r.json()
    except Exception:
        return {}


def banner(title: str) -> None:
    print("\n" + "=" * WIDTH)
    print(title)
    print("=" * WIDTH)


def section(title: str) -> None:
    print("\n" + "-" * WIDTH)
    print(title)
    print("-" * WIDTH)


def kv(label: str, value: Any, width: int = 30) -> None:
    dots = "." * max(1, width - len(label))
    print(f"{label} {dots} {value}")


def create_geo(c: httpx.Client, entity_type: str, name: str, parent_id: str | None = None) -> str:
    body: dict[str, Any] = {"entity_type": entity_type, "name": name}
    if parent_id:
        body["parent_id"] = parent_id
    result = call(c, "POST", "/planet/geo", json=body)
    return result["entity_id"]


def verify_world(c: httpx.Client, attempts: int = 4, delay_seconds: float = 2.0) -> dict:
    last_result: dict | None = None
    for attempt in range(attempts):
        result = call(c, "POST", "/verify/world")
        if result.get("ok", False):
            return result
        last_result = result
        violations = result.get("violations", [])
        only_presence = bool(violations) and all(v.get("category") == "presence_consistency" for v in violations)
        if not only_presence or attempt == attempts - 1:
            break
        time.sleep(delay_seconds)
    raise ApiError(f"World validation failed: {last_result}")


def force_round(
    c: httpx.Client,
    actor_id: str,
    actor_name: str,
    action_name: str,
    instruction: str,
    extra_context: str = "",
) -> tuple[list[dict], list[dict]]:
    """One /prompt call, explicitly instructed to use exactly one named
    action this turn — the same forcing pattern demo/dialogue's Round 1
    used successfully. Returns (steps, actions) so the caller can
    inspect the REAL result; this function writes only the prompt
    TEXT, never the outcome. `extra_context` is real, previously
    observed facts (a prior round's real result) the caller relays
    forward — the same relay-real-facts convention every negotiation
    benchmark in this suite uses."""
    prompt_text = (
        (f"{extra_context}\n\n" if extra_context else "")
        + f"Your plan for this turn MUST contain exactly one step, using "
        f'action "{action_name}" and nothing else. {instruction}'
    )
    response = call(
        c,
        "POST",
        "/prompt",
        json={"question": prompt_text},
        headers={"X-User-ID": actor_id},
    )
    execution = (response.get("query_result") or {}).get("actor_execution") or {}
    plan = execution.get("plan") or {}
    steps = plan.get("steps") or []
    actions = execution.get("actions") or []
    print(f"\n{actor_name}'s plan this round: " + (" -> ".join(s.get("action", "?") for s in steps) or "(no steps)"))
    return steps, actions


def first_result(action_name: str, steps: list[dict], actions: list[dict]) -> dict | None:
    """The real result dict of the first step matching action_name that
    succeeded, or None — a thin, honest lookup, never a fabricated
    fallback."""
    for step, outcome in zip(steps, actions):
        if step.get("action") != action_name or not outcome.get("success"):
            continue
        result = outcome.get("result")
        if isinstance(result, dict):
            return result
    return None
