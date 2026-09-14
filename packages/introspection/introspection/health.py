"""Health — health monitoring for all subsystems.

Every subsystem reports health:
- Healthy
- Degraded
- Unavailable
- Unknown

Health checks include dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class HealthCheck:
    """A single health check result."""

    check_id: str = field(default_factory=lambda: f"health-{uuid4().hex[:8]}")
    name: str = ""
    status: str = "healthy"  # healthy | degraded | unhealthy | unknown
    latency_ms: float = 0.0
    message: str = ""
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "name": self.name,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 2),
            "message": self.message,
            "dependencies": self.dependencies,
            "timestamp": self.timestamp,
        }


class HealthMonitor:
    """Health monitoring manager.

    Responsibilities:
    - Run health checks
    - Track health history
    - Aggregate health status
    """

    def __init__(self):
        self._checks: dict[str, HealthCheck] = {}
        self._history: list[HealthCheck] = []

    def check(self, name: str, status: str = "healthy", message: str = "", **metadata: Any) -> HealthCheck:
        """Record a health check."""
        check = HealthCheck(
            name=name,
            status=status,
            message=message,
            metadata=metadata,
        )
        self._checks[name] = check
        self._history.append(check)
        if len(self._history) > 1000:
            self._history = self._history[-500:]
        return check

    def healthy(self, name: str, **metadata: Any) -> HealthCheck:
        return self.check(name, "healthy", **metadata)

    def degraded(self, name: str, message: str = "", **metadata: Any) -> HealthCheck:
        return self.check(name, "degraded", message, **metadata)

    def unhealthy(self, name: str, message: str = "", **metadata: Any) -> HealthCheck:
        return self.check(name, "unhealthy", message, **metadata)

    def get_health(self, name: str) -> HealthCheck | None:
        return self._checks.get(name)

    def overall_status(self) -> str:
        statuses = [c.status for c in self._checks.values()]
        if not statuses:
            return "unknown"
        if "unhealthy" in statuses:
            return "unhealthy"
        if "degraded" in statuses:
            return "degraded"
        if all(s == "healthy" for s in statuses):
            return "healthy"
        return "unknown"

    def summary(self) -> dict:
        return {
            "overall": self.overall_status(),
            "checks": {name: check.status for name, check in self._checks.items()},
            "total_checks": len(self._checks),
        }
