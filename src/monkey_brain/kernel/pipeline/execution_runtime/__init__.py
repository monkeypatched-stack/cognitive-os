"""Formal execution model and (eventually) execution algorithms — Step 9.

Step 9.1: domain.py — the immutable execution domain model.
Step 9.2: handlers.py — ExecutionHandler/ExecutionRegistry + sample handlers.
Step 9.3: scheduler.py — dependency-aware batched scheduling + execution.
Step 9.4: retry.py — retry (per ExecutionStep.retry_policy) and per-operator
recovery policies.
Step 9.5: resolution.py — CapabilityResolver, resolving capabilities/actors/
resources/permissions/capacity before execution starts.
Step 9.6: monitoring.py — ExecutionMonitor (timeline/progress/metrics).
Step 9.7: integration.py — IntegratedExecutionEngine, a drop-in
ExecutionEngine (execution.py Protocol) wiring 9.1-9.6 together, injectable
via CognitiveRuntime(execution_engine=...) with zero changes to any existing
file.
Step 9.8 (this): trace.py — ExecutionTrace + explainability, attached via
IntegratedExecutionEngine.execute_with_trace().

Named execution_runtime/, not execution/, because kernel/pipeline/execution.py
already exists (Action, ActionOutcome, ExecutionResult, ExecutionEngine) —
a package and a module can't share a name in the same directory.
"""

from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
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
from src.monkey_brain.kernel.pipeline.execution_runtime.handlers import (
    ExecutionCapability,
    ExecutionHandler,
    SimulatedHandler,
    NavigateHandler,
    AcquireItemHandler,
    QueryInventoryHandler,
    WaitHandler,
    NotifyHandler,
    ReserveResourceHandler,
    DEFAULT_HANDLERS,
    ExecutionRegistry,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.scheduler import (
    ExecutionMode,
    DependencyViolation,
    ExecutionSchedule,
    ExecutionScheduler,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.retry import (
    RecoveryAction,
    RecoveryPolicy,
    RetryResult,
    RetryExecutor,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.resolution import (
    ExecutionEnvironment,
    ResolutionIssue,
    ResolutionReport,
    CapabilityResolver,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.monitoring import (
    TimelineEventType,
    TimelineEntry,
    ExecutionTimeline,
    ExecutionProgress,
    ExecutionMonitor,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.integration import (
    IntegratedExecutionEngine,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.trace import (
    ExecutionTrace,
    build_execution_trace,
)

__all__ = [
    "ExecutionStatus",
    "RetryStrategy",
    "RetryPolicy",
    "ExecutionStep",
    "ExecutionPlan",
    "ExecutionRequest",
    "ExecutionContext",
    "ExecutionError",
    "ExecutionOutcome",
    "ExecutionMetrics",
    "ExecutionResult",
    "ExecutionCapability",
    "ExecutionHandler",
    "SimulatedHandler",
    "NavigateHandler",
    "AcquireItemHandler",
    "QueryInventoryHandler",
    "WaitHandler",
    "NotifyHandler",
    "ReserveResourceHandler",
    "DEFAULT_HANDLERS",
    "ExecutionRegistry",
    "ExecutionMode",
    "DependencyViolation",
    "ExecutionSchedule",
    "ExecutionScheduler",
    "RecoveryAction",
    "RecoveryPolicy",
    "RetryResult",
    "RetryExecutor",
    "ExecutionEnvironment",
    "ResolutionIssue",
    "ResolutionReport",
    "CapabilityResolver",
    "TimelineEventType",
    "TimelineEntry",
    "ExecutionTimeline",
    "ExecutionProgress",
    "ExecutionMonitor",
    "IntegratedExecutionEngine",
    "ExecutionTrace",
    "build_execution_trace",
]
