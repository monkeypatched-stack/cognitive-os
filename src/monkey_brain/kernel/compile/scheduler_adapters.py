"""Scheduler Adapters — wrap existing schedulers to implement SchedulerInterface.

Phase 3.3: Adapters let each scheduler (Distributed, Reasoning, Graph, Process)
work with the unified interface without changing their implementations.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.compile.scheduler_interface import (
    SchedulerInterface,
    ScheduleRequest,
    ScheduleResult,
)

logger = logging.getLogger("agentos.scheduler_adapters")


class DistributedSchedulerAdapter(SchedulerInterface):
    """Adapter for DistributedScheduler (resource-aware task routing)."""

    def __init__(self, scheduler: Any) -> None:
        self._scheduler = scheduler
        self._completed: dict[str, ScheduleResult] = {}

    async def schedule(self, request: ScheduleRequest) -> ScheduleResult:
        """Schedule task to best available runtime."""
        from src.monkey_brain.kernel.scheduler import ScheduledTask

        task = ScheduledTask(
            task_id=request.request_id,
            task_type=request.request_type,
            payload=request.payload,
            required_capabilities=request.required_capabilities or [],
            priority=request.priority,
        )

        assigned = self._scheduler.schedule(task, tenant_id=request.tenant_id)

        return ScheduleResult(
            request_id=request.request_id,
            assigned_to=assigned.assigned_runtime if assigned else "none",
            status="scheduled" if assigned else "failed",
            message="" if assigned else "No available runtime",
        )

    async def poll(self) -> list[ScheduleResult]:
        """Poll for completed tasks."""
        results = list(self._completed.values())
        self._completed.clear()
        return results

    def register_resource(self, resource_id: str, metadata: dict[str, Any]) -> None:
        """Register a runtime."""
        from src.monkey_brain.kernel.scheduler import RuntimeDescriptor

        descriptor = RuntimeDescriptor(
            runtime_id=resource_id,
            url=metadata.get("url", ""),
            capabilities=metadata.get("capabilities", []),
            cpu_available=metadata.get("cpu_available", 1.0),
            memory_available=metadata.get("memory_available", 1.0),
        )
        self._scheduler.register_runtime(descriptor)

    def unregister_resource(self, resource_id: str) -> None:
        """Unregister a runtime."""
        self._scheduler.unregister_runtime(resource_id)

    def update_resource_health(self, resource_id: str, health: dict[str, Any]) -> None:
        """Update runtime health."""
        self._scheduler.heartbeat(
            resource_id,
            cpu=health.get("cpu_available", 1.0),
            memory=health.get("memory_available", 1.0),
            queue_depth=health.get("queue_depth", 0),
        )

    @property
    def name(self) -> str:
        return "distributed"

    @property
    def queue_depth(self) -> int:
        return len(self._scheduler._queue)


class ReasoningSchedulerAdapter(SchedulerInterface):
    """Adapter for ReasoningScheduler (topology selection)."""

    def __init__(self, scheduler: Any) -> None:
        self._scheduler = scheduler
        self._selected: dict[str, str] = {}

    async def schedule(self, request: ScheduleRequest) -> ScheduleResult:
        """Select reasoning topology for problem."""
        topology = self._scheduler.dispatch(
            problem=request.payload.get("problem", ""),
            strategy=request.payload.get("strategy", "auto"),
        )
        self._selected[request.request_id] = topology or "cot"

        return ScheduleResult(
            request_id=request.request_id,
            assigned_to=topology or "cot",
            status="scheduled",
            metadata={"topology": topology or "cot"},
        )

    async def poll(self) -> list[ScheduleResult]:
        """Poll for completed topology selections."""
        results = []
        for req_id, topology in self._selected.items():
            results.append(
                ScheduleResult(
                    request_id=req_id,
                    assigned_to=topology,
                    status="completed",
                    metadata={"topology": topology},
                )
            )
        self._selected.clear()
        return results

    def register_resource(self, resource_id: str, metadata: dict[str, Any]) -> None:
        """Register a reasoning topology (no-op for reasoning scheduler)."""
        pass

    def unregister_resource(self, resource_id: str) -> None:
        """Unregister a reasoning topology (no-op)."""
        pass

    def update_resource_health(self, resource_id: str, health: dict[str, Any]) -> None:
        """Update topology health (no-op)."""
        pass

    @property
    def name(self) -> str:
        return "reasoning"

    @property
    def queue_depth(self) -> int:
        return len(self._selected)


class GraphSchedulerAdapter(SchedulerInterface):
    """Adapter for GraphScheduler (execution graph walking)."""

    def __init__(self, scheduler: Any) -> None:
        self._scheduler = scheduler
        self._runnable: dict[str, list[str]] = {}

    async def schedule(self, request: ScheduleRequest) -> ScheduleResult:
        """Find runnable nodes in execution graph."""
        graph = request.payload.get("graph")
        if not graph:
            return ScheduleResult(
                request_id=request.request_id,
                assigned_to="none",
                status="failed",
                message="No graph in request",
            )

        runnable = self._scheduler.get_runnable_nodes(graph)
        self._runnable[request.request_id] = [n.node_id for n in runnable] if runnable else []

        return ScheduleResult(
            request_id=request.request_id,
            assigned_to="graph_nodes",
            status="scheduled",
            metadata={"runnable_nodes": len(runnable) if runnable else 0},
        )

    async def poll(self) -> list[ScheduleResult]:
        """Poll for runnable nodes."""
        results = []
        for req_id, nodes in self._runnable.items():
            results.append(
                ScheduleResult(
                    request_id=req_id,
                    assigned_to="graph_nodes",
                    status="completed",
                    metadata={"runnable_nodes": nodes},
                )
            )
        self._runnable.clear()
        return results

    def register_resource(self, resource_id: str, metadata: dict[str, Any]) -> None:
        """Register a graph node (no-op)."""
        pass

    def unregister_resource(self, resource_id: str) -> None:
        """Unregister a graph node (no-op)."""
        pass

    def update_resource_health(self, resource_id: str, health: dict[str, Any]) -> None:
        """Update graph node health (no-op)."""
        pass

    @property
    def name(self) -> str:
        return "graph"

    @property
    def queue_depth(self) -> int:
        return len(self._runnable)


class ProcessSchedulerAdapter(SchedulerInterface):
    """Adapter for Scheduler (process lifecycle management)."""

    def __init__(self, scheduler: Any) -> None:
        self._scheduler = scheduler

    async def schedule(self, request: ScheduleRequest) -> ScheduleResult:
        """Dequeue next ready process."""
        from src.monkey_brain.runtime.process import Process, ProcessPriority

        priority_map = {
            "high": ProcessPriority.HIGH,
            "normal": ProcessPriority.NORMAL,
            "low": ProcessPriority.LOW,
        }

        process = Process(
            process_id=request.request_id,
            priority=priority_map.get(str(request.priority), ProcessPriority.NORMAL),
            entrypoint=request.payload.get("entrypoint", ""),
        )

        self._scheduler.submit(process)
        assigned = self._scheduler.dequeue()

        return ScheduleResult(
            request_id=request.request_id,
            assigned_to=assigned.process_id if assigned else "none",
            status="scheduled" if assigned else "pending",
        )

    async def poll(self) -> list[ScheduleResult]:
        """Poll for ready processes."""
        results = []
        process = self._scheduler.dequeue()
        if process:
            results.append(
                ScheduleResult(
                    request_id=process.process_id,
                    assigned_to="worker",
                    status="ready",
                    metadata={"process_id": process.process_id},
                )
            )
        return results

    def register_resource(self, resource_id: str, metadata: dict[str, Any]) -> None:
        """Register a worker (no-op)."""
        pass

    def unregister_resource(self, resource_id: str) -> None:
        """Unregister a worker (no-op)."""
        pass

    def update_resource_health(self, resource_id: str, health: dict[str, Any]) -> None:
        """Update worker health (no-op)."""
        pass

    @property
    def name(self) -> str:
        return "process"

    @property
    def queue_depth(self) -> int:
        ready_count = len(self._scheduler._ready) if hasattr(self._scheduler, "_ready") else 0
        waiting_count = len(self._scheduler._waiting) if hasattr(self._scheduler, "_waiting") else 0
        return ready_count + waiting_count
