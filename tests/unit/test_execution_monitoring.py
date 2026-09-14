"""Tests for execution monitoring (Step 9.6).

Validates ExecutionMonitor's live composition (.dispatch(), drop-in with
ExecutionScheduler and RetryExecutor via duck typing — no changes to either),
the timeline, progress, and metrics views, and — the one real integration
wrinkle this step surfaced — that live composition alone never sees steps
the scheduler SKIPPED (built directly, without calling dispatch()), while
observe_all() correctly fills that gap without double-counting.
"""

from __future__ import annotations

from src.monkey_brain.kernel.pipeline.planning.domain import PlanningOperator
from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
    ExecutionStep,
    ExecutionPlan,
    ExecutionContext,
    ExecutionStatus,
    ExecutionOutcome,
    ExecutionError,
    RetryPolicy,
    RetryStrategy,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.handlers import (
    ExecutionRegistry,
    ExecutionCapability,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.scheduler import (
    ExecutionScheduler,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.retry import RetryExecutor
from src.monkey_brain.kernel.pipeline.execution_runtime.monitoring import (
    TimelineEventType,
    TimelineEntry,
    ExecutionTimeline,
    ExecutionProgress,
    ExecutionMonitor,
)


def _step(
    operator_name: str = "Navigate",
    dependencies: tuple[str, ...] = (),
    retry_policy: RetryPolicy | None = None,
    **parameters,
) -> ExecutionStep:
    # retry_policy is a real top-level ExecutionStep field, not an
    # operator parameter -- without pulling it out of **parameters here,
    # a caller passing retry_policy=... silently stored it in
    # step.parameters["retry_policy"] instead of step.retry_policy,
    # leaving the step on RetryPolicy()'s default (strategy=NONE) and
    # making RetryExecutor give up after the first failure instead of
    # actually retrying.
    kwargs = {
        "operator": PlanningOperator(name=operator_name),
        "dependencies": dependencies,
        "parameters": parameters,
    }
    if retry_policy is not None:
        kwargs["retry_policy"] = retry_policy
    return ExecutionStep(**kwargs)


class _FlakyHandler:
    operator_name = "FlakyOp"
    capability = ExecutionCapability(name="flaky")

    def __init__(self, fail_count: int) -> None:
        self.fail_count = fail_count
        self.calls = 0

    def validate(self, step):
        return None

    def execute(self, step, context):
        self.calls += 1
        if self.calls <= self.fail_count:
            return ExecutionOutcome(
                step_id=step.step_id,
                status=ExecutionStatus.FAILED,
                error=ExecutionError(step_id=step.step_id, code="TRANSIENT", retryable=True),
            )
        return ExecutionOutcome(step_id=step.step_id, status=ExecutionStatus.SUCCEEDED, output={"ok": True})


# ═══════════════════════════════════════════════════════════════════════════
# Live composition — .dispatch()
# ═══════════════════════════════════════════════════════════════════════════


class TestLiveComposition:
    def test_dispatch_returns_outcome_unchanged(self):
        monitor = ExecutionMonitor()
        step = _step("Navigate", destination="store")

        outcome = monitor.dispatch(step, ExecutionContext())

        assert outcome.status == ExecutionStatus.SUCCEEDED
        assert outcome.output["arrived_at"] == "store"

    def test_composes_into_scheduler_with_zero_scheduler_changes(self):
        monitor = ExecutionMonitor(total_steps=2)
        scheduler = ExecutionScheduler(registry=monitor)
        a = _step("Navigate", destination="store")
        b = _step("QueryInventory", dependencies=(a.step_id,), item="milk")
        plan = ExecutionPlan(steps=(a, b))

        schedule, outcomes = scheduler.run(plan, ExecutionContext())

        assert schedule.is_valid
        assert all(o.success for o in outcomes)
        assert len(monitor.outcomes()) == 2

    def test_triple_composition_monitor_retry_registry(self):
        """Monitor wraps RetryExecutor wraps ExecutionRegistry — the whole
        Step 9.2-9.6 stack composes via duck typing with no glue code."""
        flaky = _FlakyHandler(fail_count=1)
        registry = ExecutionRegistry()
        registry.register(flaky)
        retry_executor = RetryExecutor(registry=registry, sleep_fn=lambda d: None)
        monitor = ExecutionMonitor(registry=retry_executor, total_steps=1)

        step = _step(
            "FlakyOp",
            retry_policy=RetryPolicy(
                strategy=RetryStrategy.FIXED_DELAY,
                max_attempts=3,
                base_delay_seconds=0.0,
            ),
        )
        outcome = monitor.dispatch(step, ExecutionContext())

        assert outcome.success is True
        assert outcome.attempt == 2
        assert monitor.metrics().retried == 1


# ═══════════════════════════════════════════════════════════════════════════
# Timeline
# ═══════════════════════════════════════════════════════════════════════════


class TestTimeline:
    def test_records_started_and_succeeded_events(self):
        monitor = ExecutionMonitor()
        step = _step("Navigate", destination="store")

        monitor.dispatch(step, ExecutionContext())
        timeline = monitor.timeline()

        events = [e.event_type for e in timeline.events_for_step(step.step_id)]
        assert events == [
            TimelineEventType.STEP_STARTED,
            TimelineEventType.STEP_SUCCEEDED,
        ]

    def test_records_failed_event_with_error_detail(self):
        monitor = ExecutionMonitor()
        step = _step("Navigate")  # missing destination -> fails

        monitor.dispatch(step, ExecutionContext())
        entries = monitor.timeline().events_for_step(step.step_id)

        assert entries[-1].event_type == TimelineEventType.STEP_FAILED
        assert "destination" in entries[-1].detail

    def test_events_for_unrelated_step_are_empty(self):
        monitor = ExecutionMonitor()
        monitor.dispatch(_step("Navigate", destination="store"), ExecutionContext())
        assert monitor.timeline().events_for_step("never-dispatched") == ()

    def test_duration_seconds_spans_first_to_last_entry(self):
        entries = (
            TimelineEntry(step_id="a", timestamp=100.0),
            TimelineEntry(step_id="a", timestamp=102.5),
        )
        timeline = ExecutionTimeline(entries=entries)
        assert timeline.duration_seconds() == 2.5

    def test_duration_seconds_zero_with_fewer_than_two_entries(self):
        assert ExecutionTimeline().duration_seconds() == 0.0
        assert ExecutionTimeline(entries=(TimelineEntry(),)).duration_seconds() == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Progress
# ═══════════════════════════════════════════════════════════════════════════


class TestProgress:
    def test_percent_complete_and_is_complete(self):
        monitor = ExecutionMonitor(total_steps=2)
        monitor.dispatch(_step("Navigate", destination="store"), ExecutionContext())
        progress_mid = monitor.progress()
        assert progress_mid.percent_complete == 50.0
        assert progress_mid.is_complete is False

        monitor.dispatch(_step("QueryInventory", item="milk"), ExecutionContext())
        progress_done = monitor.progress()
        assert progress_done.percent_complete == 100.0
        assert progress_done.is_complete is True

    def test_empty_progress_reports_100_percent_not_a_crash(self):
        """Zero total steps must not divide by zero — treated as vacuously complete."""
        progress = ExecutionMonitor(total_steps=0).progress()
        assert progress.percent_complete == 100.0

    def test_succeeded_failed_skipped_counted_separately(self):
        monitor = ExecutionMonitor()
        monitor.dispatch(_step("Navigate", destination="store"), ExecutionContext())  # succeeds
        monitor.dispatch(_step("Navigate"), ExecutionContext())  # fails (no destination)
        monitor.observe_all((ExecutionOutcome(step_id="skipped-1", status=ExecutionStatus.SKIPPED),))

        progress = monitor.progress()
        assert progress.succeeded_steps == 1
        assert progress.failed_steps == 1
        assert progress.skipped_steps == 1
        assert progress.completed_steps == 3

    def test_total_steps_inferred_when_not_provided(self):
        monitor = ExecutionMonitor()  # no total_steps given
        monitor.dispatch(_step("Navigate", destination="store"), ExecutionContext())
        progress = monitor.progress()
        assert progress.total_steps == 1  # inferred from completed + in_progress


# ═══════════════════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════════════════


class TestMetrics:
    def test_metrics_reflect_outcomes(self):
        monitor = ExecutionMonitor()
        monitor.dispatch(_step("Navigate", destination="store"), ExecutionContext())
        monitor.dispatch(_step("Navigate"), ExecutionContext())

        metrics = monitor.metrics()
        assert metrics.total_steps == 2
        assert metrics.succeeded == 1
        assert metrics.failed == 1
        assert metrics.success_rate == 0.5

    def test_retried_counts_steps_with_attempt_greater_than_one(self):
        flaky = _FlakyHandler(fail_count=1)
        registry = ExecutionRegistry()
        registry.register(flaky)
        retry_executor = RetryExecutor(registry=registry, sleep_fn=lambda d: None)
        monitor = ExecutionMonitor(registry=retry_executor)

        monitor.dispatch(
            _step(
                "FlakyOp",
                retry_policy=RetryPolicy(
                    strategy=RetryStrategy.FIXED_DELAY,
                    max_attempts=3,
                    base_delay_seconds=0.0,
                ),
            ),
            ExecutionContext(),
        )

        assert monitor.metrics().retried == 1


# ═══════════════════════════════════════════════════════════════════════════
# The skip-visibility gap and observe_all()
# ═══════════════════════════════════════════════════════════════════════════


class TestObserveAll:
    def test_live_composition_alone_misses_skipped_steps(self):
        """The scheduler builds SKIPPED outcomes directly, without calling
        dispatch() — this is the real gap observe_all() exists to close."""
        monitor = ExecutionMonitor()
        scheduler = ExecutionScheduler(registry=monitor)
        a = _step("Navigate")  # missing destination -> fails
        b = _step("QueryInventory", dependencies=(a.step_id,), item="milk")
        plan = ExecutionPlan(steps=(a, b))

        _, outcomes = scheduler.run(plan, ExecutionContext())

        assert len(outcomes) == 2  # scheduler's own ground truth: both accounted for
        assert len(monitor.outcomes()) == 1  # monitor only saw the dispatched one (a)

    def test_observe_all_fills_the_gap(self):
        monitor = ExecutionMonitor()
        scheduler = ExecutionScheduler(registry=monitor)
        a = _step("Navigate")
        b = _step("QueryInventory", dependencies=(a.step_id,), item="milk")
        plan = ExecutionPlan(steps=(a, b))

        _, outcomes = scheduler.run(plan, ExecutionContext())
        monitor.observe_all(outcomes)

        assert len(monitor.outcomes()) == 2
        by_id = {o.step_id: o for o in monitor.outcomes()}
        assert by_id[a.step_id].status == ExecutionStatus.FAILED
        assert by_id[b.step_id].status == ExecutionStatus.SKIPPED

    def test_observe_all_does_not_double_count_already_dispatched_steps(self):
        monitor = ExecutionMonitor()
        step = _step("Navigate", destination="store")
        outcome = monitor.dispatch(step, ExecutionContext())

        monitor.observe_all((outcome,))  # same step_id, already recorded

        assert len(monitor.outcomes()) == 1
        assert len(monitor.timeline().events_for_step(step.step_id)) == 2  # not 4


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_planning_engine_or_cognitive_runtime_coupling(self):
        import inspect
        import src.monkey_brain.kernel.pipeline.execution_runtime.monitoring as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "planning_engine",
            "from src.monkey_brain.kernel.pipeline.planner import",
        ]
        for imp in forbidden:
            assert imp not in source, f"monitoring.py must not import: {imp}"
