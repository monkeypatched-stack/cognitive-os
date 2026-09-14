"""Retry & recovery policies (Step 9.4) — execution tolerates transient failures.

RetryExecutor wraps ExecutionRegistry.dispatch(): on a retryable failure, it
retries a step per its own RetryPolicy (Step 9.1 — fixed delay or
exponential backoff, up to max_attempts), then applies a RecoveryPolicy once
retries are exhausted (or immediately, for a non-retryable failure).

Recovery policies are configured PER OPERATOR NAME (a registry, like
ExecutionRegistry's own operator_name -> handler mapping), not added as a
field on ExecutionStep — this keeps Step 9.1's domain model untouched and
matches "policies should be configurable per operator."

RetryExecutor also exposes a thin .dispatch(step, context) -> ExecutionOutcome
method with the exact shape ExecutionRegistry.dispatch() has, so it can be
passed directly as ExecutionScheduler(registry=retry_executor) — the
scheduler gets retry/recovery behavior through composition, with no changes
to scheduler.py. (The richer RetryResult — attempt count, which recovery
action fired — is only available via execute_with_retry() directly; there's
no clean existing field on ExecutionOutcome to carry it through the thin
wrapper without overloading resource_usage, which Step 9.1 earmarked for
Step 9.6's monitoring, a different concern.)

Recovery actions:
    ABORT       — signal that execution should stop entirely. This module
                  only produces that signal (RetryResult.recovery_triggered);
                  actually halting an in-progress schedule is an
                  orchestration decision left to Step 9.7's integration.
    CONTINUE    — this step stays FAILED; nothing extra happens. The
                  scheduler's existing dependency-based skip logic (Step 9.3)
                  already handles "don't run downstream of a failure" —
                  CONTINUE is simply the absence of any additional action.
    ROLLBACK    — explicitly a placeholder per the spec. Recorded as the
                  triggered action; no state-reversal is implemented here.
    COMPENSATE  — dispatches a named compensating operator (e.g. "refund"
                  after a failed delivery) through the same registry. The
                  step's own outcome remains a failure; the compensation is
                  a recorded side action, not a retroactive success.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable

from src.monkey_brain.kernel.pipeline.planning.domain import PlanningOperator
from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
    ExecutionContext,
    ExecutionOutcome,
    ExecutionStatus,
    ExecutionStep,
    RetryStrategy,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.handlers import (
    ExecutionRegistry,
)


class RecoveryAction(Enum):
    ABORT = "abort"
    CONTINUE = "continue"
    ROLLBACK = "rollback"
    COMPENSATE = "compensate"


@dataclass(frozen=True)
class RecoveryPolicy:
    """What to do once a step's retries (if any) are exhausted."""

    action: RecoveryAction = RecoveryAction.CONTINUE
    compensation_operator: str = ""
    """Operator name to dispatch if action == COMPENSATE."""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RetryResult:
    """The final outcome of attempting a step with its retry policy applied."""

    outcome: ExecutionOutcome = field(default_factory=ExecutionOutcome)
    attempts: int = 1
    recovery_triggered: RecoveryAction | None = None
    compensation_outcome: ExecutionOutcome | None = None


class RetryExecutor:
    """Adds retry (per ExecutionStep.retry_policy) and recovery (per a
    per-operator RecoveryPolicy) around ExecutionRegistry dispatch."""

    def __init__(
        self,
        registry: ExecutionRegistry | None = None,
        recovery_policies: dict[str, RecoveryPolicy] | None = None,
        default_recovery: RecoveryPolicy | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._registry = registry or ExecutionRegistry()
        self._recovery_policies: dict[str, RecoveryPolicy] = (
            dict(recovery_policies) if recovery_policies is not None else {}
        )
        self._default_recovery = default_recovery or RecoveryPolicy(action=RecoveryAction.CONTINUE)
        self._sleep = sleep_fn

    def register_recovery_policy(self, operator_name: str, policy: RecoveryPolicy) -> None:
        self._recovery_policies[operator_name] = policy

    # ── Rich API ─────────────────────────────────────────────────────────

    def execute_with_retry(self, step: ExecutionStep, context: ExecutionContext) -> RetryResult:
        policy = step.retry_policy
        attempt = 1
        outcome = self._registry.dispatch(step, context)

        while not outcome.success and self._should_retry(outcome, policy, attempt):
            delay = self._compute_delay(policy, attempt)
            if delay > 0:
                self._sleep(delay)
            attempt += 1
            retrying_step = replace(step, status=ExecutionStatus.RETRYING)
            outcome = self._registry.dispatch(retrying_step, context)
            outcome = replace(outcome, attempt=attempt)

        if outcome.success:
            return RetryResult(outcome=outcome, attempts=attempt)

        recovery = self._recovery_for(step)
        compensation_outcome = None
        if recovery.action == RecoveryAction.COMPENSATE and recovery.compensation_operator:
            compensation_outcome = self._compensate(step, recovery, context)

        return RetryResult(
            outcome=outcome,
            attempts=attempt,
            recovery_triggered=recovery.action,
            compensation_outcome=compensation_outcome,
        )

    # ── Thin ExecutionRegistry-shaped API — for drop-in scheduler composition ──

    def dispatch(self, step: ExecutionStep, context: ExecutionContext | None = None) -> ExecutionOutcome:
        return self.execute_with_retry(step, context or ExecutionContext()).outcome

    # ── Retry decision ───────────────────────────────────────────────────

    def _should_retry(self, outcome: ExecutionOutcome, policy, attempt: int) -> bool:
        if policy.strategy == RetryStrategy.NONE:
            return False
        if attempt >= policy.max_attempts:
            return False
        if outcome.error is not None and not outcome.error.retryable:
            return False
        return True

    def _compute_delay(self, policy, attempt: int) -> float:
        if policy.strategy == RetryStrategy.FIXED_DELAY:
            delay = policy.base_delay_seconds
        elif policy.strategy == RetryStrategy.EXPONENTIAL_BACKOFF:
            delay = policy.base_delay_seconds * (2 ** (attempt - 1))
        else:
            delay = 0.0
        if policy.max_delay_seconds > 0:
            delay = min(delay, policy.max_delay_seconds)
        return delay

    # ── Recovery ─────────────────────────────────────────────────────────

    def _recovery_for(self, step: ExecutionStep) -> RecoveryPolicy:
        operator_name = step.operator.name if step.operator is not None else None
        if operator_name is None:
            return self._default_recovery
        return self._recovery_policies.get(operator_name, self._default_recovery)

    def _compensate(self, step: ExecutionStep, recovery: RecoveryPolicy, context: ExecutionContext) -> ExecutionOutcome:
        compensating_step = ExecutionStep(
            operator=PlanningOperator(name=recovery.compensation_operator),
            parameters=step.parameters,
            trace_metadata={
                **step.trace_metadata,
                "compensating_for_step_id": step.step_id,
            },
        )
        return self._registry.dispatch(compensating_step, context)
