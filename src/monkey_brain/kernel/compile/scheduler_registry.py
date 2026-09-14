"""Scheduler Registry — pluggable scheduler management.

Phase 3.3: Runtime can switch between schedulers without changing caller code.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.monkey_brain.kernel.compile.scheduler_interface import (
    SchedulerInterface,
    ScheduleRequest,
    ScheduleResult,
)

logger = logging.getLogger("agentos.scheduler_registry")


class SchedulerRegistry:
    """Manages pluggable schedulers. Routes requests to active scheduler.

    Supports:
    - Multiple scheduler implementations (distributed, reasoning, graph, process)
    - Runtime switching between schedulers
    - Fallback scheduler if primary unavailable
    """

    def __init__(self, default_scheduler: Optional[SchedulerInterface] = None) -> None:
        self._schedulers: dict[str, SchedulerInterface] = {}
        self._active: Optional[SchedulerInterface] = default_scheduler
        if default_scheduler:
            self._schedulers[default_scheduler.name] = default_scheduler

    def register(self, scheduler: SchedulerInterface) -> None:
        """Register a scheduler implementation."""
        self._schedulers[scheduler.name] = scheduler
        if self._active is None:
            self._active = scheduler
        logger.info("[scheduler_registry] registered scheduler: %s", scheduler.name)

    def unregister(self, name: str) -> None:
        """Unregister a scheduler."""
        if name in self._schedulers:
            del self._schedulers[name]
            if self._active and self._active.name == name:
                self._active = next(iter(self._schedulers.values())) if self._schedulers else None
        logger.info("[scheduler_registry] unregistered scheduler: %s", name)

    def activate(self, name: str) -> bool:
        """Switch to a scheduler."""
        if name not in self._schedulers:
            logger.error("[scheduler_registry] scheduler not found: %s", name)
            return False
        self._active = self._schedulers[name]
        logger.info("[scheduler_registry] activated scheduler: %s", name)
        return True

    def get_active(self) -> Optional[SchedulerInterface]:
        """Get the currently active scheduler."""
        return self._active

    def get(self, name: str) -> Optional[SchedulerInterface]:
        """Get a scheduler by name."""
        return self._schedulers.get(name)

    def list_schedulers(self) -> dict[str, str]:
        """List all registered schedulers with their status."""
        return {
            name: "active" if scheduler == self._active else "inactive" for name, scheduler in self._schedulers.items()
        }

    async def schedule(self, request: ScheduleRequest) -> ScheduleResult:
        """Schedule using active scheduler."""
        if self._active is None:
            logger.error("[scheduler_registry] no active scheduler")
            return ScheduleResult(
                request_id=request.request_id,
                assigned_to="none",
                status="failed",
                message="No active scheduler",
            )

        try:
            return await self._active.schedule(request)
        except Exception as exc:
            logger.error("[scheduler_registry] scheduling failed: %s", exc, exc_info=True)
            return ScheduleResult(
                request_id=request.request_id,
                assigned_to="none",
                status="failed",
                message=str(exc),
            )

    async def poll(self) -> list[ScheduleResult]:
        """Poll active scheduler for ready work."""
        if self._active is None:
            return []

        try:
            return await self._active.poll()
        except Exception as exc:
            logger.error("[scheduler_registry] poll failed: %s", exc)
            return []

    def register_resource(self, resource_id: str, metadata: dict[str, Any]) -> None:
        """Register resource with all active schedulers."""
        if self._active:
            try:
                self._active.register_resource(resource_id, metadata)
            except Exception as exc:
                logger.warning("[scheduler_registry] register_resource failed: %s", exc)

    def unregister_resource(self, resource_id: str) -> None:
        """Unregister resource from all schedulers."""
        if self._active:
            try:
                self._active.unregister_resource(resource_id)
            except Exception as exc:
                logger.warning("[scheduler_registry] unregister_resource failed: %s", exc)

    def update_resource_health(self, resource_id: str, health: dict[str, Any]) -> None:
        """Update resource health in active scheduler."""
        if self._active:
            try:
                self._active.update_resource_health(resource_id, health)
            except Exception as exc:
                logger.warning("[scheduler_registry] update_resource_health failed: %s", exc)

    @property
    def active_scheduler_name(self) -> str:
        """Name of the active scheduler."""
        return self._active.name if self._active else "none"

    @property
    def queue_depth(self) -> int:
        """Total queue depth across all scheduled work."""
        return self._active.queue_depth if self._active else 0


# Singleton instance
_registry: Optional[SchedulerRegistry] = None


def get_scheduler_registry() -> SchedulerRegistry:
    """Get or create singleton scheduler registry."""
    global _registry
    if _registry is None:
        _registry = SchedulerRegistry()
    return _registry


def reset_scheduler_registry() -> None:
    """Reset singleton (for testing)."""
    global _registry
    _registry = None
