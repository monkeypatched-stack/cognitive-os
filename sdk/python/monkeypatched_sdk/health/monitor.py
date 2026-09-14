"""
Health Monitoring
=================

HealthMonitor tracks the health of all registered components
(adapters, connection pools, external dependencies) and exposes
a queryable system-wide health summary.

Status model
------------
HEALTHY    – component is operating normally
DEGRADED   – component is reachable but impaired
UNHEALTHY  – component is unreachable or consistently failing
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth:
    """
    Health record for a single monitored component.

    Call set_healthy(), set_degraded(), or set_unhealthy() after
    each health-check run; read back status, last_check, and
    uptime_percent for dashboards / alerting.
    """

    def __init__(self, component_id: str) -> None:
        self.component_id = component_id
        self.status = HealthStatus.HEALTHY
        self.last_check = datetime.now(timezone.utc)
        self.error_message: Optional[str] = None
        self.check_count: int = 0
        self.failure_count: int = 0

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def set_healthy(self) -> None:
        """Mark component as healthy."""
        self.status = HealthStatus.HEALTHY
        self.error_message = None
        self.last_check = datetime.now(timezone.utc)
        self.check_count += 1

    def set_degraded(self, error: str) -> None:
        """Mark component as degraded with a diagnostic message."""
        self.status = HealthStatus.DEGRADED
        self.error_message = error
        self.last_check = datetime.now(timezone.utc)
        self.check_count += 1
        self.failure_count += 1

    def set_unhealthy(self, error: str) -> None:
        """Mark component as unhealthy with a diagnostic message."""
        self.status = HealthStatus.UNHEALTHY
        self.error_message = error
        self.last_check = datetime.now(timezone.utc)
        self.check_count += 1
        self.failure_count += 1

    # ------------------------------------------------------------------
    # Derived metrics
    # ------------------------------------------------------------------

    def get_uptime_percent(self) -> float:
        """Return the percentage of successful checks (0–100)."""
        if self.check_count == 0:
            return 100.0
        successes = self.check_count - self.failure_count
        return (successes / self.check_count) * 100.0

    def to_dict(self) -> dict:
        """Serialise to a plain dict for HTTP health endpoints."""
        return {
            "component_id": self.component_id,
            "status": self.status.value,
            "last_check": self.last_check.isoformat(),
            "error_message": self.error_message,
            "check_count": self.check_count,
            "failure_count": self.failure_count,
            "uptime_percent": round(self.get_uptime_percent(), 2),
        }


class HealthMonitor:
    """
    System-wide health monitor.

    Register components once; update their status after every
    health-check run; query system health at any time.

    Usage::

        monitor = HealthMonitor()
        monitor.register_component("cmms_adapter")

        # After each health check:
        if healthy:
            monitor.get_component_health("cmms_adapter").set_healthy()
        else:
            monitor.get_component_health("cmms_adapter").set_unhealthy("timeout")

        # Expose via HTTP:
        return monitor.get_system_health()
    """

    def __init__(self) -> None:
        self.components: Dict[str, ComponentHealth] = {}

    def register_component(self, component_id: str) -> ComponentHealth:
        """Register a component and return its health record."""
        if component_id not in self.components:
            self.components[component_id] = ComponentHealth(component_id)
        return self.components[component_id]

    def get_component_health(self, component_id: str) -> Optional[ComponentHealth]:
        """Return the health record for a specific component."""
        return self.components.get(component_id)

    def get_system_health(self) -> dict:
        """
        Return an aggregated health summary.

        Overall status:
        - HEALTHY   if every component is healthy.
        - UNHEALTHY if any component is unhealthy.
        - DEGRADED  otherwise (some degraded, none unhealthy).
        """
        if not self.components:
            return {"status": "unknown", "components": {}}

        statuses = [c.status for c in self.components.values()]

        if all(s == HealthStatus.HEALTHY for s in statuses):
            overall = HealthStatus.HEALTHY
        elif any(s == HealthStatus.UNHEALTHY for s in statuses):
            overall = HealthStatus.UNHEALTHY
        else:
            overall = HealthStatus.DEGRADED

        return {
            "status": overall.value,
            "components": {cid: comp.to_dict() for cid, comp in self.components.items()},
        }

    def is_healthy(self) -> bool:
        """Return True only if every component is HEALTHY."""
        health = self.get_system_health()
        return health.get("status") == HealthStatus.HEALTHY.value
