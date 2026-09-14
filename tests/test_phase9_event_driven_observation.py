"""Phase 9: Event-Driven Observation - Integration Tests

Tests verify that actors subscribe to events and update beliefs reactively
instead of polling world state.
"""

import pytest
import asyncio
from datetime import datetime

from src.actor.event_driven_observer import (
    ContextEvent,
    EventType,
    EventFilter,
    EventSubscription,
    ContextStreamBroker,
    EventDrivenActor,
    ReactiveAutonomousActor,
)
from src.monkey_brain.kernel.compile.context_stream_broker import ContextStreamManager
from src.monkey_brain.kernel.compile.entity import Person


class TestContextStreamBroker:
    """Test Context Stream broker for event dispatch"""

    def test_create_broker(self):
        """Context Stream broker can be created"""
        broker = ContextStreamBroker()
        assert broker is not None
        stats = broker.get_stats()
        assert stats["total_events_published"] == 0

    def test_create_event(self):
        """Context events can be created"""
        event = ContextEvent(
            event_type=EventType.ENTITY_CREATED,
            source_actor_id="actor1",
            affected_entity_id="entity1",
            data={"name": "Test"},
        )

        assert event.event_type == EventType.ENTITY_CREATED
        assert event.source_actor_id == "actor1"

    def test_event_filter_match(self):
        """Event filter matching works"""
        event = ContextEvent(
            event_type=EventType.ACTION_EXECUTED,
            source_actor_id="actor1",
            affected_entity_id="entity1",
        )

        # Filter by event type
        filter1 = EventFilter(event_types={EventType.ACTION_EXECUTED})
        assert event.matches_filter(filter1)

        # Filter by source actor
        filter2 = EventFilter(source_actors={"actor1"})
        assert event.matches_filter(filter2)

        # Filter by affected entity
        filter3 = EventFilter(affected_entities={"entity1"})
        assert event.matches_filter(filter3)

        # Non-matching filter
        filter4 = EventFilter(event_types={EventType.ENTITY_CREATED})
        assert not event.matches_filter(filter4)

    @pytest.mark.asyncio
    async def test_subscribe_to_events(self):
        """Actor can subscribe to events"""
        broker = ContextStreamBroker()
        received_events = []

        async def callback(event):
            received_events.append(event)

        event_filter = EventFilter(event_types={EventType.ACTION_EXECUTED})
        subscription = broker.subscribe(actor_id="actor1", event_filter=event_filter, callback=callback)

        assert subscription is not None

    @pytest.mark.asyncio
    async def test_publish_and_receive_event(self):
        """Published events are delivered to subscribers"""
        broker = ContextStreamBroker()
        received_events = []

        async def callback(event):
            received_events.append(event)

        event_filter = EventFilter(event_types={EventType.ACTION_EXECUTED})
        broker.subscribe("actor1", event_filter, callback)

        # Publish event
        event = ContextEvent(
            event_type=EventType.ACTION_EXECUTED,
            source_actor_id="actor2",
            affected_entity_id="entity1",
            data={"action": "move"},
        )
        await broker.publish(event)

        await asyncio.sleep(0.1)  # Let callback execute

        assert len(received_events) == 1
        assert received_events[0].event_type == EventType.ACTION_EXECUTED

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        """Multiple actors can subscribe to same events"""
        broker = ContextStreamBroker()
        actor1_events = []
        actor2_events = []

        async def callback1(event):
            actor1_events.append(event)

        async def callback2(event):
            actor2_events.append(event)

        event_filter = EventFilter(event_types={EventType.ENTITY_UPDATED})
        broker.subscribe("actor1", event_filter, callback1)
        broker.subscribe("actor2", event_filter, callback2)

        # Publish event
        event = ContextEvent(
            event_type=EventType.ENTITY_UPDATED,
            source_actor_id="actor3",
            affected_entity_id="entity1",
        )
        await broker.publish(event)

        await asyncio.sleep(0.1)

        assert len(actor1_events) == 1
        assert len(actor2_events) == 1

    @pytest.mark.asyncio
    async def test_subscribe_by_source_actor(self):
        """Subscribe to events from specific actor"""
        broker = ContextStreamBroker()
        received_events = []

        async def callback(event):
            received_events.append(event)

        # Subscribe only to actor2's events
        event_filter = EventFilter(source_actors={"actor2"})
        broker.subscribe("actor1", event_filter, callback)

        # Publish from actor2
        event1 = ContextEvent(
            event_type=EventType.ACTION_EXECUTED,
            source_actor_id="actor2",
            affected_entity_id="entity1",
        )
        await broker.publish(event1)

        # Publish from actor3 (should not receive)
        event2 = ContextEvent(
            event_type=EventType.ACTION_EXECUTED,
            source_actor_id="actor3",
            affected_entity_id="entity1",
        )
        await broker.publish(event2)

        await asyncio.sleep(0.1)

        assert len(received_events) == 1
        assert received_events[0].source_actor_id == "actor2"

    @pytest.mark.asyncio
    async def test_subscribe_by_entity(self):
        """Subscribe to events affecting specific entity"""
        broker = ContextStreamBroker()
        received_events = []

        async def callback(event):
            received_events.append(event)

        # Subscribe to entity1 changes only
        event_filter = EventFilter(affected_entities={"entity1"})
        broker.subscribe("actor1", event_filter, callback)

        # Event for entity1
        event1 = ContextEvent(
            event_type=EventType.ENTITY_UPDATED,
            source_actor_id="actor2",
            affected_entity_id="entity1",
        )
        await broker.publish(event1)

        # Event for entity2
        event2 = ContextEvent(
            event_type=EventType.ENTITY_UPDATED,
            source_actor_id="actor2",
            affected_entity_id="entity2",
        )
        await broker.publish(event2)

        await asyncio.sleep(0.1)

        assert len(received_events) == 1
        assert received_events[0].affected_entity_id == "entity1"

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        """Actor can unsubscribe from events"""
        broker = ContextStreamBroker()
        received_events = []

        async def callback(event):
            received_events.append(event)

        event_filter = EventFilter(event_types={EventType.ACTION_EXECUTED})
        subscription = broker.subscribe("actor1", event_filter, callback)

        # Unsubscribe
        broker.unsubscribe("actor1", subscription)

        # Publish event (should not be received)
        event = ContextEvent(
            event_type=EventType.ACTION_EXECUTED,
            source_actor_id="actor2",
            affected_entity_id="entity1",
        )
        await broker.publish(event)

        await asyncio.sleep(0.1)

        assert len(received_events) == 0

    @pytest.mark.asyncio
    async def test_batch_publish(self):
        """Batch publish is more efficient"""
        broker = ContextStreamBroker()
        received_events = []

        async def callback(event):
            received_events.append(event)

        event_filter = EventFilter(event_types={EventType.ACTION_EXECUTED})
        broker.subscribe("actor1", event_filter, callback)

        # Batch publish
        events = [ContextEvent(EventType.ACTION_EXECUTED, "actor2", f"entity{i}") for i in range(5)]
        await broker.publish_batch(events)

        await asyncio.sleep(0.1)

        assert len(received_events) == 5

    def test_event_history(self):
        """Event history is tracked"""
        broker = ContextStreamBroker()

        # Publish events (without async)
        for i in range(5):
            broker._event_history.append(
                ContextEvent(
                    event_type=EventType.ACTION_EXECUTED,
                    source_actor_id="actor1",
                    affected_entity_id=f"entity{i}",
                )
            )
            broker._event_count += 1

        history = broker.get_event_history(limit=3)
        assert len(history) == 3
        assert history[-1].affected_entity_id == "entity4"

    def test_broker_stats(self):
        """Broker provides statistics"""
        broker = ContextStreamBroker()
        broker._event_count = 10
        broker._event_history = [None] * 10

        stats = broker.get_stats()
        assert stats["total_events_published"] == 10
        assert stats["event_history_size"] == 10


class TestEventDrivenActor:
    """Test event-driven observation in actors"""

    def test_create_event_driven_actor(self):
        """Event-driven actor can be created"""
        actor = EventDrivenActor()
        assert actor is not None
        assert actor._subscriptions == []

    @pytest.mark.asyncio
    async def test_set_broker(self):
        """Actor can be linked to broker"""
        actor = EventDrivenActor()
        actor.id = "actor1"
        broker = ContextStreamBroker()

        actor.set_event_broker(broker)
        assert actor._event_broker is broker

    @pytest.mark.asyncio
    async def test_subscribe_to_events(self):
        """Actor subscribes to events"""
        actor = EventDrivenActor()
        actor.id = "actor1"
        broker = ContextStreamBroker()

        actor.set_event_broker(broker)

        event_filter = EventFilter(event_types={EventType.ACTION_EXECUTED})
        subscription = await actor.subscribe_to_events(event_filter)

        assert subscription in actor._subscriptions

    @pytest.mark.asyncio
    async def test_subscribe_to_entity_updates(self):
        """Actor can subscribe to specific entity updates"""
        actor = EventDrivenActor()
        actor.id = "actor1"
        broker = ContextStreamBroker()

        actor.set_event_broker(broker)
        subscription = await actor.subscribe_to_entity_updates("entity1")

        assert subscription is not None

    @pytest.mark.asyncio
    async def test_subscribe_to_actor_actions(self):
        """Actor can subscribe to another actor's actions"""
        actor = EventDrivenActor()
        actor.id = "actor1"
        broker = ContextStreamBroker()

        actor.set_event_broker(broker)
        subscription = await actor.subscribe_to_actor_actions("actor2")

        assert subscription is not None

    @pytest.mark.asyncio
    async def test_process_pending_observations(self):
        """Actor processes queued events"""
        actor = EventDrivenActor()
        actor.id = "actor1"

        # Queue some observations
        event1 = ContextEvent(EventType.ACTION_EXECUTED, "actor2", "entity1")
        event2 = ContextEvent(EventType.ENTITY_UPDATED, "actor2", "entity2")

        await actor._pending_observations.put(event1)
        await actor._pending_observations.put(event2)

        observations = await actor.process_pending_observations()

        assert len(observations) == 2
        assert observations[0].event_type == EventType.ACTION_EXECUTED
        assert observations[1].event_type == EventType.ENTITY_UPDATED

    @pytest.mark.asyncio
    async def test_unsubscribe_all(self):
        """Actor can unsubscribe from all events"""
        actor = EventDrivenActor()
        actor.id = "actor1"
        broker = ContextStreamBroker()

        actor.set_event_broker(broker)

        event_filter = EventFilter(event_types={EventType.ACTION_EXECUTED})
        await actor.subscribe_to_events(event_filter)
        assert len(actor._subscriptions) == 1

        actor.unsubscribe_all()
        assert len(actor._subscriptions) == 0


class TestReactiveAutonomousActor:
    """Test Phase 8 + Phase 9: Autonomous + Event-Driven"""

    def test_create_reactive_actor(self):
        """Reactive autonomous actor can be created"""
        actor = ReactiveAutonomousActor()
        assert actor is not None

    @pytest.mark.asyncio
    async def test_observe_from_events(self):
        """Observe stage processes events"""
        actor = ReactiveAutonomousActor()
        actor.id = "actor1"

        # Queue an event
        event = ContextEvent(EventType.ACTION_EXECUTED, "actor2", "entity1", data={"action": "move"})
        await actor._pending_observations.put(event)

        # Observe processes the event
        observations = await actor.observe()

        assert observations["event_count"] == 1
        assert len(observations["events"]) == 1

    @pytest.mark.asyncio
    async def test_believe_from_events(self):
        """Believe stage updates from events"""
        actor = ReactiveAutonomousActor()
        actor.id = "actor1"

        events = [
            ContextEvent(
                EventType.ENTITY_UPDATED,
                "actor2",
                "entity1",
                data={"new_state": {"status": "active"}},
            )
        ]

        # Mock belief model
        class MockBelief:
            def incorporate_observation(self, obs):
                pass

        actor.beliefs = MockBelief()

        updated = actor.believe_from_events(events)
        # Mock always returns False, but no exception means success
        assert isinstance(updated, bool)


class TestContextStreamManager:
    """Test Context Stream manager"""

    def test_create_manager(self):
        """Context Stream manager can be created"""
        manager = ContextStreamManager()
        assert manager is not None

    @pytest.mark.asyncio
    async def test_publish_action(self):
        """Can publish action execution event"""
        manager = ContextStreamManager()
        received = []

        async def callback(event):
            received.append(event)

        broker = manager.get_broker()
        event_filter = EventFilter(event_types={EventType.ACTION_EXECUTED})
        broker.subscribe("actor1", event_filter, callback)

        await manager.publish_action_execution(actor_id="actor2", action={"type": "move"}, result={"success": True})

        await asyncio.sleep(0.1)

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_publish_entity_update(self):
        """Can publish entity update event"""
        manager = ContextStreamManager()
        received = []

        async def callback(event):
            received.append(event)

        broker = manager.get_broker()
        event_filter = EventFilter(event_types={EventType.ENTITY_UPDATED})
        broker.subscribe("actor1", event_filter, callback)

        await manager.publish_entity_update(entity_id="entity1", source_actor_id="actor2", updates={"status": "active"})

        await asyncio.sleep(0.1)

        assert len(received) == 1

    def test_context_stream_stats(self):
        """Manager provides stats"""
        manager = ContextStreamManager()
        stats = manager.get_stats()

        assert "total_events_published" in stats
        assert "active_actors" in stats


class TestPhase9Integration:
    """Integration tests for Phase 9"""

    @pytest.mark.asyncio
    async def test_reactive_observation_cycle(self):
        """Complete reactive observation cycle"""
        # Setup
        broker = ContextStreamBroker()

        actor1 = ReactiveAutonomousActor()
        actor1.id = "actor1"
        actor1.set_event_broker(broker)

        actor2 = ReactiveAutonomousActor()
        actor2.id = "actor2"
        actor2.set_event_broker(broker)

        # Actor 1 subscribes to Actor 2's actions
        event_filter = EventFilter(source_actors={"actor2"})
        await actor1.subscribe_to_events(event_filter)

        # Actor 2 publishes action
        event = ContextEvent(
            event_type=EventType.ACTION_EXECUTED,
            source_actor_id="actor2",
            affected_entity_id="entity1",
            data={"action": "move"},
        )
        await broker.publish(event)

        await asyncio.sleep(0.1)

        # Actor 1 should have received event in observations
        observations = await actor1.observe()
        assert observations["event_count"] == 1

    @pytest.mark.asyncio
    async def test_multi_actor_event_flow(self):
        """Events flow between multiple actors"""
        manager = ContextStreamManager()
        broker = manager.get_broker()

        actors = []
        for i in range(3):
            actor = ReactiveAutonomousActor()
            actor.id = f"actor{i}"
            actor.set_event_broker(broker)
            actors.append(actor)

        # Each actor subscribes to all action events
        for actor in actors:
            await actor.subscribe_to_event_type(EventType.ACTION_EXECUTED)

        # Actor 0 publishes action
        event = ContextEvent(
            event_type=EventType.ACTION_EXECUTED,
            source_actor_id="actor0",
            affected_entity_id="entity1",
        )
        await broker.publish(event)

        await asyncio.sleep(0.1)

        # All actors should have received event
        for i, actor in enumerate(actors):
            observations = await actor.observe()
            assert observations["event_count"] == 1
