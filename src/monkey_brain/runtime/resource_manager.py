"""Resource Manager — tracks and limits resource usage.

Tracks:
- CPU Time
- Memory Usage
- Queue Length
- Thread Utilization
- Process Count
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class ResourceLimits:
    """Configurable resource limits."""

    max_processes: int = 1000
    max_memory_bytes: int = 1024 * 1024 * 1024  # 1GB
    max_cpu_time_ms: float = 60000  # 1 minute
    max_queue_length: int = 10000


@dataclass
class ResourceSnapshot:
    """Point-in-time resource snapshot."""

    snapshot_id: str = field(default_factory=lambda: f"res-{uuid4().hex[:8]}")
    timestamp: float = field(default_factory=time.time)
    process_count: int = 0
    memory_bytes: int = 0
    cpu_time_ms: float = 0.0
    queue_length: int = 0
    thread_utilization: float = 0.0


class ResourceManager:
    """Tracks and limits resource usage.

    Responsibilities:
    - Track resource usage
    - Enforce limits
    - Provide resource snapshots
    """

    def __init__(self, limits: ResourceLimits | None = None):
        self._limits = limits or ResourceLimits()
        self._usage: dict[str, float] = {}
        self._snapshots: list[ResourceSnapshot] = []

    def check_limits(self, process_count: int, memory_bytes: int) -> dict[str, Any]:
        """Check if resource limits are exceeded."""
        violations = []

        if process_count > self._limits.max_processes:
            violations.append(f"Process count {process_count} exceeds limit {self._limits.max_processes}")

        if memory_bytes > self._limits.max_memory_bytes:
            violations.append(f"Memory {memory_bytes} exceeds limit {self._limits.max_memory_bytes}")

        return {
            "allowed": len(violations) == 0,
            "violations": violations,
        }

    def record_usage(self, resource: str, value: float) -> None:
        """Record resource usage."""
        self._usage[resource] = value

    def get_usage(self, resource: str | None = None) -> float | dict:
        if resource:
            return self._usage.get(resource, 0.0)
        return dict(self._usage)

    def snapshot(self, process_count: int = 0, memory_bytes: int = 0, queue_length: int = 0) -> ResourceSnapshot:
        """Take a resource snapshot."""
        snap = ResourceSnapshot(
            process_count=process_count,
            memory_bytes=memory_bytes,
            queue_length=queue_length,
        )
        self._snapshots.append(snap)
        if len(self._snapshots) > 1000:
            self._snapshots = self._snapshots[-500:]
        return snap

    def summary(self) -> dict:
        return {
            "limits": {
                "max_processes": self._limits.max_processes,
                "max_memory_bytes": self._limits.max_memory_bytes,
            },
            "usage": dict(self._usage),
            "snapshots": len(self._snapshots),
        }
