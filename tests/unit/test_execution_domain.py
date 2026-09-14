"""Tests for the execution domain model (Step 9.1).

Validates construction, defaults, immutability, and computed properties for
all execution domain types. Pure data — no algorithm, no mocking needed.
Mirrors tests/unit/test_planning_domain.py's structure and conventions.
"""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from src.monkey_brain.kernel.pipeline.planning.domain import PlanningOperator
from src.monkey_brain.kernel.pipeline.execution_runtime import (
    ExecutionStatus,
    RetryStrategy,
    RetryPolicy,
    ExecutionStep,
    ExecutionPlan,
    ExecutionRequest,
    ExecutionContext,
    ExecutionError,
    ExecutionOutcome,
    ExecutionMetrics,
    ExecutionResult,
)

# ═══════════════════════════════════════════════════════════════════════════
# RetryPolicy
# ═══════════════════════════════════════════════════════════════════════════


class TestRetryPolicy:
    def test_defaults_mean_no_retry(self):
        policy = RetryPolicy()
        assert policy.strategy == RetryStrategy.NONE
        assert policy.max_attempts == 1

    def test_exponential_backoff_construction(self):
        policy = RetryPolicy(
            strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
            max_attempts=5,
            base_delay_seconds=1.0,
            max_delay_seconds=30.0,
        )
        assert policy.max_attempts == 5
        assert policy.max_delay_seconds == 30.0

    def test_frozen(self):
        policy = RetryPolicy()
        with pytest.raises(FrozenInstanceError):
            policy.max_attempts = 10


# ═══════════════════════════════════════════════════════════════════════════
# ExecutionStep
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionStep:
    def test_construction_minimal(self):
        step = ExecutionStep()
        assert step.step_id  # auto-generated
        assert step.operator is None
        assert step.status == ExecutionStatus.PENDING
        assert step.dependencies == ()

    def test_construction_full(self):
        operator = PlanningOperator(name="Navigate", required_capabilities=("navigation",))
        step = ExecutionStep(
            operator=operator,
            parameters={"destination": "store"},
            dependencies=("step-0",),
            expected_effects=("at_location",),
            timeout_seconds=60.0,
            retry_policy=RetryPolicy(strategy=RetryStrategy.FIXED_DELAY, max_attempts=3),
            status=ExecutionStatus.SCHEDULED,
            trace_metadata={"source": "test"},
        )
        assert step.operator.name == "Navigate"
        assert step.dependencies == ("step-0",)
        assert step.timeout_seconds == 60.0
        assert step.retry_policy.max_attempts == 3
        assert step.status == ExecutionStatus.SCHEDULED

    def test_step_ids_are_unique(self):
        assert ExecutionStep().step_id != ExecutionStep().step_id

    def test_frozen(self):
        step = ExecutionStep()
        with pytest.raises(FrozenInstanceError):
            step.status = ExecutionStatus.RUNNING


# ═══════════════════════════════════════════════════════════════════════════
# ExecutionPlan / ExecutionRequest / ExecutionContext
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionPlan:
    def test_composes_steps(self):
        steps = (
            ExecutionStep(operator=PlanningOperator(name="Navigate")),
            ExecutionStep(operator=PlanningOperator(name="AcquireItem")),
        )
        plan = ExecutionPlan(steps=steps)
        assert len(plan.steps) == 2
        assert plan.steps[0].operator.name == "Navigate"

    def test_frozen(self):
        plan = ExecutionPlan()
        with pytest.raises(FrozenInstanceError):
            plan.steps = ()


class TestExecutionRequest:
    def test_construction(self):
        plan = ExecutionPlan(steps=(ExecutionStep(),))
        request = ExecutionRequest(plan=plan, actor_id="alice", tenant_id="acme")
        assert request.actor_id == "alice"
        assert request.tenant_id == "acme"
        assert len(request.plan.steps) == 1
        assert request.request_id  # auto-generated

    def test_defaults(self):
        request = ExecutionRequest()
        assert isinstance(request.plan, ExecutionPlan)
        assert request.tenant_id == "default"


class TestExecutionContext:
    def test_composes_request_and_capabilities(self):
        request = ExecutionRequest(actor_id="alice")
        context = ExecutionContext(
            request=request,
            available_capabilities=("navigation", "shopping"),
            completed_step_ids=("step-0",),
        )
        assert context.request.actor_id == "alice"
        assert "shopping" in context.available_capabilities
        assert context.completed_step_ids == ("step-0",)

    def test_defaults(self):
        context = ExecutionContext()
        assert isinstance(context.request, ExecutionRequest)
        assert context.available_capabilities == ()
        assert context.completed_step_ids == ()

    def test_frozen(self):
        context = ExecutionContext()
        with pytest.raises(FrozenInstanceError):
            context.available_capabilities = ("x",)


# ═══════════════════════════════════════════════════════════════════════════
# ExecutionError / ExecutionOutcome
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionError:
    def test_construction(self):
        error = ExecutionError(step_id="s1", code="TIMEOUT", message="timed out", retryable=True)
        assert error.code == "TIMEOUT"
        assert error.retryable is True

    def test_frozen(self):
        error = ExecutionError()
        with pytest.raises(FrozenInstanceError):
            error.code = "x"


class TestExecutionOutcome:
    def test_success_property(self):
        succeeded = ExecutionOutcome(status=ExecutionStatus.SUCCEEDED)
        failed = ExecutionOutcome(status=ExecutionStatus.FAILED)
        assert succeeded.success is True
        assert failed.success is False

    def test_duration_seconds(self):
        outcome = ExecutionOutcome(started_at=100.0, ended_at=102.5)
        assert outcome.duration_seconds == 2.5

    def test_duration_seconds_never_negative(self):
        """Clock skew or a bad ended_at shouldn't produce a negative duration."""
        outcome = ExecutionOutcome(started_at=100.0, ended_at=99.0)
        assert outcome.duration_seconds == 0.0

    def test_carries_error_on_failure(self):
        error = ExecutionError(step_id="s1", code="TIMEOUT")
        outcome = ExecutionOutcome(step_id="s1", status=ExecutionStatus.FAILED, error=error, attempt=2)
        assert outcome.error.code == "TIMEOUT"
        assert outcome.attempt == 2

    def test_resource_usage_defaults_empty(self):
        assert ExecutionOutcome().resource_usage == {}

    def test_frozen(self):
        outcome = ExecutionOutcome()
        with pytest.raises(FrozenInstanceError):
            outcome.status = ExecutionStatus.RUNNING


# ═══════════════════════════════════════════════════════════════════════════
# ExecutionMetrics / ExecutionResult
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionMetrics:
    def test_success_rate(self):
        metrics = ExecutionMetrics(total_steps=4, succeeded=3, failed=1)
        assert metrics.success_rate == 0.75

    def test_success_rate_with_no_steps_is_zero_not_a_crash(self):
        assert ExecutionMetrics().success_rate == 0.0

    def test_frozen(self):
        metrics = ExecutionMetrics()
        with pytest.raises(FrozenInstanceError):
            metrics.succeeded = 1


class TestExecutionResult:
    def test_construction(self):
        outcomes = (
            ExecutionOutcome(step_id="s1", status=ExecutionStatus.SUCCEEDED),
            ExecutionOutcome(step_id="s2", status=ExecutionStatus.FAILED),
        )
        metrics = ExecutionMetrics(total_steps=2, succeeded=1, failed=1)
        result = ExecutionResult(request_id="r1", outcomes=outcomes, metrics=metrics, goal_achieved=False)

        assert result.request_id == "r1"
        assert len(result.outcomes) == 2
        assert result.metrics.success_rate == 0.5
        assert result.goal_achieved is False

    def test_defaults(self):
        result = ExecutionResult()
        assert result.outcomes == ()
        assert isinstance(result.metrics, ExecutionMetrics)
        assert result.goal_achieved is False

    def test_frozen(self):
        result = ExecutionResult()
        with pytest.raises(FrozenInstanceError):
            result.goal_achieved = True


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary — model-only, no coupling to runtime/execution engine
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_planning_engine_or_cognitive_runtime_coupling(self):
        """Step 9.1 must not import PlanningEngine or CognitiveRuntime —
        those stay untouched until Step 9.7."""
        import inspect
        import src.monkey_brain.kernel.pipeline.execution_runtime.domain as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "planning_engine",
            "from src.monkey_brain.kernel.pipeline.planner import",
        ]
        for imp in forbidden:
            assert imp not in source, f"execution_runtime/domain.py must not import: {imp}"

    def test_intentionally_reuses_planning_operator(self):
        """Unlike CognitiveRuntime/PlanningEngine, depending on
        planning.domain.PlanningOperator is intentional (Step 9.2's
        PlanningOperator -> ExecutionHandler mapping needs it)."""
        import inspect
        import src.monkey_brain.kernel.pipeline.execution_runtime.domain as mod

        source = inspect.getsource(mod)
        assert "from src.monkey_brain.kernel.pipeline.planning.domain import PlanningOperator" in source
