"""Distributed Scheduler — multi-node task scheduling with resource awareness.

Routes tasks to the best available runtime based on:
- Capability match (does the runtime have the required capabilities?)
- Resource availability (CPU, memory, queue depth)
- Locality (prefer nearby runtimes)
- Load balancing (distribute across available runtimes)

Gap Remediation audit note: this module's own `DistributedScheduler` class
is NOT the CognitiveOS Actor placement scheduler — that is
`kernel/society/actor_scheduler.py::ActorScheduler`, invoked by the
`ActorLifecycleController` and reachable in production. This module is only
kept alive for its two dataclasses (`RuntimeDescriptor`, `ScheduledTask`),
which `kernel.py` and `kernel/compile/scheduler_adapters.py` still import as
shared metadata types; `DistributedScheduler` itself has no production call
site. Left in place rather than deleted (dataclasses are load-bearing,
deletion risk outweighs the naming-confusion cost) — do not confuse the two
schedulers when reading either file.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

logger = logging.getLogger("agentos.scheduler")


@dataclass
class RuntimeDescriptor:
    """Describes a runtime for scheduling and Kernel registry metadata.

    The Kernel reuses this existing descriptor rather than introducing a
    second runtime metadata type. Scheduler fields remain backward compatible;
    the additional ownership/lifecycle fields are metadata only.
    """

    runtime_id: str
    name: str = ""
    runtime_class: str = ""
    version: str = "1.0.0"
    dependencies: list[str] = field(default_factory=list)
    lifecycle_state: str = "registered"
    health_state: str = "unknown"
    owner: str = "kernel"
    provenance: str = ""
    shutdown_hook: str = "shutdown"
    url: str = ""
    capabilities: list[str] = field(default_factory=list)
    cpu_available: float = 1.0  # 0.0-1.0 fraction
    memory_available: float = 1.0  # 0.0-1.0 fraction
    queue_depth: int = 0
    active_tasks: int = 0
    last_heartbeat: float = 0.0
    status: str = "online"  # online | offline | degraded

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.runtime_id
        if not self.runtime_class:
            self.runtime_class = self.name

    @property
    def id(self) -> str:
        """Canonical Kernel-facing identifier, preserving runtime_id callers."""
        return self.runtime_id

    @property
    def is_available(self) -> bool:
        return self.status == "online" and self.cpu_available > 0.1 and self.memory_available > 0.1


@dataclass
class ScheduledTask:
    """A task scheduled for execution on a specific runtime."""

    task_id: str = field(default_factory=lambda: str(uuid4()))
    task_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    required_capabilities: list[str] = field(default_factory=list)
    priority: int = 0  # higher = more urgent
    assigned_runtime: str = ""
    created_at: float = field(default_factory=time.time)
    scheduled_at: float = 0.0
    status: str = "pending"  # pending | scheduled | running | completed | failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "assigned_runtime": self.assigned_runtime,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at,
        }


class DistributedScheduler:
    """Routes tasks to the best available runtime.

    Maintains a registry of runtime descriptors and makes scheduling
    decisions based on capability match, resource availability, and load.
    """

    def __init__(self) -> None:
        self._runtimes: dict[str, RuntimeDescriptor] = {}
        self._queue: list[ScheduledTask] = []
        self._max_queue: int = 10000

    def register_runtime(self, descriptor: RuntimeDescriptor) -> None:
        """Register or update a runtime's descriptor."""
        self._runtimes[descriptor.runtime_id] = descriptor
        logger.info(
            "[scheduler] registered runtime %s (caps=%d)",
            descriptor.runtime_id,
            len(descriptor.capabilities),
        )

    def unregister_runtime(self, runtime_id: str) -> None:
        self._runtimes.pop(runtime_id, None)

    def heartbeat(
        self,
        runtime_id: str,
        cpu: float = 1.0,
        memory: float = 1.0,
        queue_depth: int = 0,
    ) -> None:
        """Update a runtime's resource state."""
        if runtime_id in self._runtimes:
            rt = self._runtimes[runtime_id]
            rt.cpu_available = cpu
            rt.memory_available = memory
            rt.queue_depth = queue_depth
            rt.last_heartbeat = time.time()
            rt.status = "online"

    def schedule(self, task: ScheduledTask) -> str:
        """Schedule a task to the best available runtime. Returns runtime_id."""
        # Find candidate runtimes that match capabilities
        candidates = []
        for rt in self._runtimes.values():
            if not rt.is_available:
                continue
            if task.required_capabilities:
                if not set(task.required_capabilities).issubset(set(rt.capabilities)):
                    continue
            candidates.append(rt)

        if not candidates:
            # No match — queue for later
            if len(self._queue) < self._max_queue:
                self._queue.append(task)
            return ""

        # Score candidates: higher is better
        scored = []
        for rt in candidates:
            score = (
                rt.cpu_available * 0.3
                + rt.memory_available * 0.3
                + (1.0 / max(rt.queue_depth + 1, 1)) * 0.2
                + (1.0 / max(rt.active_tasks + 1, 1)) * 0.2
            )
            scored.append((score, rt))

        # Pick the best
        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][1]

        task.assigned_runtime = best.runtime_id
        task.scheduled_at = time.time()
        task.status = "scheduled"
        best.queue_depth += 1

        logger.info(
            "[scheduler] scheduled task %s → %s (score=%.3f)",
            task.task_id[:8],
            best.runtime_id,
            scored[0][0],
        )
        return best.runtime_id

    def complete_task(self, task_id: str, runtime_id: str) -> None:
        """Mark a task as completed."""
        if runtime_id in self._runtimes:
            rt = self._runtimes[runtime_id]
            rt.queue_depth = max(0, rt.queue_depth - 1)
            rt.active_tasks = max(0, rt.active_tasks - 1)

    def get_queue(self) -> list[ScheduledTask]:
        return list(self._queue)

    def reschedule_pending(self) -> int:
        """Try to schedule queued tasks. Returns number scheduled."""
        scheduled = 0
        remaining = []
        for task in self._queue:
            runtime_id = self.schedule(task)
            if runtime_id:
                scheduled += 1
            else:
                remaining.append(task)
        self._queue = remaining
        return scheduled

    def runtime_count(self) -> int:
        return len(self._runtimes)

    def available_count(self) -> int:
        return sum(1 for rt in self._runtimes.values() if rt.is_available)
