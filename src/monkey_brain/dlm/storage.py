"""Storage Monitor — tracks storage usage and quotas.

Continuously monitors:
- Database size
- Index size
- Memory growth
- Cache growth
- Log growth
- Event growth

Alerts before storage exhaustion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class StorageQuota:
    """Storage quota for a data type."""

    data_type: str = ""
    max_bytes: int = 1024 * 1024 * 1024  # 1GB default
    max_count: int | None = None
    warning_threshold: float = 0.8  # 80%
    critical_threshold: float = 0.95  # 95%

    def usage_ratio(self, current_bytes: int) -> float:
        return current_bytes / self.max_bytes if self.max_bytes > 0 else 0.0


@dataclass
class StorageSnapshot:
    """Point-in-time storage snapshot."""

    snapshot_id: str = field(default_factory=lambda: f"ss-{uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_bytes: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    quotas: dict[str, dict[str, Any]] = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)


class StorageMonitor:
    """Monitors storage usage and quotas.

    Responsibilities:
    - Track storage usage by type
    - Enforce quotas
    - Generate alerts
    - Provide storage analytics
    """

    def __init__(self):
        self._usage: dict[str, int] = {}
        self._counts: dict[str, int] = {}
        self._quotas: dict[str, StorageQuota] = {}
        self._snapshots: list[StorageSnapshot] = []

    def set_quota(self, data_type: str, quota: StorageQuota) -> None:
        """Set storage quota for a data type."""
        self._quotas[data_type] = quota

    def record_write(self, data_type: str, bytes_written: int) -> None:
        """Record a write operation."""
        self._usage[data_type] = self._usage.get(data_type, 0) + bytes_written
        self._counts[data_type] = self._counts.get(data_type, 0) + 1

    def record_delete(self, data_type: str, bytes_deleted: int) -> None:
        """Record a delete operation."""
        self._usage[data_type] = max(0, self._usage.get(data_type, 0) - bytes_deleted)
        self._counts[data_type] = max(0, self._counts.get(data_type, 0) - 1)

    def get_usage(self, data_type: str | None = None) -> int:
        """Get storage usage."""
        if data_type:
            return self._usage.get(data_type, 0)
        return sum(self._usage.values())

    def get_count(self, data_type: str | None = None) -> int:
        """Get object count."""
        if data_type:
            return self._counts.get(data_type, 0)
        return sum(self._counts.values())

    def check_quotas(self) -> list[dict[str, Any]]:
        """Check all quotas and return alerts."""
        alerts = []
        for data_type, quota in self._quotas.items():
            usage = self._usage.get(data_type, 0)
            ratio = quota.usage_ratio(usage)

            if ratio >= quota.critical_threshold:
                alerts.append(
                    {
                        "type": "critical",
                        "data_type": data_type,
                        "usage_bytes": usage,
                        "quota_bytes": quota.max_bytes,
                        "usage_ratio": round(ratio, 3),
                        "message": f"Storage critical for {data_type}: {ratio * 100:.1f}% used",
                    }
                )
            elif ratio >= quota.warning_threshold:
                alerts.append(
                    {
                        "type": "warning",
                        "data_type": data_type,
                        "usage_bytes": usage,
                        "quota_bytes": quota.max_bytes,
                        "usage_ratio": round(ratio, 3),
                        "message": f"Storage warning for {data_type}: {ratio * 100:.1f}% used",
                    }
                )

        return alerts

    def snapshot(self) -> StorageSnapshot:
        """Take a storage snapshot."""
        snap = StorageSnapshot(
            total_bytes=sum(self._usage.values()),
            by_type=dict(self._usage),
        )

        for data_type, quota in self._quotas.items():
            usage = self._usage.get(data_type, 0)
            snap.quotas[data_type] = {
                "usage_bytes": usage,
                "max_bytes": quota.max_bytes,
                "usage_ratio": round(quota.usage_ratio(usage), 3),
            }

        snap.alerts = [a["message"] for a in self.check_quotas()]

        self._snapshots.append(snap)
        if len(self._snapshots) > 100:
            self._snapshots = self._snapshots[-50:]

        return snap

    def summary(self) -> dict:
        return {
            "total_bytes": sum(self._usage.values()),
            "total_count": sum(self._counts.values()),
            "by_type": {dt: {"bytes": b, "count": self._counts.get(dt, 0)} for dt, b in self._usage.items()},
            "quotas": len(self._quotas),
            "alerts": len(self.check_quotas()),
        }
