"""ContextStream — real-time event stream that updates the Global World ONLY.

The Context Stream is the ONLY mechanism allowed to modify the Global World.

Architectural invariant:
    Context updates the Global World.
    Observation updates the Actor's Belief.

Flow:
    External World → Context Stream → Global World W
    Actor observes W → Observation → Belief B → Bellman → Φ

Actors subscribe to the Context Stream to know WHEN the world changed,
but they do NOT receive world updates directly. Instead, they re-observe
the world on their next cognitive cycle.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable

logger = logging.getLogger("agentos.context_stream")


class EventType(Enum):
    STATE_CHANGE = auto()
    TRANSITION_ADD = auto()
    TRANSITION_REMOVE = auto()
    ENTITY_ADD = auto()
    ENTITY_REMOVE = attribute_update = auto()
    CONSTRAINT_UPDATE = auto()
    TOPOLOGY_CHANGE = auto()


@dataclass
class ContextEvent:
    """A single real-time world change event."""

    timestamp: float
    entity: str
    attribute: str
    previous_value: Any
    current_value: Any
    source: str
    confidence: float = 1.0
    event_type: EventType = EventType.STATE_CHANGE
    metadata: dict = field(default_factory=dict)
    tenant_id: str = "default"  # P0: Multi-tenant isolation


@dataclass
class WorldVersion:
    """Immutable snapshot of world state at a point in time."""

    version: int
    timestamp: float
    transition_count: int
    state_count: int
    checksum: str


class ContextStream:
    """Event-driven stream that updates the Global World.

    The Context Stream:
    - Receives real-time events from sensors, ERPs, user actions, etc.
    - Updates the Global World immutably
    - Publishes change notifications to subscribers
    - Maintains version history for rollback/audit

    Architectural invariant:
        Only the Context Stream may modify the Global World.
        Actors never directly mutate the world.
    """

    def __init__(self, world: Any) -> None:
        self._world = world
        self._version = 0
        self._subscribers: list[Callable[[ContextEvent, WorldVersion], None]] = []
        self._tenant_subscribers: dict[str, list[Callable[[ContextEvent, WorldVersion], None]]] = {}
        self._event_history: list[ContextEvent] = []
        self._version_history: list[WorldVersion] = []
        self._pending_events: list[ContextEvent] = []
        self._max_history = 10000

    @property
    def version(self) -> int:
        return self._version

    def subscribe(
        self,
        callback: Callable[[ContextEvent, WorldVersion], None],
        tenant_id: str | None = None,
    ) -> None:
        """Register a subscriber for world change notifications.

        Args:
            callback: Function to call on world change
            tenant_id: If provided, only receive events for this tenant. If None, receive all.
        """
        if tenant_id is None:
            # Global subscriber receives all events
            self._subscribers.append(callback)
        else:
            # Tenant-scoped subscriber
            if tenant_id not in self._tenant_subscribers:
                self._tenant_subscribers[tenant_id] = []
            self._tenant_subscribers[tenant_id].append(callback)

    def unsubscribe(
        self,
        callback: Callable[[ContextEvent, WorldVersion], None],
        tenant_id: str | None = None,
    ) -> None:
        """Unregister a subscriber."""
        if tenant_id is None:
            self._subscribers = [s for s in self._subscribers if s is not callback]
        elif tenant_id in self._tenant_subscribers:
            self._tenant_subscribers[tenant_id] = [s for s in self._tenant_subscribers[tenant_id] if s is not callback]

    def publish(self, event: ContextEvent) -> WorldVersion:
        """Publish a context event. Updates the world and notifies subscribers.

        P0: Event delivery is tenant-aware. Subscribers only receive events for their tenant.
        """
        self._event_history.append(event)
        self._pending_events.append(event)

        self._apply_to_world(event)
        self._version += 1

        version = WorldVersion(
            version=self._version,
            timestamp=time.time(),
            transition_count=self._world.nnz() if hasattr(self._world, "nnz") else 0,
            state_count=(len(self._world.states()) if hasattr(self._world, "states") else 0),
            checksum=f"v{self._version}",
        )
        self._version_history.append(version)

        # Keep histories bounded
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history :]
        if len(self._version_history) > self._max_history:
            self._version_history = self._version_history[-self._max_history :]

        # P0: Notify global subscribers (receive all events)
        for subscriber in self._subscribers:
            try:
                subscriber(event, version)
            except Exception as e:
                logger.error("[context_stream] subscriber error: %s", e)

        # P0: Notify tenant-scoped subscribers (only their tenant's events)
        if event.tenant_id in self._tenant_subscribers:
            for subscriber in self._tenant_subscribers[event.tenant_id]:
                try:
                    subscriber(event, version)
                except Exception as e:
                    logger.error(
                        "[context_stream] tenant subscriber error (tenant=%s): %s",
                        event.tenant_id,
                        e,
                    )

        logger.debug(
            "[context_stream] v%d: %s.%s = %s (from %s, tenant=%s)",
            self._version,
            event.entity,
            event.attribute,
            event.current_value,
            event.source,
            event.tenant_id,
        )
        return version

    def _apply_to_world(self, event: ContextEvent) -> None:
        """Apply a context event to the Global World."""
        if event.event_type == EventType.TRANSITION_ADD:
            src, dst = event.entity, event.attribute
            domain = event.metadata.get("domain", "default")
            weight = float(event.current_value) if event.current_value is not None else 1.0
            if hasattr(self._world, "observe"):
                self._world.observe(src, dst, domain=domain, weight=weight)

        elif event.event_type == EventType.TRANSITION_REMOVE:
            src, dst = event.entity, event.attribute
            if hasattr(self._world, "remove"):
                self._world.remove(src, dst)

        elif event.event_type == EventType.STATE_CHANGE:
            if hasattr(self._world, "update_entity"):
                self._world.update_entity(event.entity, **{event.attribute: event.current_value})

        elif event.event_type == EventType.ENTITY_ADD:
            from src.monkey_brain.kernel.compile.entity import EntityType

            entity_type = EntityType(event.metadata.get("entity_type", "entity"))
            domain = event.metadata.get("domain", "default")
            if hasattr(self._world, "_add_entity"):
                self._world._add_entity(
                    event.entity,
                    entity_type,
                    domain,
                    **{event.attribute: event.current_value},
                )

        elif event.event_type == EventType.ENTITY_REMOVE:
            if hasattr(self._world, "_entity_meta"):
                i = self._world._tensor._state_index.get(event.entity) if self._world._tensor else None
                if i is not None:
                    self._world._entity_meta.pop(i, None)

        elif event.event_type == EventType.CONSTRAINT_UPDATE:
            pass  # Constraints are immutable after init

        elif event.event_type == EventType.TOPOLOGY_CHANGE:
            pass  # Topology changes are reflected in transition graph

    def get_version(self) -> WorldVersion:
        if self._version_history:
            return self._version_history[-1]
        return WorldVersion(
            version=0,
            timestamp=time.time(),
            transition_count=0,
            state_count=0,
            checksum="v0",
        )

    def recent_events(self, limit: int = 10) -> list[ContextEvent]:
        return self._event_history[-limit:]

    def flush_pending(self) -> list[ContextEvent]:
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """Save context stream state."""
        import json
        import os

        os.makedirs(path, exist_ok=True)
        data = {
            "version": self._version,
            "events": [
                {
                    "timestamp": e.timestamp,
                    "entity": e.entity,
                    "attribute": e.attribute,
                    "previous_value": e.previous_value,
                    "current_value": e.current_value,
                    "source": e.source,
                    "confidence": e.confidence,
                    "event_type": e.event_type.name,
                    "metadata": e.metadata,
                }
                for e in self._event_history[-1000:]
            ],
        }
        with open(os.path.join(path, "context_stream.json"), "w") as f:
            json.dump(data, f, indent=2)

    def load(self, path: str) -> None:
        """Load context stream state."""
        import json
        import os

        stream_path = os.path.join(path, "context_stream.json")
        if not os.path.exists(stream_path):
            return
        with open(stream_path) as f:
            data = json.load(f)
        self._version = data.get("version", 0)

    def replay(self, from_version: int = 0, to_version: int | None = None) -> list[ContextEvent]:
        """Replay events from a version range."""
        from_ts = self._get_version_timestamp(from_version) if from_version > 0 else 0.0
        to_ts = self._get_version_timestamp(to_version) if to_version is not None else time.time()

        return [e for e in self._event_history if from_ts <= e.timestamp <= to_ts]

    def _get_version_timestamp(self, version: int) -> float:
        """Get the timestamp for a version number."""
        if version <= 0:
            return 0.0
        for v in self._version_history:
            if v.version >= version:
                return v.timestamp
        return time.time()

    def rollback_to(self, version: int) -> None:
        """Rollback the world to a specific version."""
        target_timestamp = self._get_version_timestamp(version)
        self._version_history = [v for v in self._version_history if v.version <= version]
        self._version = version
        self._event_history = [e for e in self._event_history if e.timestamp <= target_timestamp]
