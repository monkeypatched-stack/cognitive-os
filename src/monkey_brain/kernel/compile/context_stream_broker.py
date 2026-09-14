"""Context Stream Broker Integration

Step 13.3 (ACP-1): legacy/test-only. The canonical Context Stream is
kernel/society/context_stream.py::SocietyContextStream, owned by
kernel/society/runtime.py::SocietyRuntime since Step 12.6 — every Commit
stage publishes through that one. Nothing in the production execution path
imports this module; ContextStreamManager here is reachable only from
tests/test_phase9_event_driven_observation.py. Kept for that test coverage.

Updated Society Runtime to use event-driven broker for all world updates.
Replaces direct world mutations with published events.
"""

from typing import Any

from src.actor.event_driven_observer import ContextEvent, ContextStreamBroker, EventType
from src.shared.actor_protocols import ContextStreamBrokerProtocol
from src.monkey_brain.kernel.compile.solid_interfaces import ServiceInterface


class ContextStreamManager(ServiceInterface):
    """Manages Context Stream for entire platform

    Responsibility: Be the ONLY mechanism for world state evolution.
    All changes flow through events.
    """

    def __init__(self):
        self._broker: ContextStreamBrokerProtocol = None  # Injected at runtime
        self._event_version = 1
        self._world = None

    def set_world(self, world: Any) -> None:
        """Set reference to global world (read-only from actors)"""
        self._world = world

    def set_broker(self, broker: ContextStreamBrokerProtocol) -> None:
        """Set the context stream broker (dependency injection)."""
        self._broker = broker

    async def publish_entity_creation(self, entity_id: str, source_actor_id: str, entity_data: dict) -> None:
        """Publish entity creation event

        World mutation flows through the broker, not direct calls.
        """
        event = ContextEvent(
            event_type=EventType.ENTITY_CREATED,
            source_actor_id=source_actor_id,
            affected_entity_id=entity_id,
            data=entity_data,
            version=self._event_version,
        )
        self._event_version += 1

        # Publish to actors — world update handled by subscriber handlers
        await self._broker.publish(event)

    async def publish_entity_update(self, entity_id: str, source_actor_id: str, updates: dict) -> None:
        """Publish entity update event

        World mutation flows through the broker, not direct calls.
        """
        event = ContextEvent(
            event_type=EventType.ENTITY_UPDATED,
            source_actor_id=source_actor_id,
            affected_entity_id=entity_id,
            data={"updates": updates, "new_state": updates},
            version=self._event_version,
        )
        self._event_version += 1

        # Publish to actors — world update handled by subscriber handlers
        await self._broker.publish(event)

    async def publish_action_execution(self, actor_id: str, action: dict, result: dict) -> None:
        """Publish action execution event"""
        event = ContextEvent(
            event_type=EventType.ACTION_EXECUTED,
            source_actor_id=actor_id,
            affected_entity_id=actor_id,
            data={"action": action, "result": result},
            version=self._event_version,
        )
        self._event_version += 1

        # Publish to actors (world updates via transitions)
        await self._broker.publish(event)

    async def publish_world_state_change(self, source_actor_id: str, state_delta: dict) -> None:
        """Publish world state change event

        World mutation flows through the broker, not direct calls.
        """
        event = ContextEvent(
            event_type=EventType.WORLD_STATE_CHANGED,
            source_actor_id=source_actor_id,
            affected_entity_id="world",
            data=state_delta,
            version=self._event_version,
        )
        self._event_version += 1

        # Publish to actors — world update handled by subscriber handlers
        await self._broker.publish(event)

    async def publish_resource_event(self, event_type: EventType, actor_id: str, resource_id: str, data: dict) -> None:
        """Publish resource acquisition/release event"""
        event = ContextEvent(
            event_type=event_type,
            source_actor_id=actor_id,
            affected_entity_id=resource_id,
            data=data,
            version=self._event_version,
        )
        self._event_version += 1

        await self._broker.publish(event)

    def get_broker(self) -> ContextStreamBroker:
        """Get broker for actor subscriptions"""
        return self._broker

    def get_stats(self) -> dict:
        """Get Context Stream statistics"""
        return self._broker.get_stats()

    def get_event_history(self, limit: int = 100) -> list:
        """Get recent event history"""
        return self._broker.get_event_history(limit=limit)
