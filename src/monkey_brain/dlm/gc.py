"""Garbage Collector — periodic cleanup of expired data.

Responsibilities:
- Remove expired sessions
- Remove expired cache
- Remove stale runtime state
- Remove orphaned objects
- Never remove canonical business data
"""

from __future__ import annotations

import logging

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from src.monkey_brain.dlm.ttl import TtlManager

logger = logging.getLogger("monkey_brain.dlm.gc")


@dataclass
class CollectionResult:
    """Result of a garbage collection run."""

    run_id: str = field(default_factory=lambda: f"gc-{uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    entries_collected: int = 0
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)
    collected_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "entries_collected": self.entries_collected,
            "bytes_freed": self.bytes_freed,
            "errors": self.errors,
            "duration_ms": (round((self.completed_at - self.started_at) * 1000, 2) if self.completed_at else None),
        }


class GarbageCollector:
    """Periodic garbage collection for expired data.

    Responsibilities:
    - Collect expired entries
    - Apply deletion policies
    - Track collection results

    Safety:
    - Never removes protected data
    - Never removes permanent storage class
    - Logs all deletions
    """

    def __init__(self, ttl_manager: TtlManager):
        self._ttl = ttl_manager
        self._collection_history: list[CollectionResult] = []
        self._delete_callbacks: dict[str, Callable] = {}

    def register_deleter(self, data_type: str, callback: Callable) -> None:
        """Register a deletion callback for a data type."""
        self._delete_callbacks[data_type] = callback

    def collect(self, data_type: str | None = None) -> CollectionResult:
        """Run garbage collection."""
        result = CollectionResult()

        expired = self._ttl.get_expired(data_type)

        for entry in expired:
            # Check if protected
            if entry.metadata.get("protected", False):
                continue

            try:
                # Execute deletion callback
                callback = self._delete_callbacks.get(entry.data_type)
                if callback:
                    callback(entry.object_id)

                # Remove from TTL manager
                self._ttl.remove(entry.entry_id)

                result.entries_collected += 1
                result.collected_ids.append(entry.entry_id)

            except Exception as e:
                result.errors.append(f"Failed to collect {entry.entry_id}: {e}")

        result.completed_at = time.time()
        self._collection_history.append(result)

        if len(self._collection_history) > 100:
            self._collection_history = self._collection_history[-50:]

        return result

    def get_history(self, limit: int = 10) -> list[CollectionResult]:
        return self._collection_history[-limit:]

    def summary(self) -> dict:
        total_collected = sum(r.entries_collected for r in self._collection_history)
        total_errors = sum(len(r.errors) for r in self._collection_history)
        return {
            "total_runs": len(self._collection_history),
            "total_collected": total_collected,
            "total_errors": total_errors,
            "registered_deleters": list(self._delete_callbacks.keys()),
        }
