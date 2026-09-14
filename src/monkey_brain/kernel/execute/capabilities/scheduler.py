"""Minimal CapabilityScheduler stub for cognitive kernel wiring."""

from __future__ import annotations

from typing import Any


class CapabilityScheduler:
    """Minimal CapabilityScheduler stub for cognitive kernel."""

    def __init__(self, bus: Any):
        self._bus = bus

    def schedule(self, capability_name: str, **kwargs: Any) -> Any:
        """Schedule a capability for execution."""
        # Minimal implementation - just return a placeholder
        return {
            "capability": capability_name,
            "status": "scheduled",
            "bus": str(self._bus),
        }

    def summary(self) -> dict[str, Any]:
        """Get scheduler summary."""
        return {
            "type": "capability_scheduler",
            "bus_attached": self._bus is not None,
        }
