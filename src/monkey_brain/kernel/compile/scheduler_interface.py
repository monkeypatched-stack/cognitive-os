"""Unified Scheduler Interface — pluggable scheduler abstraction.

Phase 3.3: Consolidate 4 schedulers (DistributedScheduler, ReasoningScheduler,
GraphScheduler, Scheduler) into one flexible interface.

All schedulers implement SchedulerInterface, allowing runtime to switch
between strategies (resource-aware, topology selection, graph traversal, process management)
without changing caller code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass


@dataclass
class ScheduleRequest:
    """Request to schedule work."""

    request_id: str
    request_type: str  # "task" | "reasoning" | "graph" | "process"
    payload: dict[str, Any]
    priority: int = 0
    required_capabilities: list[str] | None = None
    tenant_id: str = "default"


@dataclass
class ScheduleResult:
    """Result of scheduling."""

    request_id: str
    assigned_to: str  # runtime_id | topology | node_id | worker_id
    status: str  # "scheduled" | "pending" | "failed"
    message: str = ""
    metadata: dict[str, Any] | None = None


class SchedulerInterface(ABC):
    """Unified scheduler interface supporting multiple strategies.

    All schedulers implement this interface:
    - DistributedScheduler: route_to(task, capabilities) → runtime_id
    - ReasoningScheduler: select_topology(problem) → reasoning_topology
    - GraphScheduler: walk_graph(graph) → runnable_nodes
    - Scheduler: dequeue_process() → process

    The interface abstracts the scheduling strategy; the scheduler plugin
    determines the actual behavior (resource-aware, topology-based, graph-based, etc.).
    """

    @abstractmethod
    async def schedule(self, request: ScheduleRequest) -> ScheduleResult:
        """Schedule a request and return where it's assigned.

        Args:
            request: ScheduleRequest with work details

        Returns:
            ScheduleResult indicating assignment and status
        """
        pass

    @abstractmethod
    async def poll(self) -> list[ScheduleResult]:
        """Poll for completed/ready work.

        Returns:
            List of ScheduleResults for work that's ready or completed
        """
        pass

    @abstractmethod
    def register_resource(self, resource_id: str, metadata: dict[str, Any]) -> None:
        """Register a resource (runtime, topology, graph node, worker).

        Args:
            resource_id: Unique resource identifier
            metadata: Resource capabilities and constraints
        """
        pass

    @abstractmethod
    def unregister_resource(self, resource_id: str) -> None:
        """Unregister a resource.

        Args:
            resource_id: Resource to remove
        """
        pass

    @abstractmethod
    def update_resource_health(self, resource_id: str, health: dict[str, Any]) -> None:
        """Update resource health status (CPU, memory, queue depth, etc.).

        Args:
            resource_id: Resource identifier
            health: Health metrics (cpu_available, memory_available, queue_depth, etc.)
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Scheduler name/strategy (for logging and monitoring)."""
        pass

    @property
    @abstractmethod
    def queue_depth(self) -> int:
        """Current number of pending requests."""
        pass
