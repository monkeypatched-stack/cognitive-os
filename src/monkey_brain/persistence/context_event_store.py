"""Context Event Store — durable persistence for society context events.

Provides:
- Append-only event storage with Redis backend
- Query by event type, actor, time range
- Replay from any point in time
- Integration with SocietyContextStream

Architecture:
    SocietyContextStream (in-memory)
        ↓ on_publish callback
    ContextEventStore (durable)
        ↓
    Redis (persistence)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class StoredEvent:
    """A persisted context event with metadata."""

    event_id: str = field(default_factory=lambda: uuid4().hex)
    event_type: str = ""
    actor_id: str = ""
    society_id: str = ""
    description: str = ""
    payload: Any = None
    confidence: float = 1.0
    provenance: str = ""
    timestamp: float = field(default_factory=time.time)
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "society_id": self.society_id,
            "description": self.description,
            "payload": self.payload,
            "confidence": self.confidence,
            "provenance": self.provenance,
            "timestamp": self.timestamp,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StoredEvent:
        return cls(
            event_id=d.get("event_id", uuid4().hex),
            event_type=d.get("event_type", ""),
            actor_id=d.get("actor_id", ""),
            society_id=d.get("society_id", ""),
            description=d.get("description", ""),
            payload=d.get("payload"),
            confidence=d.get("confidence", 1.0),
            provenance=d.get("provenance", ""),
            timestamp=d.get("timestamp", 0.0),
            version=d.get("version", 0),
        )


class ContextEventStore:
    """Durable event store for society context events.

    Responsibilities:
    - Append events to Redis with tenant partitioning
    - Query events by type, actor, society, time range
    - Replay events from any point in time
    - Integrate with SocietyContextStream via on_publish callback
    """

    _KEY_PREFIX = "monkeybrain:context_events"
    _INDEX_KEY = "monkeybrain:context_event_index"

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
        self._max_events_per_society = 100000
        self._buffer: list[StoredEvent] = []
        self._buffer_size = 100
        self._flush_interval = 5.0  # seconds
        self._last_flush = time.time()

    def set_redis(self, redis_client: Any) -> None:
        """Set Redis client for persistence."""
        self._redis = redis_client

    async def append(self, event: StoredEvent) -> None:
        """Append an event to the store."""
        self._buffer.append(event)

        # Flush if buffer is full or interval elapsed
        if len(self._buffer) >= self._buffer_size:
            await self._flush()
        elif time.time() - self._last_flush > self._flush_interval:
            await self._flush()

    async def _flush(self) -> None:
        """Flush buffered events to Redis."""
        if not self._redis or not self._buffer:
            return

        try:
            pipe = self._redis.pipeline()

            for event in self._buffer:
                # Store event in society-specific list
                society_key = f"{self._KEY_PREFIX}:{event.society_id}"
                pipe.rpush(society_key, json.dumps(event.to_dict()))

                # Trim to max size
                pipe.ltrim(society_key, -self._max_events_per_society, -1)

                # Update index for time-based queries
                pipe.zadd(self._INDEX_KEY, {event.event_id: event.timestamp})

            pipe.execute()
            flushed_count = len(self._buffer)
            self._buffer.clear()
            self._last_flush = time.time()
            logger.debug("Flushed %d events to Redis", flushed_count)
        except Exception as e:
            logger.error("Failed to flush events to Redis: %s", e)

    async def query(
        self,
        society_id: str | None = None,
        event_type: str | None = None,
        actor_id: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[StoredEvent]:
        """Query events with optional filters."""
        if not self._redis:
            return []

        try:
            if society_id:
                # Query specific society
                key = f"{self._KEY_PREFIX}:{society_id}"
                raw_events = self._redis.lrange(key, -limit, -1)
            else:
                # Query all societies (less efficient)
                raw_events = []
                for key in self._redis.scan_iter(f"{self._KEY_PREFIX}:*"):
                    events = self._redis.lrange(key, -limit, -1)
                    raw_events.extend(events)

            # Parse and filter
            events = []
            for raw in raw_events:
                event = StoredEvent.from_dict(json.loads(raw))

                if event_type and event.event_type != event_type:
                    continue
                if actor_id and event.actor_id != actor_id:
                    continue
                if since and event.timestamp < since:
                    continue

                events.append(event)

            # Sort by timestamp and limit
            events.sort(key=lambda e: e.timestamp, reverse=True)
            return events[:limit]

        except Exception as e:
            logger.error("Failed to query events: %s", e)
            return []

    async def replay(
        self,
        society_id: str,
        since: float | None = None,
        limit: int = 1000,
    ) -> list[StoredEvent]:
        """Replay events from a point in time."""
        return await self.query(society_id=society_id, since=since, limit=limit)

    async def count(self, society_id: str | None = None) -> int:
        """Count events, optionally filtered by society."""
        if not self._redis:
            return 0

        try:
            if society_id:
                key = f"{self._KEY_PREFIX}:{society_id}"
                return self._redis.llen(key)
            else:
                # Count all events across all societies
                total = 0
                for key in self._redis.scan_iter(f"{self._KEY_PREFIX}:*"):
                    total += self._redis.llen(key)
                return total
        except Exception as e:
            logger.error("Failed to count events: %s", e)
            return 0

    async def clear(self, society_id: str | None = None) -> None:
        """Clear events, optionally filtered by society."""
        if not self._redis:
            return

        try:
            if society_id:
                key = f"{self._KEY_PREFIX}:{society_id}"
                self._redis.delete(key)
            else:
                # Clear all events
                for key in self._redis.scan_iter(f"{self._KEY_PREFIX}:*"):
                    self._redis.delete(key)
                self._redis.delete(self._INDEX_KEY)
        except Exception as e:
            logger.error("Failed to clear events: %s", e)

    def create_on_publish_callback(self, society_id: str) -> Callable[[], None]:
        """Create an on_publish callback for a SocietyContextStream."""

        def callback():
            # This will be called by SocietyContextStream after each publish
            # We'll handle the actual persistence in the async append method
            pass

        return callback


# Singleton
_event_store: ContextEventStore | None = None


def get_event_store() -> ContextEventStore:
    """Get or create the singleton ContextEventStore."""
    global _event_store
    if _event_store is None:
        _event_store = ContextEventStore()
    return _event_store
