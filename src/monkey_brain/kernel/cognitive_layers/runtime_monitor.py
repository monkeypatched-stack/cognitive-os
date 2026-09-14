"""Layer 4: Runtime Monitoring - Single Responsibility.

Responsibility: Health monitoring, logging, and observability.
Depends on: health check interface, event bus
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.compile.solid_interfaces import HealthMonitorInterface

if TYPE_CHECKING:
    pass

logger = logging.getLogger("agentos.cognitive.layers.runtime_monitor")


class RuntimeMonitor(HealthMonitorInterface):
    """Monitor runtime health and emit events.

    Responsibility: Report health status, log events, track execution.
    Does NOT execute logic - only observes and reports.
    """

    def __init__(self, name: str = "CognitiveRuntime") -> None:
        self._name = name
        self._event_bus: Any = None
        self._execution_count = 0
        self._error_count = 0
        self._last_execution_time: float | None = None

    @property
    def name(self) -> str:
        """Component name."""
        return self._name

    @property
    def health(self) -> dict[str, Any]:
        """Get health status.

        Returns:
            Dict with health metrics
        """
        return {
            "name": self._name,
            "status": "healthy" if self._error_count == 0 else "degraded",
            "executions": self._execution_count,
            "errors": self._error_count,
            "error_rate": (self._error_count / self._execution_count if self._execution_count > 0 else 0.0),
            "last_execution_time": self._last_execution_time,
        }

    def set_event_bus(self, event_bus: Any) -> None:
        """Set event bus for publishing events."""
        self._event_bus = event_bus

    async def publish_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish monitoring event."""
        if self._event_bus is not None:
            await self._event_bus.publish(event_type, payload)
            logger.debug("[monitor] Published event: %s", event_type)

    def record_execution(self, run_id: str, success: bool, duration: float) -> None:
        """Record execution completion.

        Args:
            run_id: Execution run ID
            success: Whether execution succeeded
            duration: Execution duration in seconds
        """
        self._execution_count += 1
        if not success:
            self._error_count += 1

        self._last_execution_time = duration

        status = "success" if success else "failure"
        logger.info(
            "[monitor] Execution recorded: run_id=%s, status=%s, duration=%.2fs",
            run_id,
            status,
            duration,
        )

    def record_error(self, run_id: str, error: Exception) -> None:
        """Record execution error.

        Args:
            run_id: Execution run ID
            error: Exception that occurred
        """
        self._error_count += 1
        logger.error(
            "[monitor] Execution error: run_id=%s, error=%s",
            run_id,
            str(error),
        )

    def get_metrics(self) -> dict[str, Any]:
        """Get detailed metrics.

        Returns:
            Dict with execution metrics
        """
        return {
            "total_executions": self._execution_count,
            "total_errors": self._error_count,
            "success_rate": (
                (self._execution_count - self._error_count) / self._execution_count
                if self._execution_count > 0
                else 1.0
            ),
            "error_rate": (self._error_count / self._execution_count if self._execution_count > 0 else 0.0),
            "last_execution_time": self._last_execution_time,
        }

    def reset_metrics(self) -> None:
        """Reset all metrics."""
        self._execution_count = 0
        self._error_count = 0
        self._last_execution_time = None
        logger.info("[monitor] Metrics reset")
