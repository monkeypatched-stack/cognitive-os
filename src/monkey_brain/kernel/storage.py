"""Durable Storage — append-only event log with tenant partitioning.

Replaces prototype in-memory storage with durable, append-only,
tenant-partitioned persistence.  Atomic writes, incremental checkpoints,
streaming updates.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger("agentos.storage")


@dataclass
class Event:
    """An immutable event in the append-only log."""

    event_id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = "default"
    event_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class AppendOnlyLog:
    """Thread-safe, append-only event log with tenant partitioning.

    Events are appended atomically.  Reads are consistent snapshots.
    Supports incremental checkpointing.
    """

    def __init__(self, path: str | None = None, max_in_memory: int = 100_000) -> None:
        self._path = Path(path) if path else None
        self._events: dict[str, list[Event]] = {}  # tenant_id -> events
        self._revision: dict[str, int] = {}  # tenant_id -> revision
        self._lock = threading.Lock()
        # In-memory retention was UNBOUNDED: this log backs the audit trail, so every event
        # accumulated forever. Bound it — but only evict what is already durable on disk, and
        # have query() fall back to the log file so audit reads are never silently incomplete.
        self._max_in_memory = max_in_memory
        self._evicted: dict[str, int] = {}  # tenant_id -> events evicted from memory
        if self._path and self._path.exists():
            self._load()

    def append(self, tenant_id: str, event_type: str, payload: dict[str, Any]) -> Event:
        """Append an event to the tenant's log.  Returns the event."""
        with self._lock:
            rev = self._revision.get(tenant_id, 0) + 1
            self._revision[tenant_id] = rev
            event = Event(
                tenant_id=tenant_id,
                event_type=event_type,
                payload=payload,
                revision=rev,
            )
            events = self._events.setdefault(tenant_id, [])
            events.append(event)
            if self._path:
                self._persist_event(tenant_id, event)
                self._maybe_evict(tenant_id, events)  # safe: it's on disk now
            return event

    def _maybe_evict(self, tenant_id: str, events: list[Event]) -> None:
        """Trim the in-memory window. Only ever called when `self._path` is set, i.e. when the
        events being dropped are already persisted — query() re-reads them from disk on demand.
        """
        if self._max_in_memory <= 0 or len(events) <= self._max_in_memory:
            return
        drop = len(events) - (self._max_in_memory // 2)
        del events[:drop]
        self._evicted[tenant_id] = self._evicted.get(tenant_id, 0) + drop
        logger.warning(
            "[storage] tenant %s: evicted %d events from memory (still durable on "
            "disk; queries below revision %d read from the log file)",
            tenant_id,
            drop,
            events[0].revision if events else 0,
        )

    def _read_from_disk(self, tenant_id: str) -> list[Event]:
        """Full event history for a tenant, read from the durable log."""
        if not self._path:
            return []
        log_file = self._path / tenant_id / "events.jsonl"
        if not log_file.exists():
            return []
        out: list[Event] = []
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(Event.from_dict(json.loads(line)))
        return out

    def query(
        self,
        tenant_id: str,
        after_revision: int = 0,
        event_type: str | None = None,
        limit: int = 1000,
    ) -> list[Event]:
        """Query events for a tenant, optionally filtered by type and revision."""
        events = self._events.get(tenant_id, [])
        # If anything was evicted and the caller wants events older than the retained window,
        # serve from the durable log instead of silently returning a truncated audit result.
        if self._path and self._evicted.get(tenant_id, 0):
            oldest = events[0].revision if events else None
            if oldest is None or after_revision + 1 < oldest:
                events = self._read_from_disk(tenant_id)
        result = []
        for e in events:
            if e.revision <= after_revision:
                continue
            if event_type and e.event_type != event_type:
                continue
            result.append(e)
            if len(result) >= limit:
                break
        return result

    def latest_revision(self, tenant_id: str) -> int:
        return self._revision.get(tenant_id, 0)

    def _persist_event(self, tenant_id: str, event: Event) -> None:
        if not self._path:
            return
        tenant_dir = self._path / tenant_id
        tenant_dir.mkdir(parents=True, exist_ok=True)
        log_file = tenant_dir / "events.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(event.to_dict(), default=str) + "\n")

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        for tenant_dir in self._path.iterdir():
            if not tenant_dir.is_dir():
                continue
            log_file = tenant_dir / "events.jsonl"
            if log_file.exists():
                with open(log_file) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            event = Event.from_dict(json.loads(line))
                            self._events.setdefault(event.tenant_id, []).append(event)
                            self._revision[event.tenant_id] = max(
                                self._revision.get(event.tenant_id, 0), event.revision
                            )
                # Boot must not pull an unbounded history into memory either.
                for tid, evs in self._events.items():
                    self._maybe_evict(tid, evs)

    def checkpoint(self, tenant_id: str) -> dict[str, Any]:
        """Snapshot the current state for checkpointing."""
        return {
            "tenant_id": tenant_id,
            "revision": self.latest_revision(tenant_id),
            "event_count": len(self._events.get(tenant_id, [])),
        }
