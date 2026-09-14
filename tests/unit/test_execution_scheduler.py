"""Tests for the execution scheduler (Step 9.3).

Validates dependency-aware batch scheduling (topological grouping), the
dependency graph (violation and deadlock detection), and .run()'s actual
execution — sequential, genuinely-concurrent parallel (timing-verified,
not just API surface), and failure propagation (downstream steps are
skipped, not blindly executed against a broken precondition).
"""

from __future__ import annotations

import time
from dataclasses import replace

from src.monkey_brain.kernel.pipeline.planning.domain import PlanningOperator
from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
    ExecutionStep,
    ExecutionPlan,
    ExecutionContext,
    ExecutionStatus,
    ExecutionOutcome,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.handlers import (
    ExecutionRegistry,
    ExecutionCapability,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.scheduler import (
    ExecutionMode,
    DependencyViolation,
    ExecutionSchedule,
    ExecutionScheduler,
)


def _step(operator_name: str = "Navigate", dependencies: tuple[str, ...] = (), **parameters) -> ExecutionStep:
    return ExecutionStep(
        operator=PlanningOperator(name=operator_name),
        dependencies=dependencies,
        parameters=parameters,
    )


# ═══════════════════════════════════════════════════════════════════════════
# schedule() — batching
# ═══════════════════════════════════════════════════════════════════════════


class TestScheduleBatching:
    def test_linear_chain_produces_one_step_per_batch(self):
        a = _step()
        b = _step(dependencies=(a.step_id,))
        c = _step(dependencies=(b.step_id,))
        plan = ExecutionPlan(steps=(a, b, c))

        schedule = ExecutionScheduler().schedule(plan)

        assert schedule.is_valid
        assert [len(batch) for batch in schedule.batches] == [1, 1, 1]
        assert schedule.batches[0] == (a.step_id,)
        assert schedule.batches[1] == (b.step_id,)
        assert schedule.batches[2] == (c.step_id,)

    def test_independent_steps_share_a_batch(self):
        x, y = _step(), _step()
        z = _step(dependencies=(x.step_id, y.step_id))
        plan = ExecutionPlan(steps=(x, y, z))

        schedule = ExecutionScheduler().schedule(plan)

        assert [len(batch) for batch in schedule.batches] == [2, 1]
        assert set(schedule.batches[0]) == {x.step_id, y.step_id}

    def test_fully_independent_steps_are_one_batch(self):
        steps = tuple(_step() for _ in range(4))
        plan = ExecutionPlan(steps=steps)

        schedule = ExecutionScheduler().schedule(plan)

        assert len(schedule.batches) == 1
        assert len(schedule.batches[0]) == 4

    def test_empty_plan_produces_empty_schedule(self):
        schedule = ExecutionScheduler().schedule(ExecutionPlan(steps=()))
        assert schedule.batches == ()
        assert schedule.is_valid


# ═══════════════════════════════════════════════════════════════════════════
# Dependency violations
# ═══════════════════════════════════════════════════════════════════════════


class TestDependencyViolations:
    def test_unknown_dependency_is_a_violation(self):
        step = _step(dependencies=("does-not-exist",))
        schedule = ExecutionScheduler().schedule(ExecutionPlan(steps=(step,)))

        assert not schedule.is_valid
        assert len(schedule.violations) == 1
        assert schedule.violations[0].missing_dependency == "does-not-exist"

    def test_self_dependency_is_a_violation(self):
        step = _step()
        step = replace(step, dependencies=(step.step_id,))
        schedule = ExecutionScheduler().schedule(ExecutionPlan(steps=(step,)))

        assert not schedule.is_valid
        assert "depends on itself" in schedule.violations[0].message

    def test_invalid_schedule_produces_no_batches(self):
        step = _step(dependencies=("does-not-exist",))
        schedule = ExecutionScheduler().schedule(ExecutionPlan(steps=(step,)))
        assert schedule.batches == ()


# ═══════════════════════════════════════════════════════════════════════════
# Deadlock detection
# ═══════════════════════════════════════════════════════════════════════════


class TestDeadlockDetection:
    def test_two_step_cycle_is_a_deadlock(self):
        a, b = _step(), _step()
        a = replace(a, dependencies=(b.step_id,))
        b = replace(b, dependencies=(a.step_id,))
        schedule = ExecutionScheduler().schedule(ExecutionPlan(steps=(a, b)))

        assert schedule.has_deadlock
        assert set(schedule.deadlocked_step_ids) == {a.step_id, b.step_id}
        assert not schedule.is_valid

    def test_deadlock_among_a_subset_still_schedules_the_rest(self):
        """A cycle among some steps must not prevent scheduling steps
        outside the cycle."""
        free = _step()
        a, b = _step(), _step()
        a = replace(a, dependencies=(b.step_id,))
        b = replace(b, dependencies=(a.step_id,))
        schedule = ExecutionScheduler().schedule(ExecutionPlan(steps=(free, a, b)))

        assert schedule.has_deadlock
        assert free.step_id in schedule.batches[0]
        assert set(schedule.deadlocked_step_ids) == {a.step_id, b.step_id}


# ═══════════════════════════════════════════════════════════════════════════
# run() — sequential execution
# ═══════════════════════════════════════════════════════════════════════════


class TestRunSequential:
    def test_acquire_milk_scenario_all_succeed(self):
        nav1 = _step("Navigate", destination="store")
        inv = _step("QueryInventory", dependencies=(nav1.step_id,), item="milk")
        buy = _step("AcquireItem", dependencies=(inv.step_id,), item="milk", quantity=2)
        nav2 = _step("Navigate", dependencies=(buy.step_id,), destination="home")
        plan = ExecutionPlan(steps=(nav1, inv, buy, nav2))

        schedule, outcomes = ExecutionScheduler().run(plan, ExecutionContext())

        assert schedule.is_valid
        assert len(outcomes) == 4
        assert all(o.success for o in outcomes)
        assert [o.status for o in outcomes] == [ExecutionStatus.SUCCEEDED] * 4

    def test_invalid_schedule_executes_nothing(self):
        step = _step(dependencies=("missing",))
        schedule, outcomes = ExecutionScheduler().run(ExecutionPlan(steps=(step,)), ExecutionContext())
        assert not schedule.is_valid
        assert outcomes == ()


# ═══════════════════════════════════════════════════════════════════════════
# run() — genuine parallelism (timing-verified)
# ═══════════════════════════════════════════════════════════════════════════


class _SlowHandler:
    operator_name = "SlowOp"
    capability = ExecutionCapability(name="slow")

    def validate(self, step):
        return None

    def execute(self, step, context):
        time.sleep(0.2)
        return ExecutionOutcome(
            step_id=step.step_id,
            status=ExecutionStatus.SUCCEEDED,
            output={"slept": True},
        )


class TestRunParallel:
    def test_independent_steps_run_concurrently_not_sequentially(self):
        """3 steps at 0.2s each: sequential must take ~0.6s, parallel ~0.2s.
        A generous threshold avoids test flakiness while still proving real
        concurrency, not just a different code path that happens to look
        parallel."""
        registry = ExecutionRegistry()
        registry.register(_SlowHandler())
        scheduler = ExecutionScheduler(registry=registry)
        plan = ExecutionPlan(steps=tuple(_step("SlowOp") for _ in range(3)))

        start = time.time()
        _, outcomes = scheduler.run(plan, ExecutionContext(), mode=ExecutionMode.PARALLEL)
        elapsed = time.time() - start

        assert all(o.success for o in outcomes)
        assert elapsed < 0.5  # well under the ~0.6s sequential would take

    def test_parallel_mode_still_respects_dependencies(self):
        """Parallel mode must not run a dependent before its dependency —
        only steps within the SAME batch run concurrently."""
        registry = ExecutionRegistry()
        registry.register(_SlowHandler())
        scheduler = ExecutionScheduler(registry=registry)

        first = _step("SlowOp")
        second = _step("Navigate", dependencies=(first.step_id,), destination="store")
        plan = ExecutionPlan(steps=(first, second))

        schedule, outcomes = scheduler.run(plan, ExecutionContext(), mode=ExecutionMode.PARALLEL)

        assert len(schedule.batches) == 2  # not merged into one batch
        assert all(o.success for o in outcomes)


# ═══════════════════════════════════════════════════════════════════════════
# Failure propagation — downstream steps are skipped, not blindly executed
# ═══════════════════════════════════════════════════════════════════════════


class TestFailurePropagation:
    def test_downstream_steps_are_skipped_when_dependency_fails(self):
        a = _step("Navigate")  # missing 'destination' -> validation fails
        b = _step("QueryInventory", dependencies=(a.step_id,), item="milk")
        c = _step("AcquireItem", dependencies=(b.step_id,), item="milk")  # transitively depends on A
        plan = ExecutionPlan(steps=(a, b, c))

        _, outcomes = ExecutionScheduler().run(plan, ExecutionContext())
        by_id = {o.step_id: o for o in outcomes}

        assert by_id[a.step_id].status == ExecutionStatus.FAILED
        assert by_id[b.step_id].status == ExecutionStatus.SKIPPED
        assert by_id[c.step_id].status == ExecutionStatus.SKIPPED  # transitive, not just direct
        assert by_id[b.step_id].error.code == "DEPENDENCY_FAILED"

    def test_independent_steps_unaffected_by_an_unrelated_failure(self):
        failing = _step("Navigate")  # missing destination -> fails
        independent = _step("Notify", recipient="alice", message="hi")
        plan = ExecutionPlan(steps=(failing, independent))

        _, outcomes = ExecutionScheduler().run(plan, ExecutionContext())
        by_id = {o.step_id: o for o in outcomes}

        assert by_id[failing.step_id].status == ExecutionStatus.FAILED
        assert by_id[independent.step_id].status == ExecutionStatus.SUCCEEDED


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_planning_engine_or_cognitive_runtime_coupling(self):
        import inspect
        import src.monkey_brain.kernel.pipeline.execution_runtime.scheduler as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "planning_engine",
            "from src.monkey_brain.kernel.pipeline.planner import",
        ]
        for imp in forbidden:
            assert imp not in source, f"scheduler.py must not import: {imp}"
