"""Shared real-boundary helpers for the minimal CognitiveOS E2E suite.

Every helper here calls the live HTTP API (AGENTOS_URL/api/v1/agentos/...)
— nothing here constructs PlanCompiler/PredictionResult/Comparator/
Learner/BeliefRuntime objects directly. That boundary rule is the
whole point of this suite (see the module docstring on each test
file), so it's enforced by construction here rather than repeated per
test.

Comparator attribution: earlier versions of this suite could only read
the Comparator's outcome by tailing the live server's own log
(comparator_runtime.py's "Comparison: outcome=..." line had no
execution_id, and this server's background autonomous actor loop
pollutes the shared log with unrelated concurrent lines — confirmed
live: one run observed 11 unrelated Comparison lines inside a single
test's own request window). Both are now fixed in production
(minimally): the log line includes execution_id, and
cognitive_actor.py::_record_cognitive_artifacts persists
comparator_outcome/actor_loss/world_loss/policy_loss onto the same
PLAN Timeline record every tick already writes (tagged with the same
execution_id). Tests now read comparator_outcome directly off that
record via actor_get(actor_id, "plans") instead of tailing any log.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import pytest

AGENTOS_URL = os.getenv("AGENTOS_URL", "http://localhost:8031")
BASE = f"{AGENTOS_URL}/api/v1/agentos"

# LLM-planner ticks observed at 1-90s in this environment (Ollama, real
# grounding/KG lookups) — see the calibration calls this suite's design
# was based on. A later real run of this suite hit a 180s ReadTimeout
# under this suite's own concurrent load (several tests, several real
# LLM calls each, plus this server's background autonomous actor loop,
# all contending for the same local Ollama instance) — generous but not
# unbounded.
PROMPT_TIMEOUT = httpx.Timeout(240.0)

# Stages in the order the task spec defines them, for FIRST FAILURE
# diagnostics (see first_failure_stage below).
STAGE_ORDER = (
    "REQUEST",
    "INTENT",
    "GOAL",
    "KNOWLEDGE",
    "GROUNDING",
    "PLAN",
    "HYSTERESIS",
    "COMPILATION",
    "PREDICTION",
    "EXECUTION",
    "OBSERVATION",
    "COMPARATOR",
    "LEARNING",
    "BELIEF",
)


def _reachable() -> bool:
    try:
        r = httpx.get(f"{AGENTOS_URL}/health", timeout=3.0)
        return r.status_code < 500
    except Exception:
        return False


requires_live_backend = pytest.mark.skipif(
    not _reachable(),
    reason=f"AgentOS backend not reachable at {AGENTOS_URL}",
)


def get(path: str, **kwargs: Any) -> httpx.Response:
    return httpx.get(f"{BASE}{path}", timeout=PROMPT_TIMEOUT, **kwargs)


def post(path: str, json: dict[str, Any], user_id: str | None = None, **kwargs: Any) -> httpx.Response:
    headers = {"X-User-ID": user_id} if user_id else {}
    return httpx.post(f"{BASE}{path}", json=json, headers=headers, timeout=PROMPT_TIMEOUT, **kwargs)


def family_society_id() -> str:
    resp = get("/societies")
    resp.raise_for_status()
    for s in resp.json():
        if s["name"] == "Sharma Family":
            return s["society_id"]
    raise RuntimeError("'Sharma Family' society not found — run scripts/seed_world.py seed first")


def find_actor_id(name: str) -> str:
    """Look up one of the pre-seeded demo actors by name (scripts/seed_world.py)."""
    resp = get("/actors")
    resp.raise_for_status()
    for a in resp.json():
        if a["name"] == name:
            return a["actor_id"]
    raise RuntimeError(f"actor {name!r} not found — run scripts/seed_world.py seed first")


def create_actor(label: str, *, society_id: str | None = None) -> str:
    """Register a brand-new actor through the real POST /actors boundary —
    this is this suite's 'fresh actor' isolation mechanism (see each
    test's TEST ISOLATION note). Unique name per call so two test runs
    (or two tests) never collide on the same actor."""
    body = {
        "name": f"E2E {label} {uuid.uuid4().hex[:8]}",
        "actor_type": "human",
        "description": f"Throwaway actor created by the CognitiveOS E2E suite for {label}.",
        "society_id": society_id or "",
        "goals": [],
    }
    resp = post("/actors", body, user_id="e2e-cognitive-loop-suite")
    resp.raise_for_status()
    return resp.json()["actor_id"]


def prompt(actor_id: str, question: str, *, run_simulate: bool = False, max_retries: int = 4) -> dict[str, Any]:
    """POST /prompt as `actor_id` — the exact real boundary
    src/monkey_brain/api/routes/prompt.py::unified_prompt exposes, and
    the one scripts/seed_world.py demo() itself uses. Returns the full
    decoded PromptResponse JSON.

    Real finding, confirmed by actually running this suite:
    PlanetaryRuntime allows only ONE tick in flight for the whole
    planet at a time, and this server's own background autonomous
    actor loop keeps ticking other actors independently of any test —
    a real, transient "A planetary tick is already running" failure
    (still HTTP 200; prompt.py's own except-block reports it inside
    query_result.answer, never raises) can genuinely collide with a
    test's own request through no fault of the request itself. This
    is a real lock-contention condition in a live, shared system, not
    a pipeline defect — retried here the way any well-behaved real
    client would retry a locked/busy resource, with a short backoff so
    a retry doesn't just re-collide with the same in-flight tick.
    """
    import time

    for attempt in range(max_retries + 1):
        resp = post(
            "/prompt",
            {"question": question, "run_simulate": run_simulate},
            user_id=actor_id,
        )
        assert resp.status_code == 200, (
            f"REQUEST stage failed: POST /prompt returned {resp.status_code}: {resp.text[:500]}"
        )
        body = resp.json()
        answer = (body.get("query_result") or {}).get("answer") or ""
        if "planetary tick is already running" in answer and attempt < max_retries:
            time.sleep(2.0 * (attempt + 1))
            continue
        return body
    return body  # pragma: no cover — loop always returns above


def tick_result(response: dict[str, Any]) -> dict[str, Any]:
    """Pull the real _CognitiveTickResult (asdict'd) out of a /prompt
    response — see prompt.py::_actor_query_result / cognitive_actor.py::
    _CognitiveTickResult. This IS the pipeline's own result object,
    serialized; nothing here is fabricated by the test."""
    query_result = response.get("query_result")
    assert isinstance(query_result, dict) and query_result.get("actor_execution"), (
        "REQUEST reached the API, but query_result.actor_execution is missing — "
        f"the actor's cognitive tick never ran or raised. Full response: {response}"
    )
    return query_result["actor_execution"]


def actor_get(actor_id: str, suffix: str) -> Any:
    resp = get(f"/actors/{actor_id}/{suffix}")
    resp.raise_for_status()
    return resp.json()


def first_failure_stage(tick: dict[str, Any], *, comparison_outcomes: list[str] | None = None) -> str | None:
    """Walk the pipeline stage order and return the name of the first
    stage whose real artifact is missing from this tick result — used
    to produce the FAILURE DIAGNOSTICS block the task spec requires.
    Returns None if every stage produced real evidence."""
    if not tick:
        return "REQUEST"
    if not tick.get("plan"):
        return "PLAN"
    if not tick.get("actions"):
        return "EXECUTION"
    if not tick.get("predicted_outcome"):
        return "PREDICTION"
    if tick.get("observations") is None:
        return "OBSERVATION"
    if comparison_outcomes is not None and not comparison_outcomes:
        return "COMPARATOR"
    if "learned" not in tick:
        return "LEARNING"
    if "belief_updated" not in tick:
        return "BELIEF"
    return None
