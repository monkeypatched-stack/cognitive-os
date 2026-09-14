"""Event Sourcing Layer — records all state changes as immutable events.

Every change to the world, actor beliefs, or policies is recorded as an event.
Events are append-only and form a complete audit trail.

Event Types:
  - world.transition    : A transition was observed in the world
  - world.belief        : An actor's belief changed
  - world.policy        : An actor's policy changed
  - world.conflict      : A conflict was detected and resolved
  - world.gossip        : An observation was gossiped between actors

Snapshotting:
  Periodic snapshots capture the current state, enabling fast reconstruction
  without replaying all events from the beginning.

Reconstruction:
  Events can be replayed to reconstruct the state at any point in time.
  This enables:
    - Temporal queries ("what was the state at time t?")
    - Debugging ("what caused this state?")
    - Rollback ("revert to state at time t")
    - Audit ("who changed what, when?")
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger("agentos.event_sourcing")


class EventType(str, Enum):
    """Types of events in the cognitive system."""

    TRANSITION = "world.transition"
    BELIEF = "world.belief"
    POLICY = "world.policy"
    CONFLICT = "world.conflict"
    GOSSIP = "world.gossip"


@dataclass
class Event:
    """An immutable record of a state change."""

    event_id: str = field(default_factory=lambda: str(uuid4()))
    event_type: EventType = EventType.TRANSITION
    timestamp: float = field(default_factory=time.time)
    actor_id: str = ""
    src: str = ""
    dst: str = ""
    domain: str = ""
    feature: str = ""
    old_value: float = 0.0
    new_value: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "actor_id": self.actor_id,
            "src": self.src,
            "dst": self.dst,
            "domain": self.domain,
            "feature": self.feature,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        return cls(
            event_id=d.get("event_id", str(uuid4())),
            event_type=EventType(d.get("event_type", "world.transition")),
            timestamp=d.get("timestamp", time.time()),
            actor_id=d.get("actor_id", ""),
            src=d.get("src", ""),
            dst=d.get("dst", ""),
            domain=d.get("domain", ""),
            feature=d.get("feature", ""),
            old_value=d.get("old_value", 0.0),
            new_value=d.get("new_value", 0.0),
            metadata=d.get("metadata", {}),
        )


@dataclass
class Snapshot:
    """A point-in-time capture of the system state."""

    snapshot_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: float = field(default_factory=time.time)
    event_index: int = 0  # Index of the last event included
    world_state: dict[str, Any] = field(default_factory=dict)
    actor_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "event_index": self.event_index,
            "world_state": self.world_state,
            "actor_states": self.actor_states,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Snapshot":
        return cls(
            snapshot_id=d.get("snapshot_id", str(uuid4())),
            timestamp=d.get("timestamp", time.time()),
            event_index=d.get("event_index", 0),
            world_state=d.get("world_state", {}),
            actor_states=d.get("actor_states", {}),
            metadata=d.get("metadata", {}),
        )


class EventStore:
    """Append-only event store with snapshotting and undo/redo for the cognitive system.

    All state changes are recorded as events.
    Events can be replayed to reconstruct state at any point in time.
    Snapshots capture periodic state for fast reconstruction.
    Undo/redo allows reverting and re-applying events.
    """

    def __init__(self, max_events: int = 1_000_000, persist_path: str | None = None) -> None:
        self._events: list[Event] = []
        self._snapshots: list[Snapshot] = []
        self._undo_stack: list[Event] = []
        self._lock = threading.Lock()
        self._max = max_events
        self._subscribers: list[callable] = []
        self._snapshot_interval: int = 1000  # Snapshot every N events
        self._persist_path: str | None = persist_path
        self._auto_save: bool = persist_path is not None

        # Load from disk if path exists
        if persist_path:
            self._load()

    def append(self, event: Event) -> None:
        """Append an event to the store."""
        with self._lock:
            if len(self._events) >= self._max:
                logger.warning("Event store full — oldest events will be rotated")
                self._events = self._events[-self._max // 2 :]
            self._events.append(event)

            # Notify subscribers (sorted by priority, highest first)
            sorted_subs = sorted(self._subscribers, key=lambda s: -s.get("priority", 0))
            for sub in sorted_subs:
                # Check filters
                if sub.get("event_type") and event.event_type != sub["event_type"]:
                    continue
                if sub.get("actor_id") and event.actor_id != sub["actor_id"]:
                    continue
                try:
                    sub["callback"](event)
                except Exception:
                    logger.debug("append: suppressed exception", exc_info=True)

        # Auto-save outside the lock to avoid deadlock
        if self._auto_save:
            self._save()

    def subscribe(
        self,
        callback: callable,
        event_type: EventType | None = None,
        actor_id: str | None = None,
        priority: int = 0,
    ) -> str:
        """Subscribe to events with optional filters.

        Args:
            callback: Function to call when event matches filters
            event_type: Only receive events of this type (None = all)
            actor_id: Only receive events from this actor (None = all)
            priority: Higher priority subscribers are notified first

        Returns:
            Subscription ID for unsubscribing
        """
        sub_id = str(uuid4())
        subscription = {
            "id": sub_id,
            "callback": callback,
            "event_type": event_type,
            "actor_id": actor_id,
            "priority": priority,
        }
        self._subscribers.append(subscription)
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Remove a subscription by ID."""
        for i, sub in enumerate(self._subscribers):
            if sub.get("id") == sub_id:
                self._subscribers.pop(i)
                return True
        return False

    def query(
        self,
        event_type: EventType | None = None,
        actor_id: str | None = None,
        src: str | None = None,
        dst: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Query events with optional filters."""
        result = self._events

        if event_type:
            result = [e for e in result if e.event_type == event_type]
        if actor_id:
            result = [e for e in result if e.actor_id == actor_id]
        if src:
            result = [e for e in result if e.src == src]
        if dst:
            result = [e for e in result if e.dst == dst]
        if since:
            result = [e for e in result if e.timestamp >= since]

        return result[-limit:]

    def replay(self, since: float = 0.0) -> list[Event]:
        """Replay all events since a given timestamp."""
        return [e for e in self._events if e.timestamp >= since]

    def replay_to_time(self, target_time: float) -> list[Event]:
        """Replay all events up to a given timestamp."""
        return [e for e in self._events if e.timestamp <= target_time]

    def replay_range(self, start_time: float, end_time: float) -> list[Event]:
        """Replay events within a time range."""
        return [e for e in self._events if start_time <= e.timestamp <= end_time]

    def replay_by_actor(self, actor_id: str, since: float = 0.0) -> list[Event]:
        """Replay all events for a specific actor since a timestamp."""
        return [e for e in self._events if e.actor_id == actor_id and e.timestamp >= since]

    def replay_by_type(self, event_type: EventType, since: float = 0.0) -> list[Event]:
        """Replay all events of a specific type since a timestamp."""
        return [e for e in self._events if e.event_type == event_type and e.timestamp >= since]

    def count(self) -> int:
        return len(self._events)

    def last_n(self, n: int) -> list[Event]:
        return self._events[-n:]

    # ── Undo/Redo ────────────────────────────────────────────────────────

    def undo(self) -> Event | None:
        """Undo the last event by recording an inverse event.

        Returns the inverse event if successful, None if no events to undo.
        """
        with self._lock:
            if not self._events:
                return None

            last_event = self._events[-1]

            # Create inverse event
            inverse = Event(
                event_type=last_event.event_type,
                actor_id=last_event.actor_id,
                src=last_event.src,
                dst=last_event.dst,
                domain=last_event.domain,
                feature=last_event.feature,
                old_value=last_event.new_value,  # Swap values
                new_value=last_event.old_value,
                metadata={
                    "action": "undo",
                    "original_event_id": last_event.event_id,
                    "original_timestamp": last_event.timestamp,
                },
            )

            self._events.append(inverse)
            self._undo_stack.append(last_event)

            # Notify subscribers
            for subscriber in self._subscribers:
                try:
                    subscriber(inverse)
                except Exception:
                    logger.debug("undo: suppressed exception", exc_info=True)

            logger.info("[event_sourcing] undone event %s", last_event.event_id)
            return inverse

    def redo(self) -> Event | None:
        """Redo the last undone event by re-applying it.

        Returns the re-applied event if successful, None if no events to redo.
        """
        with self._lock:
            if not self._undo_stack:
                return None

            original = self._undo_stack.pop()

            # Create redo event (same as original but with new timestamp)
            redo_event = Event(
                event_type=original.event_type,
                actor_id=original.actor_id,
                src=original.src,
                dst=original.dst,
                domain=original.domain,
                feature=original.feature,
                old_value=original.old_value,
                new_value=original.new_value,
                metadata={
                    "action": "redo",
                    "original_event_id": original.event_id,
                    "original_timestamp": original.timestamp,
                },
            )

            self._events.append(redo_event)

            # Notify subscribers
            for subscriber in self._subscribers:
                try:
                    subscriber(redo_event)
                except Exception:
                    logger.debug("redo: suppressed exception", exc_info=True)

            logger.info("[event_sourcing] redone event %s", original.event_id)
            return redo_event

    def can_undo(self) -> bool:
        """Check if there are events to undo."""
        with self._lock:
            return len(self._events) > 0

    def can_redo(self) -> bool:
        """Check if there are events to redo."""
        with self._lock:
            return len(self._undo_stack) > 0

    def undo_count(self) -> int:
        """Number of events that can be undone."""
        with self._lock:
            return len(self._events)

    def redo_count(self) -> int:
        """Number of events that can be redone."""
        with self._lock:
            return len(self._undo_stack)

    def clear_undo_stack(self) -> None:
        """Clear the undo stack (irreversible)."""
        with self._lock:
            self._undo_stack.clear()

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load events from disk."""
        if not self._persist_path:
            return
        import json
        from pathlib import Path

        path = Path(self._persist_path)
        if not path.exists():
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._events = [Event.from_dict(e) for e in data.get("events", [])]
                snapshots_data = data.get("snapshots", [])
                if snapshots_data:
                    self._snapshots = [Snapshot.from_dict(s) for s in snapshots_data]
            logger.info(
                "[event_sourcing] loaded %d events, %d snapshots from %s",
                len(self._events),
                len(self._snapshots),
                self._persist_path,
            )
        except Exception as exc:
            logger.warning("[event_sourcing] load failed: %s", exc)

    def _save(self) -> None:
        """Save events to disk (atomic write)."""
        if not self._persist_path:
            return
        import json
        import os
        from pathlib import Path

        path = Path(self._persist_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Snapshot data outside the lock to minimize lock hold time
        with self._lock:
            data = {
                "events": [e.to_dict() for e in self._events],
                "snapshots": [s.to_dict() for s in self._snapshots],
            }

        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, default=str)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)
        except Exception as exc:
            logger.warning("[event_sourcing] save failed: %s", exc)
            if tmp.exists():
                tmp.unlink()

    def save(self) -> None:
        """Manual save to disk."""
        self._save()

    def load(self) -> int:
        """Manual load from disk. Returns number of events loaded."""
        with self._lock:
            old_count = len(self._events)
        self._load()
        with self._lock:
            return len(self._events) - old_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [e.to_dict() for e in self._events],
            "count": len(self._events),
        }

    # ── Snapshotting ──────────────────────────────────────────────────────

    def create_snapshot(
        self,
        world_state: dict[str, Any],
        actor_states: dict[str, dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> Snapshot:
        """Create a point-in-time snapshot of the system state."""
        with self._lock:
            snapshot = Snapshot(
                event_index=len(self._events),
                world_state=world_state,
                actor_states=actor_states,
                metadata=metadata or {},
            )
            self._snapshots.append(snapshot)
            logger.info("[event_sourcing] snapshot created at event %d", snapshot.event_index)

        # Auto-save if persistence is enabled
        if self._auto_save:
            self._save()

        return snapshot

    def get_latest_snapshot(self) -> Snapshot | None:
        """Get the most recent snapshot."""
        with self._lock:
            return self._snapshots[-1] if self._snapshots else None

    def get_snapshot_before(self, timestamp: float) -> Snapshot | None:
        """Get the most recent snapshot before a given timestamp."""
        with self._lock:
            for snapshot in reversed(self._snapshots):
                if snapshot.timestamp <= timestamp:
                    return snapshot
        return None

    def get_snapshots(self, limit: int = 100) -> list[Snapshot]:
        """Get recent snapshots."""
        with self._lock:
            return self._snapshots[-limit:]

    def replay_from_snapshot(self, snapshot: Snapshot) -> list[Event]:
        """Replay events since a snapshot."""
        with self._lock:
            return self._events[snapshot.event_index :]

    def rebuild_from_snapshot(self, snapshot: Snapshot) -> dict[str, Any]:
        """Rebuild state by replaying events from a snapshot.

        Returns the reconstructed state including:
        - world_state from snapshot
        - actor_states from snapshot
        - events since snapshot
        """
        events_since = self.replay_from_snapshot(snapshot)
        return {
            "snapshot": snapshot.to_dict(),
            "events_since": len(events_since),
            "events": [e.to_dict() for e in events_since],
        }

    # ── Event Projection ─────────────────────────────────────────────────

    def project_actor_beliefs(self) -> dict[str, dict[str, Any]]:
        """Project: build read-optimized view of actor beliefs from events.

        Returns {actor_id: {src→dst: value}} for all belief changes.
        """
        beliefs: dict[str, dict[tuple[str, str], float]] = {}
        with self._lock:
            for event in self._events:
                if event.event_type == EventType.BELIEF:
                    actor = event.actor_id
                    if actor not in beliefs:
                        beliefs[actor] = {}
                    beliefs[actor][(event.src, event.dst)] = event.new_value
        # Convert to serializable format
        return {actor: {f"{s}→{d}": v for (s, d), v in edges.items()} for actor, edges in beliefs.items()}

    def project_world_transitions(self) -> dict[str, dict[str, Any]]:
        """Project: build read-optimized view of world transitions.

        Returns {src→dst: {domain, count, last_value}} for all transitions.
        """
        transitions: dict[tuple[str, str], dict[str, Any]] = {}
        with self._lock:
            for event in self._events:
                if event.event_type == EventType.TRANSITION:
                    key = (event.src, event.dst)
                    if key not in transitions:
                        transitions[key] = {
                            "domain": event.domain,
                            "count": 0,
                            "last_value": 0.0,
                        }
                    transitions[key]["count"] += 1
                    transitions[key]["last_value"] = event.new_value
        # Convert to serializable format
        return {f"{s}→{d}": info for (s, d), info in transitions.items()}

    def project_conflicts(self) -> list[dict[str, Any]]:
        """Project: build read-optimized view of conflict resolutions."""
        conflicts = []
        with self._lock:
            for event in self._events:
                if event.event_type == EventType.CONFLICT:
                    conflicts.append(
                        {
                            "src": event.src,
                            "dst": event.dst,
                            "actor_a": event.actor_id,
                            "timestamp": event.timestamp,
                            "old_value": event.old_value,
                            "new_value": event.new_value,
                            "strategy": event.metadata.get("strategy", "unknown"),
                            "severity": event.metadata.get("severity", 0.0),
                        }
                    )
        return conflicts

    def project_statistics(self) -> dict[str, Any]:
        """Project: build event statistics."""
        with self._lock:
            stats = {
                "total_events": len(self._events),
                "total_snapshots": len(self._snapshots),
                "by_type": {},
                "by_actor": {},
                "time_range": {
                    "first": self._events[0].timestamp if self._events else 0,
                    "last": self._events[-1].timestamp if self._events else 0,
                },
            }
            for event in self._events:
                # By type
                event_type = event.event_type.value
                stats["by_type"][event_type] = stats["by_type"].get(event_type, 0) + 1
                # By actor
                if event.actor_id:
                    stats["by_actor"][event.actor_id] = stats["by_actor"].get(event.actor_id, 0) + 1
        return stats


class EventReplayEngine:
    """Reconstructs state by replaying events.

    Supports:
    - Rebuilding world state from events
    - Rebuilding actor beliefs from events
    - Temporal queries ("what was the state at time t?")
    - Event replay for debugging
    """

    def __init__(self, event_store: EventStore) -> None:
        self._store = event_store

    def rebuild_world_state(self, since: float = 0.0) -> dict[str, tuple[str, str, float]]:
        """Rebuild world transitions from events.

        Returns {(src, dst): last_value} for all transitions since timestamp.
        """
        events = self._store.replay(since=since)
        transitions: dict[tuple[str, str], float] = {}
        for event in events:
            if event.event_type == EventType.TRANSITION:
                transitions[(event.src, event.dst)] = event.new_value
        return transitions

    def rebuild_actor_beliefs(self, actor_id: str, since: float = 0.0) -> dict[str, float]:
        """Rebuild an actor's beliefs from events.

        Returns {src→dst: value} for all belief changes since timestamp.
        """
        events = self._store.replay_by_actor(actor_id, since=since)
        beliefs: dict[tuple[str, str], float] = {}
        for event in events:
            if event.event_type == EventType.BELIEF:
                beliefs[(event.src, event.dst)] = event.new_value
        return {f"{s}→{d}": v for (s, d), v in beliefs.items()}

    def rebuild_all_actor_beliefs(self, since: float = 0.0) -> dict[str, dict[str, float]]:
        """Rebuild all actors' beliefs from events.

        Returns {actor_id: {src→dst: value}} for all actors.
        """
        events = self._store.replay(since=since)
        beliefs: dict[str, dict[tuple[str, str], float]] = {}
        for event in events:
            if event.event_type == EventType.BELIEF:
                actor = event.actor_id
                if actor not in beliefs:
                    beliefs[actor] = {}
                beliefs[actor][(event.src, event.dst)] = event.new_value
        # Convert to serializable format
        return {actor: {f"{s}→{d}": v for (s, d), v in edges.items()} for actor, edges in beliefs.items()}

    def rebuild_conflict_history(self, since: float = 0.0) -> list[dict[str, Any]]:
        """Rebuild conflict resolution history from events."""
        events = self._store.replay_by_type(EventType.CONFLICT, since=since)
        return [
            {
                "timestamp": e.timestamp,
                "actor_a": e.actor_id,
                "src": e.src,
                "dst": e.dst,
                "old_value": e.old_value,
                "new_value": e.new_value,
                "strategy": e.metadata.get("strategy", "unknown"),
                "severity": e.metadata.get("severity", 0.0),
            }
            for e in events
        ]

    def temporal_query(self, timestamp: float) -> dict[str, Any]:
        """Get the state of the system at a specific point in time.

        Returns world transitions, actor beliefs, and conflict history
        up to the given timestamp.
        """
        return {
            "timestamp": timestamp,
            "world_transitions": self.rebuild_world_state(since=0.0),
            "actor_beliefs": self.rebuild_all_actor_beliefs(since=0.0),
            "conflict_history": self.rebuild_conflict_history(since=0.0),
            "events_at_time": len(self._store.replay_to_time(timestamp)),
        }

    def replay_events(self, events: list[Event]) -> dict[str, Any]:
        """Replay a list of events and return the resulting state.

        Useful for debugging and testing.
        """
        world: dict[tuple[str, str], float] = {}
        beliefs: dict[str, dict[tuple[str, str], float]] = {}
        conflicts: list[dict] = []

        for event in events:
            if event.event_type == EventType.TRANSITION:
                world[(event.src, event.dst)] = event.new_value
            elif event.event_type == EventType.BELIEF:
                actor = event.actor_id
                if actor not in beliefs:
                    beliefs[actor] = {}
                beliefs[actor][(event.src, event.dst)] = event.new_value
            elif event.event_type == EventType.CONFLICT:
                conflicts.append(
                    {
                        "src": event.src,
                        "dst": event.dst,
                        "strategy": event.metadata.get("strategy", "unknown"),
                    }
                )

        return {
            "world_transitions": len(world),
            "actor_count": len(beliefs),
            "conflict_count": len(conflicts),
            "events_replayed": len(events),
        }


# Global event store instance
_event_store: EventStore | None = None


def get_event_store() -> EventStore:
    """Get the global event store instance."""
    global _event_store
    if _event_store is None:
        _event_store = EventStore()
    return _event_store


class SubscriptionManager:
    """Advanced subscription management for event sourcing.

    Provides:
    - Pattern matching on event properties
    - Subscription groups
    - One-time subscriptions
    - Subscription history
    """

    def __init__(self, event_store: EventStore) -> None:
        self._store = event_store
        self._patterns: dict[str, dict] = {}  # sub_id -> pattern
        self._history: list[dict] = []  # Recent subscription events

    def subscribe_pattern(
        self,
        callback: callable,
        pattern: dict[str, Any],
        priority: int = 0,
    ) -> str:
        """Subscribe with pattern matching.

        Pattern can include:
            event_type: EventType
            actor_id: str
            src: str (exact match)
            dst: str (exact match)
            src_contains: str (substring match)
            dst_contains: str (substring match)
        """
        sub_id = self._store.subscribe(
            callback,
            event_type=pattern.get("event_type"),
            actor_id=pattern.get("actor_id"),
            priority=priority,
        )
        self._patterns[sub_id] = pattern
        self._history.append(
            {
                "action": "subscribe",
                "sub_id": sub_id,
                "pattern": pattern,
                "timestamp": time.time(),
            }
        )
        return sub_id

    def subscribe_one_time(
        self,
        callback: callable,
        event_type: EventType | None = None,
        actor_id: str | None = None,
    ) -> str:
        """Subscribe to receive only the next matching event."""

        def wrapper(event: Event):
            callback(event)
            self._store.unsubscribe(sub_id)

        sub_id = self._store.subscribe(wrapper, event_type=event_type, actor_id=actor_id)
        self._patterns[sub_id] = {"one_time": True}
        self._history.append(
            {
                "action": "subscribe_one_time",
                "sub_id": sub_id,
                "timestamp": time.time(),
            }
        )
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Remove a subscription."""
        result = self._store.unsubscribe(sub_id)
        if result:
            self._patterns.pop(sub_id, None)
            self._history.append(
                {
                    "action": "unsubscribe",
                    "sub_id": sub_id,
                    "timestamp": time.time(),
                }
            )
        return result

    def get_subscription_history(self, limit: int = 100) -> list[dict]:
        """Get recent subscription events."""
        return self._history[-limit:]

    def clear_history(self) -> None:
        """Clear subscription history."""
        self._history.clear()
