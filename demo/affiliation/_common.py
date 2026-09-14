"""Shared demo-side orchestration helpers for the Affiliation-Based
Communication Governance benchmark suite (MB-3400 through MB-3405) —
real HTTP calls only, no runtime internals. Duplicates
demo/negotiation/_common.py's core helpers (client/call/banner/section/
kv/create_geo/verify_world/force_round) per this codebase's established
convention of one _common.py per demo folder rather than a shared
cross-folder import, and adds the pieces this suite specifically needs:
`affiliate()` (create a real, shared symbolic-group Affiliation via the
existing POST /actors/{id}/relationships route) and `move_actor()`
(real Presence change, the mechanism temporary Society membership is
derived from).
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


def call_allow_error(c: httpx.Client, method: str, path: str, **kwargs: Any) -> tuple[int, dict]:
    """Like call(), but returns (status_code, body) instead of raising —
    for the denial-path assertions this suite makes on purpose (a 403 or
    a success:false body IS the expected, correct result being tested)."""
    r = c.request(method, path, **kwargs)
    if not r.text:
        return r.status_code, {}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {}


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


def create_society(c: httpx.Client, name: str, description: str = "", society_type: str = "generic") -> str:
    result = call(
        c,
        "POST",
        "/societies",
        json={
            "name": name,
            "description": description,
            "society_type": society_type,
        },
    )
    return result["society_id"]


def host_society(c: httpx.Client, space_id: str, society_id: str) -> None:
    call(c, "POST", f"/planet/geo/{space_id}/host", json={"society_id": society_id})


def create_actor(
    c: httpx.Client,
    name: str,
    society_id: str,
    goals: list[str],
    *,
    actor_type: str = "ai_agent",
) -> str:
    result = call(
        c,
        "POST",
        "/actors",
        json={
            "name": name,
            "actor_type": actor_type,
            "goals": goals,
            "society_id": society_id,
            "capabilities": [{"name": "general"}],
        },
    )
    return result["actor_id"]


def affiliate(
    c: httpx.Client,
    actor_id: str,
    group_target_id: str,
    *,
    relationship_type: str = "team_membership",
    strength: float = 1.0,
) -> None:
    """Give `actor_id` a real, persisted Affiliation whose target_id is
    `group_target_id` — a free-form symbolic string, not required to be
    a real actor (see kernel/affiliations/relationship_bridge.py). Two
    actors that each hold an Affiliation pointing at the SAME symbolic
    target_id share that affiliation, which is exactly what
    AffiliationCommunicationRouter checks. Calling this once per actor
    per group is how a real "warehouse_team"-style group affiliation is
    built from the existing pairwise relationships primitive — no new
    "Team" entity type needed."""
    call(
        c,
        "POST",
        f"/actors/{actor_id}/relationships",
        json={
            "source_actor_id": actor_id,
            "target_actor_id": group_target_id,
            "relationship_type": relationship_type,
            "strength": strength,
        },
    )


def move_actor(c: httpx.Client, actor_id: str, space_id: str, activity: str = "") -> dict:
    return call(
        c,
        "POST",
        f"/actors/{actor_id}/move",
        json={"space_id": space_id, "activity": activity},
    )


def ask_actor(
    c: httpx.Client,
    from_actor_id: str,
    from_actor_name: str,
    to_actor_id: str,
    question: str,
) -> tuple[int, dict]:
    """The real POST /actors/{id}/ask route — deterministic (no LLM
    planner in the loop deciding routing), used throughout this suite to
    isolate the affiliation/society ROUTING decision from LLM action-
    choice variance. The reply text itself, when allowed, is still a
    real LLM call (AnswerQuestionCapability) — only which actor gets
    asked is fixed by this suite's own script, the same convention
    demo/conversation/run_conversation.py already established."""
    return call_allow_error(
        c,
        "POST",
        f"/actors/{to_actor_id}/ask",
        json={
            "question": question,
            "from_actor_id": from_actor_id,
            "from_actor_name": from_actor_name,
        },
    )


def force_round(
    c: httpx.Client,
    actor_id: str,
    actor_name: str,
    action_name: str,
    instruction: str,
    extra_context: str = "",
) -> tuple[list[dict], list[dict]]:
    """One /prompt call, explicitly instructed to use exactly one named
    action this turn — the forcing pattern demo/negotiation established.
    Used here only for BroadcastToAffiliation, which (unlike AskActor)
    has no direct HTTP shortcut and must go through the real LLM
    planner's own plan-step choice."""
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
    """The real result dict of the first step matching action_name —
    regardless of success, since a real DENIAL (success:false) is
    exactly what several of this suite's demonstrations verify."""
    for step, outcome in zip(steps, actions):
        if step.get("action") != action_name:
            continue
        result = outcome.get("result")
        if isinstance(result, dict):
            return result
    return None
