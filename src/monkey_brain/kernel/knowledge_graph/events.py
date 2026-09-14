"""KnowledgeGraph Event Integration — Wires KnowledgeGraph to ContextEventStore.

Provides:
- Emit events on entity/relationship changes
- Subscribe to ContextEventStore for updates
- Sync KnowledgeGraph state with durable storage
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger("agentos.knowledge_graph.events")


class KnowledgeGraphEventEmitter:
    """Emits events to ContextEventStore on KnowledgeGraph changes.

    Integrates KnowledgeGraph with the existing event sourcing system.
    """

    def __init__(self, context_event_store: Any = None):
        self._event_store = context_event_store
        self._callbacks: list[Callable] = []

    def set_event_store(self, store: Any) -> None:
        """Set the ContextEventStore for persistence."""
        self._event_store = store

    def add_callback(self, callback: Callable) -> None:
        """Add a callback for events."""
        self._callbacks.append(callback)

    async def emit_entity_created(self, person_id: str, entity: Any) -> None:
        """Emit event when entity is created."""
        await self._emit(
            person_id,
            "entity_created",
            {
                "entity_id": entity.entity_id,
                "entity_type": (
                    entity.entity_type.value if hasattr(entity.entity_type, "value") else str(entity.entity_type)
                ),
                "name": entity.name,
            },
        )

    async def emit_entity_updated(self, person_id: str, entity: Any, changes: dict[str, Any] | None = None) -> None:
        """Emit event when entity is updated."""
        await self._emit(
            person_id,
            "entity_updated",
            {
                "entity_id": entity.entity_id,
                "entity_type": (
                    entity.entity_type.value if hasattr(entity.entity_type, "value") else str(entity.entity_type)
                ),
                "changes": changes or {},
            },
        )

    async def emit_entity_removed(self, person_id: str, entity_id: str) -> None:
        """Emit event when entity is removed."""
        await self._emit(
            person_id,
            "entity_removed",
            {
                "entity_id": entity_id,
            },
        )

    async def emit_relationship_created(self, person_id: str, relationship: Any) -> None:
        """Emit event when relationship is created."""
        await self._emit(
            person_id,
            "relationship_created",
            {
                "relationship_id": relationship.relationship_id,
                "source_id": relationship.source_id,
                "target_id": relationship.target_id,
                "relationship_type": (
                    relationship.relationship_type.value
                    if hasattr(relationship.relationship_type, "value")
                    else str(relationship.relationship_type)
                ),
            },
        )

    async def emit_relationship_removed(self, person_id: str, relationship_id: str) -> None:
        """Emit event when relationship is removed."""
        await self._emit(
            person_id,
            "relationship_removed",
            {
                "relationship_id": relationship_id,
            },
        )

    async def _emit(self, person_id: str, event_type: str, data: dict[str, Any]) -> None:
        """Emit an event to the ContextEventStore."""
        if self._event_store is None:
            logger.debug("No event store configured, skipping event: %s", event_type)
            return

        try:
            from ..persistence.context_event_store import StoredEvent

            event = StoredEvent(
                event_id=str(uuid4()),
                event_type=event_type,
                actor_id=person_id,
                society_id=f"knowledge_graph:{person_id}",
                description=f"KnowledgeGraph {event_type}",
                payload=data,
                timestamp=time.time(),
            )

            await self._event_store.append(event)
            logger.debug("Emitted event: %s for person %s", event_type, person_id)

        except Exception as e:
            logger.error("Failed to emit event: %s", e)

    async def sync_to_store(self, person_id: str, graph: Any) -> int:
        """Sync entire KnowledgeGraph to ContextEventStore."""
        if self._event_store is None:
            return 0

        count = 0
        for entity in graph._entities.values():
            await self.emit_entity_created(person_id, entity)
            count += 1

        for relationship in graph._relationships.values():
            await self.emit_relationship_created(person_id, relationship)
            count += 1

        logger.info("Synced %d events for person %s", count, person_id)
        return count


class KnowledgeGraphEventSubscriber:
    """Subscribes to ContextEventStore for KnowledgeGraph updates."""

    def __init__(self, context_event_store: Any = None):
        self._event_store = context_event_store
        self._handlers: dict[str, Callable] = {}

    def set_event_store(self, store: Any) -> None:
        """Set the ContextEventStore."""
        self._event_store = store

    def register_handler(self, event_type: str, handler: Callable) -> None:
        """Register a handler for an event type."""
        self._handlers[event_type] = handler

    async def process_events(self, person_id: str, limit: int = 100) -> int:
        """Process pending events for a person."""
        if self._event_store is None:
            return 0

        try:
            events = await self._event_store.query(
                society_id=f"knowledge_graph:{person_id}",
                limit=limit,
            )

            count = 0
            for event in events:
                handler = self._handlers.get(event.event_type)
                if handler:
                    try:
                        await handler(event)
                        count += 1
                    except Exception as e:
                        logger.error("Handler failed for %s: %s", event.event_type, e)

            return count

        except Exception as e:
            logger.error("Failed to process events: %s", e)
            return 0
