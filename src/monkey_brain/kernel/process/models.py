"""Core data structures for the Process Manager.

Naming note: this package deliberately avoids the bare names `Process` /
`ProcessState` / `ResourceLimits` — those already exist in
`src/monkey_brain/runtime/process.py` and `runtime/resource_manager.py` as a
distinct, lower-level actor-model abstraction (Wolverine's process/scheduler
layer, message-passing, no relation to ExecutionGraph/Workload/IntentIR).
Reusing those names here would recreate exactly the kind of duplicate-class
naming collision this codebase has been cleaning up. Every type below is
prefixed accordingly (`RuntimeProcess*`, `ProcessResourceLimits`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal
from uuid import uuid4

from src.monkey_brain.kernel.execute.context import ExecutionContext
from src.monkey_brain.kernel.execute.graph import ExecutionGraph


class RuntimeProcessState(str, Enum):
    """Process-level lifecycle state — layered ABOVE ExecutionGraph's
    per-node NodeState, not a replacement for it. See kernel/process/manager.py
    module docstring for the full state machine and valid transitions.
    """

    CREATED = "created"
    INITIALIZED = "initialized"
    RUNNING = "running"
    WAITING = "waiting"
    SUSPENDED = "suspended"
    CHECKPOINTED = "checkpointed"
    RESUMING = "resuming"
    COMPENSATING = "compensating"
    ROLLED_BACK = "rolled_back"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


TERMINAL_STATES = frozenset(
    {
        RuntimeProcessState.ROLLED_BACK,
        RuntimeProcessState.COMPLETED,
        RuntimeProcessState.FAILED,
        RuntimeProcessState.TERMINATED,
    }
)

# Explicit valid-transition table — enforced by ProcessManager._transition().
# Anything not listed here is invalid and rejected, including all of the
# specifically-forbidden shortcuts called out in the design spec (e.g.
# CHECKPOINTED -> RUNNING must pass through RESUMING).
VALID_TRANSITIONS: dict[RuntimeProcessState, frozenset[RuntimeProcessState]] = {
    RuntimeProcessState.CREATED: frozenset({RuntimeProcessState.INITIALIZED, RuntimeProcessState.TERMINATED}),
    RuntimeProcessState.INITIALIZED: frozenset({RuntimeProcessState.RUNNING, RuntimeProcessState.TERMINATED}),
    RuntimeProcessState.RUNNING: frozenset(
        {
            RuntimeProcessState.WAITING,
            RuntimeProcessState.SUSPENDED,
            RuntimeProcessState.COMPLETED,
            RuntimeProcessState.COMPENSATING,
            RuntimeProcessState.FAILED,
            RuntimeProcessState.TERMINATED,
        }
    ),
    RuntimeProcessState.WAITING: frozenset(
        {
            RuntimeProcessState.RUNNING,
            RuntimeProcessState.SUSPENDED,
            RuntimeProcessState.COMPENSATING,  # e.g. an approval gate rejected while WAITING on it
            RuntimeProcessState.TERMINATED,
        }
    ),
    RuntimeProcessState.SUSPENDED: frozenset(
        {
            RuntimeProcessState.CHECKPOINTED,
            RuntimeProcessState.RUNNING,
            RuntimeProcessState.TERMINATED,
        }
    ),
    RuntimeProcessState.CHECKPOINTED: frozenset({RuntimeProcessState.RESUMING, RuntimeProcessState.TERMINATED}),
    RuntimeProcessState.RESUMING: frozenset({RuntimeProcessState.RUNNING, RuntimeProcessState.FAILED}),
    RuntimeProcessState.COMPENSATING: frozenset({RuntimeProcessState.ROLLED_BACK, RuntimeProcessState.FAILED}),
    RuntimeProcessState.ROLLED_BACK: frozenset(),
    RuntimeProcessState.COMPLETED: frozenset(),
    RuntimeProcessState.FAILED: frozenset(),
    RuntimeProcessState.TERMINATED: frozenset(),
}


class InvalidTransitionError(Exception):
    def __init__(self, run_id: str, current: RuntimeProcessState, target: RuntimeProcessState) -> None:
        super().__init__(f"run={run_id!r}: invalid transition {current.value} -> {target.value}")
        self.run_id = run_id
        self.current = current
        self.target = target


class ProcessNotFoundError(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"no process found for run_id={run_id!r}")
        self.run_id = run_id


@dataclass
class ApprovalGateState:
    node_id: str
    prompt: str
    requested_at: float = field(default_factory=time.time)
    approver: str | None = None
    decision: Literal["approve", "reject"] | None = None
    note: str = ""
    decided_at: float | None = None


@dataclass
class CompensationRecord:
    node_id: str
    capability_name: str
    compensating_capability: str | None
    success: bool
    detail: str = ""
    compensated_at: float = field(default_factory=time.time)


@dataclass
class ProcessError:
    message: str
    node_id: str | None = None
    occurred_at: float = field(default_factory=time.time)


@dataclass
class ProcessResourceLimits:
    """Per-RuntimeProcess resource limits enforced by the Process Manager's
    macro-scheduler. Distinct from runtime.resource_manager.ResourceLimits,
    which governs the lower-level actor/worker pool (different layer,
    different unit of measurement — that one is process-pool-wide, this one
    is one Runtime Process's own budget).
    """

    max_concurrent_capability_calls: int = 4
    timeout_seconds: float | None = None


@dataclass
class CheckpointRef:
    """Lightweight pointer to a stored Checkpoint — kept on the RPCB so the
    full checkpoint payload doesn't have to stay resident in memory.
    """

    checkpoint_id: str
    run_id: str
    created_at: float = field(default_factory=time.time)


@dataclass
class RuntimeProcessControlBlock:
    """The OS-process-table-entry analog — one per in-flight Runtime Process.

    `context` and `graph` are references to state genuinely owned elsewhere
    (ExecutionContext is immutable and owned by whichever runtime built it;
    ExecutionGraph is the existing single source of truth for node state) —
    the RPCB indexes them for lifecycle management, it does not duplicate them.
    """

    run_id: str
    context: ExecutionContext
    graph: ExecutionGraph
    target: Literal["execute", "simulate", "compare", "codegen"] = "execute"
    state: RuntimeProcessState = RuntimeProcessState.CREATED
    parent_run_id: str | None = None
    priority: int = 0
    created_at: float = field(default_factory=time.time)
    checkpoints: list[CheckpointRef] = field(default_factory=list)
    compensation_log: list[CompensationRecord] = field(default_factory=list)
    approval_gate: ApprovalGateState | None = None
    retry_budget: dict[str, int] = field(default_factory=dict)
    timeout_at: float | None = None
    resource_limits: ProcessResourceLimits | None = None
    error: ProcessError | None = None
    replayed_from: str | None = None

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


def new_run_id() -> str:
    return f"proc-{uuid4().hex[:16]}"
