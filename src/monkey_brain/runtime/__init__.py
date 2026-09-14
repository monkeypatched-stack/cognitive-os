"""MonkeyBrain Runtime (Wolverine).

The Runtime executes workloads.

Responsibilities:
- Workload Execution
- Process Management
- Thread Pool
- Memory
- Streaming
- Scheduling
- Supervision
- Resource Management

The Runtime never performs planning.
The Runtime never performs learning.
The Runtime never owns execution policy.
"""

from __future__ import annotations

# Lazy imports to avoid circular dependencies with kernel
# Runtime, StepResult, ExecutionResult are imported on demand
from src.monkey_brain.runtime.engine import Engine, EngineConfig
from src.monkey_brain.runtime.process import (
    Process,
    ProcessState,
    ProcessPriority,
    Message,
)
from src.monkey_brain.runtime.scheduler import Scheduler, SchedulerPolicy
from src.monkey_brain.runtime.worker_pool import WorkerPool, Worker
from src.monkey_brain.runtime.message_bus import MessageBus
from src.monkey_brain.runtime.supervisor import (
    Supervisor,
    SupervisorConfig,
    RestartStrategy,
)
from src.monkey_brain.runtime.resource_manager import ResourceManager, ResourceLimits


def get_runtime():
    """Lazy import of Runtime to avoid circular dependencies."""
    from src.monkey_brain.runtime.runtime import Runtime

    return Runtime


__all__ = [
    "get_runtime",
    "Engine",
    "EngineConfig",
    "Process",
    "ProcessState",
    "ProcessPriority",
    "Message",
    "Scheduler",
    "SchedulerPolicy",
    "WorkerPool",
    "Worker",
    "MessageBus",
    "Supervisor",
    "SupervisorConfig",
    "RestartStrategy",
    "ResourceManager",
    "ResourceLimits",
]
