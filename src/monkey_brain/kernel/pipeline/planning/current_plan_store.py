"""Persistence for an actor's Current Plan — the real, standing plan a
Decide stage compares every freshly generated candidate against
(kernel/pipeline/planning/plan_hysteresis.py::decide).

Mirrors kernel/pipeline/prediction/persistence.py's exact shape (own
lazy Redis singleton, same REDIS_URL/REDIS_HOST/REDIS_PORT convention,
never raises) — deliberately duplicated rather than shared, matching
that module's own established one-small-module-per-concern convention.

Without this, "Current Plan" would only live on the in-memory
ComparisonIntegratedPolicy._current_plans dict (one entry per goal_key,
lazily populated per tick) — every restart, every actor would forget it
ever had a plan and always "replace" (bootstrap) on the very next tick.

Keyed by (actor_id, goal_key), not actor_id alone — a standing plan for
one goal must never suppress or be returned for an unrelated goal (see
kernel/pipeline/planning/goal_key.py::canonicalize_goal).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.pipeline.planning.current_plan_store")

_CURRENT_PLAN_KEY_PREFIX = "monkeybrain:current_plan:"
_client: Any = None
_connect_attempted = False


def _redis_url() -> str:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    return f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"


def _get_client() -> Any:
    """Lazy, module-level singleton — same shape as
    prediction/persistence.py's own lazy backend."""
    global _client, _connect_attempted
    if _client is not None:
        return _client
    try:
        import redis

        client = redis.from_url(
            _redis_url(),
            decode_responses=True,
            socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT_SEC", "5")),
            socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_SEC", "5")),
        )
        client.ping()
        _client = client
        _connect_attempted = True
    except Exception as exc:
        logger.warning("CurrentPlan persistence: Redis unavailable (non-fatal): %s", exc)
        _client = None
    return _client


@dataclass
class CurrentPlanRecord:
    """The actor's standing plan plus the score it was chosen with —
    frozen at the moment it became current, so "kept for N ticks"/"age"
    in the debugger is real, not re-derived.

    to_dict() emits a strict superset of
    kernel/timeline/entry.py::PlanRecord.to_dict()'s field vocabulary
    (entry_id/start_time/end_time/confidence/source/metadata plus
    plan_id/goal/steps/step_descriptions/node_count/completed_nodes/
    cost/risk/status/result) so the frontend's existing PlanRecord-
    shaped rendering keeps working untouched on a CurrentPlanRecord,
    with only additive optional fields for the new score/replacement
    data (see InspectorPanel.tsx's PlanRecord type extension)."""

    plan_id: str
    actor_id: str
    goal: str = ""
    steps: tuple[str, ...] = ()
    step_descriptions: tuple[str, ...] = ()
    cost: float = 0.0
    risk: float = 0.0
    confidence: float = 0.0
    score: float = 0.0
    score_components: dict[str, float] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    kept_count: int = 0
    last_kept_at: float | None = None
    replaced_plan_id: str | None = None
    plan: dict[str, Any] = field(default_factory=dict)
    """The FULL serialized belief_state.Plan (via plan_to_dict below) —
    not just the flattened steps/step_descriptions summary above (which
    exist only for cheap display/PlanRecord-compat). This is what makes
    a "keep" tick able to genuinely re-execute the Current Plan: every
    PlanStep.parameters (e.g. which product/qty a ProductSelection step
    chose), required_permission, preconditions, etc. round-trips through
    this field via plan_from_dict."""
    last_execution_failed: bool = False
    """Set by comparison/integration.py's plan_outcome_feedback stage,
    right after this plan's real execution outcome is known (never at
    creation time — a brand-new "replace" record always starts False).
    plan_hysteresis.py's score_plan only looks at predicted probability/
    utility/cost/risk, never actual execution success, so a plan that
    happens to score competitively can stay "current" — and keep
    re-executing verbatim — forever, even after every real attempt at it
    fails (confirmed live: an LLM-truncated plan missing OrderCreation
    replayed identically across 3 consecutive retries with 0/2 successes
    each time). _run_decide reads this to bypass the score comparison and
    force a replace instead of comparing a fresh candidate against a plan
    that has already demonstrated it doesn't work."""
    entity_versions: dict[str, int] = field(default_factory=dict)
    """{entity_id: kg.version_of(entity_id)} for every entity this plan's
    steps reference, captured via plan_staleness.py::capture_entity_versions
    at the moment this record was saved. Checked by
    plan_staleness.py::check_plan_staleness before either reuse point
    (belief_runtime.py's skip-gate, _run_decide's "keep" branch) is
    allowed to replay this plan — a world state that has moved on since
    (a sold-out product, a delisted store) must force a real replan
    instead of blindly re-executing stale assumptions. Empty for a plan
    that references no entities, or one persisted before this field
    existed — check_plan_staleness treats "nothing recorded" as "nothing
    to compare," never as evidence of staleness."""

    def to_dict(self) -> dict[str, Any]:
        return {
            # PlanRecord-compatible superset:
            "entry_id": self.plan_id,
            "actor_id": self.actor_id,
            "start_time": self.created_at,
            "end_time": None,
            "confidence": self.confidence,
            "source": "plan_hysteresis",
            "metadata": {},
            "plan_id": self.plan_id,
            "goal": self.goal,
            "steps": list(self.steps),
            "step_descriptions": list(self.step_descriptions),
            "node_count": len(self.steps),
            "completed_nodes": 0,
            "cost": self.cost,
            "risk": self.risk,
            "status": "current",
            "result": "",
            # CurrentPlanRecord-only fields:
            "score": self.score,
            "score_components": dict(self.score_components),
            "created_at": self.created_at,
            "kept_count": self.kept_count,
            "last_kept_at": self.last_kept_at,
            "replaced_plan_id": self.replaced_plan_id,
            "plan": self.plan,
            "last_execution_failed": self.last_execution_failed,
            "entity_versions": dict(self.entity_versions),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "CurrentPlanRecord":
        return CurrentPlanRecord(
            plan_id=d.get("plan_id", ""),
            actor_id=d.get("actor_id", ""),
            goal=d.get("goal", ""),
            steps=tuple(d.get("steps", ())),
            step_descriptions=tuple(d.get("step_descriptions", ())),
            cost=float(d.get("cost", 0.0) or 0.0),
            risk=float(d.get("risk", 0.0) or 0.0),
            confidence=float(d.get("confidence", 0.0) or 0.0),
            score=float(d.get("score", 0.0) or 0.0),
            score_components=dict(d.get("score_components", {}) or {}),
            created_at=float(d.get("created_at", time.time())),
            kept_count=int(d.get("kept_count", 0) or 0),
            last_kept_at=d.get("last_kept_at"),
            replaced_plan_id=d.get("replaced_plan_id"),
            plan=dict(d.get("plan", {}) or {}),
            last_execution_failed=bool(d.get("last_execution_failed", False)),
            entity_versions={str(k): int(v) for k, v in (d.get("entity_versions", {}) or {}).items()},
        )


def plan_to_dict(plan: Any) -> dict[str, Any]:
    """belief_state.Plan -> plain JSON-safe dict, full fidelity (every
    PlanStep field, not a summary)."""
    import dataclasses as _dc

    return _dc.asdict(plan)


def plan_from_dict(d: dict[str, Any]) -> Any:
    """Inverse of plan_to_dict — reconstructs a real, executable
    belief_state.Plan (with real PlanStep objects, including
    parameters/required_permission) from a persisted CurrentPlanRecord.
    Used when a "keep" verdict means re-executing the Current Plan."""
    from src.monkey_brain.kernel.pipeline.belief_state import Plan, PlanStep

    steps = tuple(
        PlanStep(
            action=s.get("action", ""),
            description=s.get("description", ""),
            preconditions=tuple(s.get("preconditions", ()) or ()),
            expected_outcome=s.get("expected_outcome", ""),
            cost=float(s.get("cost", 0.0) or 0.0),
            confidence=float(s.get("confidence", 0.0) or 0.0),
            required_permission=s.get("required_permission", ""),
            parameters=dict(s.get("parameters", {}) or {}),
            # Found while wiring execution-checkpoint resume (which needs
            # a re-executed plan's step ordering to survive intact): this
            # never round-tripped depends_on at all, silently flattening
            # every reused plan's dependency graph into "everything
            # independent" -- ActionExecutor would then have run every
            # step immediately regardless of the original plan's real
            # depends_on, on every "keep" (incremental-scheduling reuse)
            # cycle, not just a resumed one.
            depends_on=tuple(s.get("depends_on", ()) or ()),
        )
        for s in (d.get("steps") or ())
    )
    return Plan(
        goal=d.get("goal", ""),
        preconditions=tuple(d.get("preconditions", ()) or ()),
        steps=steps,
        expected_outcomes=tuple(d.get("expected_outcomes", ()) or ()),
        cost=float(d.get("cost", 0.0) or 0.0),
        confidence=float(d.get("confidence", 0.0) or 0.0),
        risk=float(d.get("risk", 0.0) or 0.0),
        start_state=d.get("start_state", ""),
        goal_state=d.get("goal_state", ""),
        planner=d.get("planner", "default"),
        metadata=dict(d.get("metadata", {}) or {}),
    )


def _key(actor_id: str, goal_key: str) -> str:
    """(actor_id, goal_key) is the minimum state boundary for a Current
    Plan — a standing plan for one goal must never be readable as, or
    overwritten by, another goal's plan. goal_key is expected to already
    be canonicalized (kernel/pipeline/planning/goal_key.py::canonicalize_goal)
    by the caller; this module doesn't re-derive it from a raw goal string
    itself, to keep exactly one normalization implementation."""
    return f"{_CURRENT_PLAN_KEY_PREFIX}{actor_id}:{goal_key}"


def save_current_plan(actor_id: str, goal_key: str, record: CurrentPlanRecord) -> bool:
    """Never raises — a dropped write here just means the next tick's
    Decide stage sees no Current Plan for this goal and treats it as
    bootstrap (always replace), same degrade-gracefully shape as
    save_transition_model."""
    client = _get_client()
    if client is None or not actor_id or not goal_key:
        return False
    try:
        client.set(_key(actor_id, goal_key), json.dumps(record.to_dict()))
        return True
    except Exception as exc:
        logger.debug("save_current_plan(%s, %s) failed: %s", actor_id, goal_key, exc)
        return False


def load_current_plan(actor_id: str, goal_key: str) -> CurrentPlanRecord | None:
    """Called at Decide-stage time (kernel/pipeline/comparison/integration.py
    ::_run_decide, lazily per goal_key) and by /cognitive-state's display
    route — if a real prior Current Plan exists FOR THIS EXACT GOAL, it
    seeds ComparisonIntegratedPolicy._current_plans[goal_key] instead of
    always bootstrapping to "replace" on the first tick after a restart.
    A goal_key mismatch (a different goal) always returns None here by
    construction — there is no cross-goal fallback."""
    client = _get_client()
    if client is None or not actor_id or not goal_key:
        return None
    try:
        raw = client.get(_key(actor_id, goal_key))
        if not raw:
            return None
        return CurrentPlanRecord.from_dict(json.loads(raw))
    except Exception as exc:
        logger.debug("load_current_plan(%s, %s) failed: %s", actor_id, goal_key, exc)
        return None
