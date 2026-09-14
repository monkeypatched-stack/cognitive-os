"""Tests for retry & recovery policies (Step 9.4).

Validates the retry framework (fixed-delay/exponential-backoff timing,
max_attempts exhaustion, non-retryable short-circuiting, and — the actual
point of "tolerate transient failures" — a genuinely flaky handler that
recovers after a few retries), and the four recovery actions (Abort,
Continue, Rollback-as-placeholder, Compensate). Also verifies RetryExecutor
composes into ExecutionScheduler via duck typing with zero changes to
scheduler.py.
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
from src.monkey_brain.kernel.pipeline.execution_runtime.retry import (
    RecoveryAction,
    RecoveryPolicy,
    RetryResult,
    RetryExecutor,
)


def _step(operator_name: str = "Op", retry_policy: RetryPolicy | None = None, **parameters) -> ExecutionStep:
    return ExecutionStep(
        operator=PlanningOperator(name=operator_name),
        parameters=parameters,
        retry_policy=retry_policy or RetryPolicy(),
    )


class _FlakyHandler:
    """Fails a fixed number of times, then succeeds — a real transient
    failure scenario, not just a canned FAILED outcome."""

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
                error=ExecutionError(
                    step_id=step.step_id,
                    code="TRANSIENT",
                    message="flaky",
                    retryable=True,
                ),
            )
        return ExecutionOutcome(step_id=step.step_id, status=ExecutionStatus.SUCCEEDED, output={"ok": True})


class _AlwaysFailsHandler:
    operator_name = "AlwaysFails"
    capability = ExecutionCapability(name="fails")
    retryable = True

    def validate(self, step):
        return None

    def execute(self, step, context):
        return ExecutionOutcome(
            step_id=step.step_id,
            status=ExecutionStatus.FAILED,
            error=ExecutionError(
                step_id=step.step_id,
                code="PERSISTENT",
                message="nope",
                retryable=self.retryable,
            ),
        )


def _sleep_recorder():
    delays: list[float] = []
    return delays, delays.append


# ═══════════════════════════════════════════════════════════════════════════
# Retry framework — a real transient failure actually recovers
# ═══════════════════════════════════════════════════════════════════════════


class TestTransientFailureRecovery:
    def test_recovers_after_retries(self):
        flaky = _FlakyHandler(fail_count=2)
        registry = ExecutionRegistry()
        registry.register(flaky)
        delays, sleep_fn = _sleep_recorder()
        executor = RetryExecutor(registry=registry, sleep_fn=sleep_fn)

        step = _step(
            "FlakyOp",
            retry_policy=RetryPolicy(
                strategy=RetryStrategy.FIXED_DELAY,
                max_attempts=5,
                base_delay_seconds=1.0,
            ),
        )
        result = executor.execute_with_retry(step, ExecutionContext())

        assert result.outcome.success is True
        assert result.attempts == 3
        assert flaky.calls == 3

    def test_fixed_delay_between_each_attempt(self):
        flaky = _FlakyHandler(fail_count=2)
        registry = ExecutionRegistry()
        registry.register(flaky)
        delays, sleep_fn = _sleep_recorder()
        executor = RetryExecutor(registry=registry, sleep_fn=sleep_fn)

        step = _step(
            "FlakyOp",
            retry_policy=RetryPolicy(
                strategy=RetryStrategy.FIXED_DELAY,
                max_attempts=5,
                base_delay_seconds=2.0,
            ),
        )
        executor.execute_with_retry(step, ExecutionContext())

        assert delays == [2.0, 2.0]

    def test_final_outcome_attempt_number_reflects_retries(self):
        flaky = _FlakyHandler(fail_count=1)
        registry = ExecutionRegistry()
        registry.register(flaky)
        executor = RetryExecutor(registry=registry, sleep_fn=lambda d: None)

        step = _step(
            "FlakyOp",
            retry_policy=RetryPolicy(
                strategy=RetryStrategy.FIXED_DELAY,
                max_attempts=3,
                base_delay_seconds=0.0,
            ),
        )
        result = executor.execute_with_retry(step, ExecutionContext())

        assert result.outcome.attempt == 2


# ═══════════════════════════════════════════════════════════════════════════
# Backoff timing
# ═══════════════════════════════════════════════════════════════════════════


class TestBackoffTiming:
    def test_exponential_backoff_doubles_each_attempt(self):
        registry = ExecutionRegistry()
        registry.register(_AlwaysFailsHandler())
        delays, sleep_fn = _sleep_recorder()
        executor = RetryExecutor(registry=registry, sleep_fn=sleep_fn)

        step = _step(
            "AlwaysFails",
            retry_policy=RetryPolicy(
                strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
                max_attempts=5,
                base_delay_seconds=1.0,
            ),
        )
        executor.execute_with_retry(step, ExecutionContext())

        assert delays == [1.0, 2.0, 4.0, 8.0]

    def test_exponential_backoff_capped_at_max_delay(self):
        registry = ExecutionRegistry()
        registry.register(_AlwaysFailsHandler())
        delays, sleep_fn = _sleep_recorder()
        executor = RetryExecutor(registry=registry, sleep_fn=sleep_fn)

        step = _step(
            "AlwaysFails",
            retry_policy=RetryPolicy(
                strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
                max_attempts=6,
                base_delay_seconds=1.0,
                max_delay_seconds=5.0,
            ),
        )
        executor.execute_with_retry(step, ExecutionContext())

        assert delays == [1.0, 2.0, 4.0, 5.0, 5.0]

    def test_strategy_none_never_retries(self):
        registry = ExecutionRegistry()
        registry.register(_AlwaysFailsHandler())
        delays, sleep_fn = _sleep_recorder()
        executor = RetryExecutor(registry=registry, sleep_fn=sleep_fn)

        step = _step(
            "AlwaysFails",
            retry_policy=RetryPolicy(strategy=RetryStrategy.NONE, max_attempts=10),
        )
        result = executor.execute_with_retry(step, ExecutionContext())

        assert result.attempts == 1
        assert delays == []


# ═══════════════════════════════════════════════════════════════════════════
# Exhaustion and non-retryable errors
# ═══════════════════════════════════════════════════════════════════════════


class TestRetryExhaustion:
    def test_exhausts_max_attempts_and_stays_failed(self):
        registry = ExecutionRegistry()
        registry.register(_AlwaysFailsHandler())
        executor = RetryExecutor(registry=registry, sleep_fn=lambda d: None)

        step = _step(
            "AlwaysFails",
            retry_policy=RetryPolicy(
                strategy=RetryStrategy.FIXED_DELAY,
                max_attempts=4,
                base_delay_seconds=0.0,
            ),
        )
        result = executor.execute_with_retry(step, ExecutionContext())

        assert result.attempts == 4
        assert result.outcome.success is False

    def test_non_retryable_error_is_never_retried(self):
        handler = _AlwaysFailsHandler()
        handler.retryable = False
        registry = ExecutionRegistry()
        registry.register(handler)
        delays, sleep_fn = _sleep_recorder()
        executor = RetryExecutor(registry=registry, sleep_fn=sleep_fn)

        step = _step(
            "AlwaysFails",
            retry_policy=RetryPolicy(
                strategy=RetryStrategy.FIXED_DELAY,
                max_attempts=5,
                base_delay_seconds=1.0,
            ),
        )
        result = executor.execute_with_retry(step, ExecutionContext())

        assert result.attempts == 1
        assert delays == []


# ═══════════════════════════════════════════════════════════════════════════
# Recovery policies
# ═══════════════════════════════════════════════════════════════════════════


class TestRecoveryPolicies:
    def test_default_recovery_is_continue(self):
        registry = ExecutionRegistry()
        registry.register(_AlwaysFailsHandler())
        executor = RetryExecutor(registry=registry, sleep_fn=lambda d: None)

        result = executor.execute_with_retry(_step("AlwaysFails"), ExecutionContext())

        assert result.recovery_triggered == RecoveryAction.CONTINUE

    def test_abort_is_recorded(self):
        registry = ExecutionRegistry()
        registry.register(_AlwaysFailsHandler())
        executor = RetryExecutor(
            registry=registry,
            sleep_fn=lambda d: None,
            recovery_policies={"AlwaysFails": RecoveryPolicy(action=RecoveryAction.ABORT)},
        )

        result = executor.execute_with_retry(_step("AlwaysFails"), ExecutionContext())

        assert result.recovery_triggered == RecoveryAction.ABORT

    def test_rollback_is_recorded_but_performs_no_state_reversal(self):
        """Explicitly a placeholder per the spec — only the signal fires."""
        registry = ExecutionRegistry()
        registry.register(_AlwaysFailsHandler())
        executor = RetryExecutor(
            registry=registry,
            sleep_fn=lambda d: None,
            recovery_policies={"AlwaysFails": RecoveryPolicy(action=RecoveryAction.ROLLBACK)},
        )

        result = executor.execute_with_retry(_step("AlwaysFails"), ExecutionContext())

        assert result.recovery_triggered == RecoveryAction.ROLLBACK
        assert result.compensation_outcome is None

    def test_compensate_dispatches_the_named_operator(self):
        class RefundHandler:
            operator_name = "Refund"
            capability = ExecutionCapability(name="refund")

            def validate(self, step):
                return None

            def execute(self, step, context):
                return ExecutionOutcome(
                    step_id=step.step_id,
                    status=ExecutionStatus.SUCCEEDED,
                    output={"refunded": True},
                )

        registry = ExecutionRegistry()
        registry.register(_AlwaysFailsHandler())
        registry.register(RefundHandler())
        executor = RetryExecutor(
            registry=registry,
            sleep_fn=lambda d: None,
            recovery_policies={
                "AlwaysFails": RecoveryPolicy(action=RecoveryAction.COMPENSATE, compensation_operator="Refund")
            },
        )

        result = executor.execute_with_retry(_step("AlwaysFails"), ExecutionContext())

        assert result.recovery_triggered == RecoveryAction.COMPENSATE
        assert result.compensation_outcome.success is True
        assert result.compensation_outcome.output == {"refunded": True}
        assert result.outcome.success is False  # the original failure is not overwritten

    def test_policies_are_configured_per_operator_not_globally(self):
        """Two operators, each with its own recovery policy, must not bleed
        into each other — the whole point of 'configurable per operator'."""

        class OtherAlwaysFailsHandler(_AlwaysFailsHandler):
            operator_name = "OtherAlwaysFails"

        registry = ExecutionRegistry()
        registry.register(_AlwaysFailsHandler())
        registry.register(OtherAlwaysFailsHandler())
        executor = RetryExecutor(
            registry=registry,
            sleep_fn=lambda d: None,
            recovery_policies={
                "AlwaysFails": RecoveryPolicy(action=RecoveryAction.ABORT),
                "OtherAlwaysFails": RecoveryPolicy(action=RecoveryAction.ROLLBACK),
            },
            default_recovery=RecoveryPolicy(action=RecoveryAction.CONTINUE),
        )

        first = executor.execute_with_retry(_step("AlwaysFails"), ExecutionContext())
        second = executor.execute_with_retry(_step("OtherAlwaysFails"), ExecutionContext())
        unconfigured = executor.execute_with_retry(
            _step("Op"), ExecutionContext()
        )  # falls back to registry's NO_HANDLER path

        assert first.recovery_triggered == RecoveryAction.ABORT
        assert second.recovery_triggered == RecoveryAction.ROLLBACK
        assert unconfigured.recovery_triggered == RecoveryAction.CONTINUE  # default, since "Op" has no specific policy

    def test_register_recovery_policy_after_construction(self):
        registry = ExecutionRegistry()
        registry.register(_AlwaysFailsHandler())
        executor = RetryExecutor(registry=registry, sleep_fn=lambda d: None)

        executor.register_recovery_policy("AlwaysFails", RecoveryPolicy(action=RecoveryAction.ROLLBACK))
        result = executor.execute_with_retry(_step("AlwaysFails"), ExecutionContext())

        assert result.recovery_triggered == RecoveryAction.ROLLBACK

    def test_step_with_no_operator_uses_default_recovery_without_crashing(self):
        executor = RetryExecutor(sleep_fn=lambda d: None)
        step = ExecutionStep(operator=None)

        result = executor.execute_with_retry(step, ExecutionContext())

        assert result.outcome.status == ExecutionStatus.FAILED
        assert result.recovery_triggered == RecoveryAction.CONTINUE


# ═══════════════════════════════════════════════════════════════════════════
# dispatch() — thin ExecutionRegistry-shaped wrapper
# ═══════════════════════════════════════════════════════════════════════════


class TestDispatchCompatibility:
    def test_dispatch_returns_bare_outcome(self):
        flaky = _FlakyHandler(fail_count=1)
        registry = ExecutionRegistry()
        registry.register(flaky)
        executor = RetryExecutor(registry=registry, sleep_fn=lambda d: None)

        outcome = executor.dispatch(
            _step(
                "FlakyOp",
                retry_policy=RetryPolicy(
                    strategy=RetryStrategy.FIXED_DELAY,
                    max_attempts=3,
                    base_delay_seconds=0.0,
                ),
            )
        )

        assert isinstance(outcome, ExecutionOutcome)
        assert outcome.success is True
        assert outcome.attempt == 2

    def test_composes_into_scheduler_with_zero_scheduler_changes(self):
        """RetryExecutor can be passed anywhere an ExecutionRegistry is
        expected — ExecutionScheduler only ever calls .dispatch(step,
        context), so retry/recovery behavior is available through pure
        composition, no changes to scheduler.py."""
        flaky = _FlakyHandler(fail_count=1)
        registry = ExecutionRegistry()
        registry.register(flaky)
        retry_executor = RetryExecutor(registry=registry, sleep_fn=lambda d: None)

        scheduler = ExecutionScheduler(registry=retry_executor)
        step = _step(
            "FlakyOp",
            retry_policy=RetryPolicy(
                strategy=RetryStrategy.FIXED_DELAY,
                max_attempts=3,
                base_delay_seconds=0.0,
            ),
        )
        plan = ExecutionPlan(steps=(step,))

        schedule, outcomes = scheduler.run(plan, ExecutionContext())

        assert schedule.is_valid
        assert outcomes[0].success is True
        assert flaky.calls == 2  # actually retried, not just passed straight through


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_planning_engine_or_cognitive_runtime_coupling(self):
        import inspect
        import src.monkey_brain.kernel.pipeline.execution_runtime.retry as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "planning_engine",
            "from src.monkey_brain.kernel.pipeline.planner import",
        ]
        for imp in forbidden:
            assert imp not in source, f"retry.py must not import: {imp}"
