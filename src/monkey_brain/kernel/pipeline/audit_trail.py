"""Durable, execution_id-correlated audit trail — a thin wrapper over
TimelineStore (kernel/timeline/store.py), not a new persistence system.

Production Hardening audit (docs/adr/020-production-hardening-audit.md)
found two existing "audit" systems, both broken/disabled by default:
kernel/audit.py's hash-chained AuditLog (in-memory only in practice) and
introspection/audit.py's @audited decorator (Mongo sink off unless
AUDIT_MONGODB_ENABLED is explicitly set). Neither is what this extends.

TimelineStore is the system that's actually durable (Redis-backed),
append-only, and already execution_id-correlated via
TimelineEntry.correlation_id — it's used for Goal/Belief/Execution
timelines today, but TimelineKind.PLAN/DECISION were defined
(kernel/timeline/entry.py) and never written. This module is the one
place that writes plan-lifecycle and decision audit events, so every
caller (plan invalidation, idempotency replay/conflict, payment
completion) goes through the same durable path instead of each inventing
its own.

Every function here fails soft (log-only) — an audit-emission failure
must never break the request it's describing, matching TimelineStore's
own backend-level fail-soft convention.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.timeline.entry import TimelineKind
from src.monkey_brain.kernel.timeline.store import TimelineStore

logger = logging.getLogger("agentos.pipeline.audit_trail")


def record_plan_event(
    status: str,
    *,
    plan_id: str,
    actor_id: str,
    execution_id: str = "",
    goal: str = "",
    steps: tuple[str, ...] = (),
    step_descriptions: tuple[str, ...] = (),
    result: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """One PLAN timeline entry — status is one of "generated" | "completed"
    | "failed" | "partial" (PlanRecord's existing vocabulary) plus two new
    values this feature introduces: "invalidated" (a stale Current Plan
    was rejected before execution) and "replaced" (a fresh plan replaced
    it). Each call is a NEW entry, never an edit — the append-only history
    of a plan_id's lifecycle (generated -> invalidated, or
    generated -> replaced by a new generated) is reconstructed by querying
    every PLAN entry with a matching plan_id/correlation_id, not by
    mutating one row in place.
    """
    from src.monkey_brain.kernel.compile import _obs

    try:
        TimelineStore().record(
            TimelineKind.PLAN,
            plan_id=plan_id,
            actor_id=actor_id,
            goal=goal,
            steps=tuple(steps),
            step_descriptions=tuple(step_descriptions),
            status=status,
            result=result,
            correlation_id=execution_id,
            source="audit_trail",
            metadata=dict(metadata or {}),
        )
        _obs.counter("audit.events.total", event_type=TimelineKind.PLAN.value, status="success")
    except Exception:
        logger.warning(
            "record_plan_event(%s, plan_id=%r) failed (non-fatal)",
            status,
            plan_id,
            exc_info=True,
        )
        _obs.counter("audit.events.total", event_type=TimelineKind.PLAN.value, status="error")
        _obs.counter("audit.write_errors.total")


def record_decision_event(
    selected_strategy: str,
    *,
    actor_id: str,
    execution_id: str = "",
    reason: str = "",
    evidence: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> None:
    """One DECISION timeline entry — used for cross-cutting runtime
    decisions that aren't plan-lifecycle-shaped, e.g.
    selected_strategy="idempotency_replay"/"idempotency_conflict"."""
    from src.monkey_brain.kernel.compile import _obs

    try:
        TimelineStore().record(
            TimelineKind.DECISION,
            actor_id=actor_id,
            selected_strategy=selected_strategy,
            reason=reason,
            evidence=tuple(evidence),
            correlation_id=execution_id,
            source="audit_trail",
            metadata=dict(metadata or {}),
        )
        _obs.counter(
            "audit.events.total",
            event_type=TimelineKind.DECISION.value,
            status="success",
        )
    except Exception:
        logger.warning(
            "record_decision_event(%s) failed (non-fatal)",
            selected_strategy,
            exc_info=True,
        )
        _obs.counter("audit.events.total", event_type=TimelineKind.DECISION.value, status="error")
        _obs.counter("audit.write_errors.total")


def query_audit_timeline(
    actor_id: str,
    execution_id: str,
    since: float | None = None,
    until: float | None = None,
) -> list[dict[str, Any]]:
    """Every PLAN/EXECUTION/DECISION entry for this actor whose
    correlation_id matches execution_id, merged chronologically — the
    durable reconstruction of "what happened during this execution" the
    Execution Debugger needs (goal -> plan -> validation -> execution ->
    outcome), read from TimelineStore rather than a live in-memory replay.
    """
    store = TimelineStore()
    entries: list[dict[str, Any]] = []
    for kind in (TimelineKind.PLAN, TimelineKind.EXECUTION, TimelineKind.DECISION):
        for entry in store.query(actor_id, kind, since, until):
            if entry.correlation_id == execution_id:
                d = entry.to_dict()
                d["kind"] = kind.value
                entries.append(d)
    entries.sort(key=lambda d: d["start_time"])
    return entries
