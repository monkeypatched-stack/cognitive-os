"""Tests for execution trace & explainability (Step 9.8).

Validates ExecutionTrace construction (build_execution_trace), its
.explain() narrative (the Execution Request -> Dependency Scheduling ->
Capability Resolution -> Execute -> Execution Monitoring -> Execution
Result format from Step 9's own acceptance criteria), .to_dict()
serializability, and IntegratedExecutionEngine.execute_with_trace()'s
attachment across every code path.

Includes a regression test for a real bug caught while building this: for
the empty-actions and fallback paths, the trace's final_result.goal_achieved
must match what execute() actually returns — an earlier version always
derived False from an empty outcomes tuple, contradicting cases where the
real result was a genuine success (empty actions is vacuous success; a
fallback to ActionExecutor can itself succeed).
"""

from __future__ import annotations

import asyncio
import json

from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
    ExecutionRequest,
    ExecutionOutcome,
    ExecutionStatus,
    ExecutionError,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.scheduler import (
    ExecutionSchedule,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.resolution import (
    ResolutionReport,
    ResolutionIssue,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.trace import (
    ExecutionTrace,
    build_execution_trace,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.integration import (
    IntegratedExecutionEngine,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.resolution import (
    ExecutionEnvironment,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.handlers import (
    ExecutionRegistry,
    ExecutionCapability,
)


def _milk_scenario_actions() -> tuple[Action, ...]:
    return (
        Action(
            action_id="s1",
            capability="Navigate",
            parameters={"destination": "store"},
            expected_outcome="at_location",
        ),
        Action(
            action_id="s2",
            capability="QueryInventory",
            parameters={"item": "milk"},
            expected_outcome="milk_availability_known",
        ),
        Action(
            action_id="s3",
            capability="AcquireItem",
            parameters={"item": "milk", "quantity": 2},
            expected_outcome="milk_acquired",
        ),
        Action(
            action_id="s4",
            capability="Navigate",
            parameters={"destination": "home"},
            expected_outcome="at_home",
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# build_execution_trace — aggregation
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildExecutionTrace:
    def test_success_scenario(self):
        engine = IntegratedExecutionEngine()
        result, trace = asyncio.run(engine.execute_with_trace(_milk_scenario_actions(), None))

        assert len(trace.outcomes) == 4
        assert trace.failed_step_ids == ()
        assert trace.skipped_step_ids == ()
        assert trace.final_result.goal_achieved is True

    def test_failure_and_skip_are_recorded(self):
        actions = (
            Action(action_id="s1", capability="Navigate", parameters={}),  # missing destination -> fails
            Action(action_id="s2", capability="QueryInventory", parameters={"item": "milk"}),
        )
        engine = IntegratedExecutionEngine()
        result, trace = asyncio.run(engine.execute_with_trace(actions, None))

        assert trace.failed_step_ids == ("s1",)
        assert trace.skipped_step_ids == ("s2",)
        assert trace.final_result.goal_achieved is False

    def test_retried_steps_are_recorded(self):
        class FlakyHandler:
            operator_name = "FlakyOp"
            capability = ExecutionCapability(name="flaky")

            def __init__(self):
                self.calls = 0

            def validate(self, step):
                return None

            def execute(self, step, context):
                self.calls += 1
                if self.calls < 2:
                    return ExecutionOutcome(
                        step_id=step.step_id,
                        status=ExecutionStatus.FAILED,
                        error=ExecutionError(step_id=step.step_id, code="X", retryable=True),
                    )
                return ExecutionOutcome(
                    step_id=step.step_id,
                    status=ExecutionStatus.SUCCEEDED,
                    output={"ok": True},
                )

        from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
            ExecutionStep,
            ExecutionPlan,
            ExecutionContext,
            RetryPolicy,
            RetryStrategy,
        )
        from src.monkey_brain.kernel.pipeline.execution_runtime.retry import (
            RetryExecutor,
        )
        from src.monkey_brain.kernel.pipeline.execution_runtime.monitoring import (
            ExecutionMonitor,
        )
        from src.monkey_brain.kernel.pipeline.execution_runtime.scheduler import (
            ExecutionScheduler,
        )
        from src.monkey_brain.kernel.pipeline.planning.domain import PlanningOperator

        registry = ExecutionRegistry()
        registry.register(FlakyHandler())
        retry_executor = RetryExecutor(registry=registry, sleep_fn=lambda d: None)
        monitor = ExecutionMonitor(registry=retry_executor)
        scheduler = ExecutionScheduler(registry=monitor)
        step = ExecutionStep(
            operator=PlanningOperator(name="FlakyOp"),
            retry_policy=RetryPolicy(
                strategy=RetryStrategy.FIXED_DELAY,
                max_attempts=3,
                base_delay_seconds=0.0,
            ),
        )
        plan = ExecutionPlan(steps=(step,))
        request = ExecutionRequest(plan=plan, actor_id="alice")
        schedule, outcomes = scheduler.run(plan, ExecutionContext(request=request))
        monitor.observe_all(outcomes)

        trace = build_execution_trace(request, schedule, outcomes, timeline=monitor.timeline())

        assert trace.retried_step_ids == (step.step_id,)
        assert trace.final_result.goal_achieved is True

    def test_resolution_failure_is_recorded(self):
        resolution = ResolutionReport(
            request_id="r1",
            resolved=False,
            issues=(ResolutionIssue(kind="capability", subject="navigation", message="not available"),),
        )
        trace = build_execution_trace(
            ExecutionRequest(request_id="r1"),
            ExecutionSchedule(),
            (),
            resolution=resolution,
            goal_achieved_override=False,
        )
        assert trace.resolution.resolved is False
        assert trace.final_result.goal_achieved is False

    def test_goal_achieved_override_takes_precedence_over_derivation(self):
        """Regression: an empty outcomes tuple must not force goal_achieved
        to False when the caller already knows the real result."""
        trace_true = build_execution_trace(
            ExecutionRequest(),
            ExecutionSchedule(),
            (),
            goal_achieved_override=True,
        )
        trace_false = build_execution_trace(
            ExecutionRequest(),
            ExecutionSchedule(),
            (),
            goal_achieved_override=False,
        )
        assert trace_true.final_result.goal_achieved is True
        assert trace_false.final_result.goal_achieved is False

    def test_custom_rationale_overrides_default(self):
        trace = build_execution_trace(
            ExecutionRequest(),
            ExecutionSchedule(),
            (),
            rationale="custom explanation",
        )
        assert trace.rationale == "custom explanation"


# ═══════════════════════════════════════════════════════════════════════════
# .explain() — the acceptance-criteria narrative shape
# ═══════════════════════════════════════════════════════════════════════════


class TestExplain:
    def test_matches_acceptance_example_structure(self):
        engine = IntegratedExecutionEngine()
        result, trace = asyncio.run(engine.execute_with_trace(_milk_scenario_actions(), None))

        narrative = trace.explain()
        lines = narrative.split("\n")

        assert lines[0] == "Execution Request"
        assert any(line.startswith("Dependency Scheduling") for line in lines)
        assert any(line.startswith("Capability Resolution") for line in lines)
        assert any(line == "Execute:" for line in lines)
        assert any(line.startswith("Execution Monitoring") for line in lines)
        assert any(line.startswith("Execution Result:") for line in lines)
        assert any(line.startswith("Rationale:") for line in lines)

    def test_all_four_steps_named_with_checkmarks(self):
        engine = IntegratedExecutionEngine()
        result, trace = asyncio.run(engine.execute_with_trace(_milk_scenario_actions(), None))
        narrative = trace.explain()

        assert narrative.count("✓") >= 4  # 4 succeeded steps, plus "Capability Resolution ✓"

    def test_failed_and_skipped_steps_use_distinct_marks(self):
        actions = (
            Action(action_id="s1", capability="Navigate", parameters={}),
            Action(action_id="s2", capability="QueryInventory", parameters={"item": "milk"}),
        )
        engine = IntegratedExecutionEngine()
        result, trace = asyncio.run(engine.execute_with_trace(actions, None))
        narrative = trace.explain()

        assert "✗" in narrative  # failed
        assert "⤳" in narrative  # skipped

    def test_retry_attempt_shown_in_narrative(self):
        outcome = ExecutionOutcome(step_id="s1", status=ExecutionStatus.SUCCEEDED, attempt=3)
        trace = ExecutionTrace(outcomes=(outcome,), retried_step_ids=("s1",))
        assert "(attempt 3)" in trace.explain()

    def test_step_labels_used_when_provided(self):
        engine = IntegratedExecutionEngine()
        result, trace = asyncio.run(engine.execute_with_trace(_milk_scenario_actions(), None))
        narrative = trace.explain()
        # step_labels map action_id -> capability, e.g. "s1" -> "Navigate"
        assert "Navigate" in narrative
        assert "AcquireItem" in narrative


# ═══════════════════════════════════════════════════════════════════════════
# .to_dict() — serializability
# ═══════════════════════════════════════════════════════════════════════════


class TestToDict:
    def test_json_serializable(self):
        engine = IntegratedExecutionEngine()
        result, trace = asyncio.run(engine.execute_with_trace(_milk_scenario_actions(), None))
        json.dumps(trace.to_dict())  # must not raise

    def test_dict_contains_all_deliverable_components(self):
        """Scheduled steps, executed steps, retries, failures, outputs,
        timing, final outcome — every component the spec names."""
        engine = IntegratedExecutionEngine()
        result, trace = asyncio.run(engine.execute_with_trace(_milk_scenario_actions(), None))
        d = trace.to_dict()

        assert d["schedule"] is not None
        assert len(d["outcomes"]) == 4
        assert all("duration_seconds" in o for o in d["outcomes"])
        assert all("output" in o for o in d["outcomes"])
        assert "retried_step_ids" in d
        assert "failed_step_ids" in d
        assert "metrics" in d
        assert "goal_achieved" in d


# ═══════════════════════════════════════════════════════════════════════════
# IntegratedExecutionEngine.execute_with_trace() — every path attaches a trace
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteWithTraceAttachment:
    def test_execute_still_works_unchanged(self):
        """.execute() (the required Protocol method) must still return only
        a LegacyExecutionResult — trace attachment must not change it."""
        engine = IntegratedExecutionEngine()
        result = asyncio.run(engine.execute(_milk_scenario_actions(), None))
        assert result.goal_achieved is True

    def test_empty_actions_trace_matches_actual_result(self):
        """Regression: previously always said goal_achieved=False here,
        contradicting the real vacuous-success result."""
        engine = IntegratedExecutionEngine()
        result, trace = asyncio.run(engine.execute_with_trace((), None))
        assert result.goal_achieved is True
        assert trace.final_result.goal_achieved is True

    def test_fallback_trace_matches_actual_result(self):
        """Regression: same bug, for the backward-compatibility fallback path."""
        engine = IntegratedExecutionEngine()
        actions = (Action(action_id="a1", capability="process_milk", parameters={}),)
        result, trace = asyncio.run(engine.execute_with_trace(actions, None))
        assert result.goal_achieved is True
        assert trace.final_result.goal_achieved is True

    def test_resolution_failure_trace_matches_actual_result(self):
        registry = ExecutionRegistry()
        env = ExecutionEnvironment(available_capabilities=())

        class SpyNavigate:
            operator_name = "Navigate"
            capability = ExecutionCapability(name="navigation")

            def validate(self, step):
                return None

            def execute(self, step, context):
                raise AssertionError("must not execute")

        registry.register(SpyNavigate())
        engine = IntegratedExecutionEngine(registry=registry, environment=env)
        actions = (
            Action(
                action_id="s1",
                capability="Navigate",
                parameters={"destination": "store"},
            ),
        )

        result, trace = asyncio.run(engine.execute_with_trace(actions, None))

        assert result.goal_achieved is False
        assert trace.final_result.goal_achieved is False
        assert trace.resolution is not None
        assert trace.resolution.resolved is False

    def test_success_and_failure_traces_agree_with_their_results_across_scenarios(self):
        engine = IntegratedExecutionEngine()
        cases = [
            _milk_scenario_actions(),
            (Action(action_id="s1", capability="Navigate", parameters={}),),  # fails
        ]
        for actions in cases:
            result, trace = asyncio.run(engine.execute_with_trace(actions, None))
            assert result.goal_achieved == trace.final_result.goal_achieved


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_planning_engine_or_cognitive_runtime_coupling(self):
        import inspect
        import src.monkey_brain.kernel.pipeline.execution_runtime.trace as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "planning_engine",
            "from src.monkey_brain.kernel.pipeline.planner import",
        ]
        for imp in forbidden:
            assert imp not in source, f"trace.py must not import: {imp}"
