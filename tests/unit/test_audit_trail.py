"""Production Hardening — Durable Auditability: unit tests for
kernel/pipeline/audit_trail.py, the thin TimelineStore wrapper that
writes PLAN/DECISION lifecycle events (previously unused
TimelineKind.PLAN, per the architecture exploration this feature was
built from).
"""

from __future__ import annotations

from src.monkey_brain.kernel.pipeline.audit_trail import (
    query_audit_timeline,
    record_decision_event,
    record_plan_event,
)
from src.monkey_brain.kernel.timeline.entry import TimelineKind
from src.monkey_brain.kernel.timeline.store import TimelineStore


def setup_function(_fn):
    TimelineStore.reset_for_testing()


def test_record_plan_event_is_queryable_by_execution_id():
    record_plan_event(
        "generated",
        plan_id="plan_001",
        actor_id="alice",
        execution_id="exec_1",
        goal="buy milk",
        steps=("ProductSelection", "OrderCreation"),
    )
    entries = TimelineStore().query("alice", TimelineKind.PLAN)
    assert len(entries) == 1
    assert entries[0].plan_id == "plan_001"
    assert entries[0].status == "generated"
    assert entries[0].correlation_id == "exec_1"


def test_plan_lifecycle_is_append_only_not_overwritten():
    """The exact "plan_001 invalidated / plan_002 active" history the
    spec asks for — achieved by writing NEW entries, never mutating the
    original generated record."""
    record_plan_event(
        "generated",
        plan_id="plan_001",
        actor_id="alice",
        execution_id="exec_1",
        goal="buy milk",
    )
    record_plan_event(
        "invalidated",
        plan_id="plan_001",
        actor_id="alice",
        execution_id="exec_2",
        goal="buy milk",
        result="Whole Milk: out of stock (quantity=0)",
    )
    record_plan_event(
        "generated",
        plan_id="plan_002",
        actor_id="alice",
        execution_id="exec_2",
        goal="buy milk",
        metadata={"replaces": "plan_001"},
    )

    all_plan_entries = TimelineStore().query("alice", TimelineKind.PLAN)
    assert len(all_plan_entries) == 3
    statuses_by_plan_id = [(e.plan_id, e.status) for e in all_plan_entries]
    assert ("plan_001", "generated") in statuses_by_plan_id
    assert ("plan_001", "invalidated") in statuses_by_plan_id
    assert ("plan_002", "generated") in statuses_by_plan_id


def test_record_decision_event_for_idempotency_replay():
    record_decision_event(
        "idempotency_replay",
        actor_id="alice",
        execution_id="key-abc",
        reason="Idempotency-Key 'key-abc' replayed cached result",
    )
    entries = TimelineStore().query("alice", TimelineKind.DECISION)
    assert len(entries) == 1
    assert entries[0].selected_strategy == "idempotency_replay"
    assert entries[0].correlation_id == "key-abc"


def test_query_audit_timeline_merges_plan_execution_decision_by_correlation_id():
    record_plan_event(
        "generated",
        plan_id="plan_001",
        actor_id="bob",
        execution_id="exec_9",
        goal="buy eggs",
    )
    record_decision_event(
        "payment_completed",
        actor_id="bob",
        execution_id="exec_9",
        reason="Charged $5.49",
    )
    # A different execution for the same actor must not leak in.
    record_plan_event(
        "generated",
        plan_id="plan_999",
        actor_id="bob",
        execution_id="exec_OTHER",
        goal="unrelated",
    )
    TimelineStore().record(
        TimelineKind.EXECUTION,
        actor_id="bob",
        goal="buy eggs",
        outcome="success",
        correlation_id="exec_9",
    )

    timeline = query_audit_timeline("bob", "exec_9")
    assert len(timeline) == 3
    kinds = {e["kind"] for e in timeline}
    assert kinds == {"plan", "decision", "execution"}
    assert all(e["correlation_id"] == "exec_9" for e in timeline)
    # Chronological order.
    assert timeline == sorted(timeline, key=lambda e: e["start_time"])


def test_query_audit_timeline_empty_for_unknown_execution():
    assert query_audit_timeline("carol", "no-such-execution") == []


def test_audit_emission_failure_is_non_fatal():
    """A malformed call (e.g. a plan_id that isn't a string) must never
    raise out of the caller's request path — audit is best-effort, not a
    dependency the request itself can fail on."""
    record_plan_event("generated", plan_id=object(), actor_id="alice", execution_id="exec_1", goal="x")
    # No exception propagated — success is simply "didn't crash."
