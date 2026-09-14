"""TTL Manager — Time-To-Live management for data objects.

Supports configurable TTL per data class.
Tracks object creation time and expiration.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.dlm.lifecycle import get_policy


@dataclass
class TtlEntry:
    """An entry with TTL tracking."""

    entry_id: str = field(default_factory=lambda: f"ttl-{uuid4().hex[:8]}")
    data_type: str = ""
    object_id: str = ""
    created_at: float = field(default_factory=time.time)
    ttl_seconds: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    @property
    def remaining_seconds(self) -> float | None:
        if self.ttl_seconds is None:
            return None
        remaining = self.ttl_seconds - self.age_seconds
        return max(0.0, remaining)

    @property
    def is_expired(self) -> bool:
        if self.ttl_seconds is None:
            return False
        return self.age_seconds >= self.ttl_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "data_type": self.data_type,
            "object_id": self.object_id,
            "created_at": self.created_at,
            "age_seconds": round(self.age_seconds, 2),
            "ttl_seconds": self.ttl_seconds,
            "remaining_seconds": (round(self.remaining_seconds, 2) if self.remaining_seconds is not None else None),
            "is_expired": self.is_expired,
        }


class TtlManager:
    """Manages TTL for all data objects.

    Responsibilities:
    - Track object creation and expiration
    - Query expired objects
    - Apply TTL policies
    """

    def __init__(self):
        self._entries: dict[str, TtlEntry] = {}
        self._by_type: dict[str, list[str]] = {}

    def register(
        self,
        data_type: str,
        object_id: str,
        ttl_seconds: int | None = None,
        **metadata: Any,
    ) -> TtlEntry:
        """Register an object with TTL."""
        policy = get_policy(data_type)
        effective_ttl = ttl_seconds if ttl_seconds is not None else policy.ttl_seconds

        entry = TtlEntry(
            data_type=data_type,
            object_id=object_id,
            ttl_seconds=effective_ttl,
            metadata=metadata,
        )

        self._entries[entry.entry_id] = entry

        if data_type not in self._by_type:
            self._by_type[data_type] = []
        self._by_type[data_type].append(entry.entry_id)

        return entry

    def is_expired(self, entry_id: str) -> bool:
        """Check if an entry has expired."""
        entry = self._entries.get(entry_id)
        return entry.is_expired if entry else False

    def get_expired(self, data_type: str | None = None) -> list[TtlEntry]:
        """Get all expired entries."""
        expired = []
        for entry in self._entries.values():
            if entry.is_expired:
                if data_type is None or entry.data_type == data_type:
                    expired.append(entry)
        return expired

    def get_by_type(self, data_type: str) -> list[TtlEntry]:
        """Get all entries of a type."""
        entry_ids = self._by_type.get(data_type, [])
        return [self._entries[eid] for eid in entry_ids if eid in self._entries]

    def remove(self, entry_id: str) -> bool:
        """Remove an entry."""
        if entry_id in self._entries:
            entry = self._entries[entry_id]
            self._by_type.get(entry.data_type, []).remove(entry_id)
            del self._entries[entry_id]
            return True
        return False

    def count(self, data_type: str | None = None) -> int:
        """Count entries."""
        if data_type:
            return len(self._by_type.get(data_type, []))
        return len(self._entries)

    def summary(self) -> dict:
        expired_count = sum(1 for e in self._entries.values() if e.is_expired)
        by_type = {dt: len(ids) for dt, ids in self._by_type.items()}
        return {
            "total": len(self._entries),
            "expired": expired_count,
            "active": len(self._entries) - expired_count,
            "by_type": by_type,
        }
