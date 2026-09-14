"""Execution domain model (Step 9.1) — formal, algorithm-independent execution types.

This module defines WHAT an execution request, step, and result are. It does
not decide HOW to schedule, retry, resolve capabilities, or monitor
execution — those are Steps 9.2-9.6. All types are immutable value objects,
mirroring the planning domain model (kernel/pipeline/planning/domain.py).

Relationship to existing types:
    execution.py already has simple Action / ActionOutcome / ExecutionResult
    dataclasses and an ExecutionEngine Protocol, produced today by
    ActionExecutor (action_executor.py) from belief_state.Plan steps. The
    types here are richer, formal versions of the same concepts — ExecutionStep
    is meant to be Action's eventual replacement, ExecutionResult/ExecutionOutcome
    here are richer versions of execution.py's same-named types. They
    intentionally coexist rather than replace anything yet; that integration
    is Step 9.7's job (via ExecutionEngine), not a rewrite of execution.py.
    Because the names genuinely collide (ExecutionResult, ExecutionOutcome
    exist in both places), any call site needing both must alias one on
    import — the same discipline already used for the two CognitiveRuntime
    classes and the two Goal/Plan pairs in kernel/pipeline/planning/.

    contracts.RuntimeContext (booted infra handles) and
    planning.domain.PlanningContext (the planning phase's own working set)
    are different things again from ExecutionContext below — this is the
    execution phase's own scope: what's already completed, what capabilities
    are available, right now, while a plan is being carried out.

ExecutionStep.operator reuses planning.domain.PlanningOperator rather than
reinventing an operator representation — Step 9.2's whole job is "Support
mapping: PlanningOperator -> ExecutionHandler", which presumes execution
steps carry a real PlanningOperator, not just a bare string.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline.planning.domain import PlanningOperator


class ExecutionStatus(Enum):
    """Lifecycle state of one ExecutionStep."""

    PENDING = "pending"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class RetryStrategy(Enum):
    """Which retry algorithm a RetryPolicy names.

    Just the label — Step 9.4 builds the framework that actually interprets
    these (computing delays, deciding when to give up). This module only
    describes what policy a step was configured with.
    """

    NONE = "none"
    FIXED_DELAY = "fixed_delay"
    EXPONENTIAL_BACKOFF = "exponential_backoff"


@dataclass(frozen=True)
class RetryPolicy:
    """How an ExecutionStep should be retried on transient failure."""

    strategy: RetryStrategy = RetryStrategy.NONE
    max_attempts: int = 1
    base_delay_seconds: float = 0.0
    max_delay_seconds: float = 0.0


@dataclass(frozen=True)
class ExecutionStep:
    """One step to execute: an operator, its parameters, and how it relates
    to the other steps in the same plan (dependencies, timeout, retry)."""

    step_id: str = field(default_factory=lambda: uuid4().hex)
    operator: PlanningOperator | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    """step_ids of steps that must complete before this one may start."""
    expected_effects: tuple[str, ...] = ()
    timeout_seconds: float = 30.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    status: ExecutionStatus = ExecutionStatus.PENDING
    trace_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionPlan:
    """An ordered (or dependency-graphed, once Step 9.3's scheduler runs)
    set of ExecutionSteps to carry out. Self-contained — deliberately holds
    no reference back to whatever planning.domain.Plan it was built from;
    that conversion is Step 9.7's job, not this model's."""

    plan_id: str = field(default_factory=lambda: uuid4().hex)
    steps: tuple[ExecutionStep, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionRequest:
    """A request to carry out an ExecutionPlan for a specific actor/tenant."""

    request_id: str = field(default_factory=lambda: uuid4().hex)
    plan: ExecutionPlan = field(default_factory=ExecutionPlan)
    actor_id: str = ""
    tenant_id: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionContext:
    """The execution phase's own working scope: the request being carried
    out, what capabilities are available, and (as steps complete) which
    step_ids have already finished — the information a scheduler and
    capability resolver need that isn't intrinsic to the plan itself.

    Distinct from contracts.RuntimeContext (booted infra handles) and
    planning.domain.PlanningContext (the planning phase's own working set).
    """

    request: ExecutionRequest = field(default_factory=ExecutionRequest)
    available_capabilities: tuple[str, ...] = ()
    completed_step_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionError:
    """What went wrong executing one step."""

    step_id: str = ""
    code: str = ""
    message: str = ""
    retryable: bool = True
    occurred_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ExecutionOutcome:
    """The result of one ExecutionStep's (attempted) execution."""

    step_id: str = ""
    status: ExecutionStatus = ExecutionStatus.PENDING
    output: Any = None
    error: ExecutionError | None = None
    attempt: int = 1
    resource_usage: dict[str, Any] = field(default_factory=dict)
    """Forward-compatible placeholder for Step 9.6's monitoring — this model
    just has somewhere to put it; 9.6 decides what gets recorded here."""
    started_at: float = 0.0
    ended_at: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.ended_at - self.started_at)

    @property
    def success(self) -> bool:
        return self.status == ExecutionStatus.SUCCEEDED


@dataclass(frozen=True)
class ExecutionMetrics:
    """Aggregate statistics over one ExecutionRequest's outcomes."""

    total_steps: int = 0
    succeeded: int = 0
    failed: int = 0
    retried: int = 0
    skipped: int = 0
    total_duration_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.total_steps if self.total_steps else 0.0


@dataclass(frozen=True)
class ExecutionResult:
    """The complete outcome of carrying out one ExecutionRequest."""

    request_id: str = ""
    outcomes: tuple[ExecutionOutcome, ...] = ()
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    goal_achieved: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
