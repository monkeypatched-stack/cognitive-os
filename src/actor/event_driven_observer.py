"""Phase 9: Event-Driven Observation - Reactive Belief Updates

Step 13.3 (ACP-1): legacy/test-only. Superseded by
kernel/society/context_stream.py::SocietyContextStream (the canonical Context
Stream since Step 12.6). No production execution path imports this module —
reachable only from tests/test_phase9_event_driven_observation.py via
kernel/compile/context_stream_broker.py. Kept for that test coverage.

Actors subscribe to Context Stream events and update beliefs automatically.
No more polling world state - observations push updates to actors.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Set, Any, Optional
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    """Context Stream event types"""

    ENTITY_CREATED = "entity_created"
    ENTITY_UPDATED = "entity_updated"
    ENTITY_DELETED = "entity_deleted"
    ACTION_EXECUTED = "action_executed"
    BELIEF_UPDATED = "belief_updated"
    WORLD_STATE_CHANGED = "world_state_changed"
    AGENT_JOINED = "agent_joined"
    AGENT_LEFT = "agent_left"
    RESOURCE_ACQUIRED = "resource_acquired"
    RESOURCE_RELEASED = "resource_released"


@dataclass
class ContextEvent:
    """Event emitted to Context Stream"""

    event_type: EventType
    source_actor_id: str
    affected_entity_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    version: int = 1

    def matches_filter(self, event_filter: "EventFilter") -> bool:
        """Check if event matches subscription filter"""
        if event_filter.event_types and self.event_type not in event_filter.event_types:
            return False

        if event_filter.source_actors and self.source_actor_id not in event_filter.source_actors:
            return False

        if event_filter.affected_entities and self.affected_entity_id not in event_filter.affected_entities:
            return False

        if event_filter.predicate and not event_filter.predicate(self):
            return False

        return True


@dataclass
class EventFilter:
    """Filter for event subscriptions"""

    event_types: Optional[Set[EventType]] = None
    source_actors: Optional[Set[str]] = None
    affected_entities: Optional[Set[str]] = None
    predicate: Optional[Callable[[ContextEvent], bool]] = None


class EventSubscription:
    """Subscription to Context Stream events"""

    def __init__(
        self,
        actor_id: str,
        event_filter: EventFilter,
        callback: Callable[[ContextEvent], None],
        auto_believe: bool = True,
    ):
        self.actor_id = actor_id
        self.event_filter = event_filter
        self.callback = callback
        self.auto_believe = auto_believe
        self.created_at = datetime.now()
        self.event_count = 0
        self.last_event: Optional[ContextEvent] = None

    async def handle_event(self, event: ContextEvent) -> None:
        """Process incoming event"""
        if not event.matches_filter(self.event_filter):
            return

        self.event_count += 1
        self.last_event = event

        try:
            await self.callback(event)
        except Exception as e:
            print(f"Subscription callback error for {self.actor_id}: {e}")


class ContextStreamBroker:
    """Manages event subscriptions and dispatch

    Central broker for event-driven observation.
    Replaces polling-based world access.
    """

    def __init__(self):
        self._subscriptions: Dict[str, List[EventSubscription]] = {}
        self._event_history: List[ContextEvent] = []
        self._max_history = 10000
        self._event_count = 0
        self._subscribers_by_type: Dict[EventType, List[EventSubscription]] = {}

    def subscribe(
        self,
        actor_id: str,
        event_filter: EventFilter,
        callback: Callable[[ContextEvent], None],
        auto_believe: bool = True,
    ) -> EventSubscription:
        """Subscribe actor to Context Stream events

        Args:
            actor_id: ID of subscribing actor
            event_filter: Filter for relevant events
            callback: Async callback for each event
            auto_believe: Automatically update beliefs from events

        Returns:
            Subscription object (can be used to unsubscribe)
        """
        subscription = EventSubscription(
            actor_id=actor_id,
            event_filter=event_filter,
            callback=callback,
            auto_believe=auto_believe,
        )

        if actor_id not in self._subscriptions:
            self._subscriptions[actor_id] = []
        self._subscriptions[actor_id].append(subscription)

        # Index by event type for efficient dispatch
        if event_filter.event_types:
            for event_type in event_filter.event_types:
                if event_type not in self._subscribers_by_type:
                    self._subscribers_by_type[event_type] = []
                self._subscribers_by_type[event_type].append(subscription)

        return subscription

    def unsubscribe(self, actor_id: str, subscription: EventSubscription) -> None:
        """Unsubscribe actor from events"""
        if actor_id in self._subscriptions:
            self._subscriptions[actor_id].remove(subscription)

    async def publish(self, event: ContextEvent) -> None:
        """Publish event to all subscribed actors

        Only mechanism for world state evolution.
        """
        self._event_count += 1
        self._event_history.append(event)

        # Trim history if too large
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history :]

        # Dispatch to subscribed actors
        relevant_subscriptions = []

        # Get subscriptions by event type (fast path)
        if event.event_type in self._subscribers_by_type:
            relevant_subscriptions.extend(self._subscribers_by_type[event.event_type])

        # Also check subscriptions without type filter
        for actor_id, subscriptions in self._subscriptions.items():
            for subscription in subscriptions:
                if not subscription.event_filter.event_types:  # No type filter
                    relevant_subscriptions.append(subscription)

        # Dedup and process
        seen = set()
        tasks = []
        for sub in relevant_subscriptions:
            sub_id = (sub.actor_id, id(sub))
            if sub_id not in seen:
                seen.add(sub_id)
                tasks.append(sub.handle_event(event))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def publish_batch(self, events: List[ContextEvent]) -> None:
        """Publish multiple events (more efficient)"""
        tasks = [self.publish(event) for event in events]
        await asyncio.gather(*tasks, return_exceptions=True)

    def get_subscription_stats(self, actor_id: str) -> Optional[Dict[str, Any]]:
        """Get stats for actor's subscriptions"""
        if actor_id not in self._subscriptions:
            return None

        subscriptions = self._subscriptions[actor_id]
        stats = {
            "actor_id": actor_id,
            "subscription_count": len(subscriptions),
            "total_events_received": sum(s.event_count for s in subscriptions),
            "subscriptions": [],
        }

        for sub in subscriptions:
            stats["subscriptions"].append(
                {
                    "event_types": (list(sub.event_filter.event_types) if sub.event_filter.event_types else None),
                    "event_count": sub.event_count,
                    "last_event": sub.last_event.timestamp if sub.last_event else None,
                }
            )

        return stats

    def get_event_history(
        self,
        event_type: Optional[EventType] = None,
        actor_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[ContextEvent]:
        """Retrieve event history with filters"""
        results = []

        for event in reversed(self._event_history):
            if event_type and event.event_type != event_type:
                continue
            if actor_id and event.source_actor_id != actor_id:
                continue
            results.append(event)
            if len(results) >= limit:
                break

        return list(reversed(results))

    def get_stats(self) -> Dict[str, Any]:
        """Get broker statistics"""
        return {
            "total_events_published": self._event_count,
            "event_history_size": len(self._event_history),
            "active_actors": len(self._subscriptions),
            "total_subscriptions": sum(len(subs) for subs in self._subscriptions.values()),
        }


class EventDrivenActor:
    """Mixin for actors using event-driven observation

    Combines AutonomousActor capabilities with reactive subscriptions.
    """

    def __init__(self):
        self._event_broker: Optional[ContextStreamBroker] = None
        self._subscriptions: List[EventSubscription] = []
        self._pending_observations: asyncio.Queue = asyncio.Queue()

    def set_event_broker(self, broker: ContextStreamBroker) -> None:
        """Link to Context Stream broker"""
        self._event_broker = broker

    async def subscribe_to_events(
        self,
        event_filter: EventFilter,
        callback: Optional[Callable[[ContextEvent], None]] = None,
    ) -> EventSubscription:
        """Subscribe to Context Stream events

        Replaces polling-based observe() calls.
        Events automatically update beliefs.
        """
        if not self._event_broker:
            raise RuntimeError(f"Actor {self.id} not linked to event broker")

        if callback is None:
            callback = self._default_event_handler

        subscription = self._event_broker.subscribe(
            actor_id=self.id,
            event_filter=event_filter,
            callback=callback,
            auto_believe=True,
        )

        self._subscriptions.append(subscription)
        return subscription

    async def subscribe_to_entity_updates(self, entity_id: str) -> EventSubscription:
        """Subscribe to updates for specific entity"""
        event_filter = EventFilter(
            event_types={
                EventType.ENTITY_CREATED,
                EventType.ENTITY_UPDATED,
                EventType.ACTION_EXECUTED,
            },
            affected_entities={entity_id},
        )

        return await self.subscribe_to_events(event_filter)

    async def subscribe_to_actor_actions(self, source_actor_id: str) -> EventSubscription:
        """Subscribe to actions from specific actor"""
        event_filter = EventFilter(event_types={EventType.ACTION_EXECUTED}, source_actors={source_actor_id})

        return await self.subscribe_to_events(event_filter)

    async def subscribe_to_event_type(self, event_type: EventType) -> EventSubscription:
        """Subscribe to all events of specific type"""
        event_filter = EventFilter(event_types={event_type})
        return await self.subscribe_to_events(event_filter)

    async def _default_event_handler(self, event: ContextEvent) -> None:
        """Default handler: queue event for belief update"""
        await self._pending_observations.put(event)

    async def process_pending_observations(self) -> List[ContextEvent]:
        """Process queued events and update beliefs

        Called during cognitive cycle's believe() stage.
        """
        observations = []

        while not self._pending_observations.empty():
            try:
                event = self._pending_observations.get_nowait()
                observations.append(event)

                # Auto-update belief from event
                if hasattr(self, "beliefs"):
                    await self._update_belief_from_event(event)

            except asyncio.QueueEmpty:
                break

        return observations

    async def _update_belief_from_event(self, event: ContextEvent) -> None:
        """Update belief model based on received event

        This is the reactive observation mechanism.
        Replaces polling-based worldexplorer.observe().
        """
        if event.event_type == EventType.ENTITY_UPDATED:
            if hasattr(self, "beliefs"):
                self.beliefs.update_entity(
                    entity_id=event.affected_entity_id,
                    state=event.data.get("new_state"),
                )

        elif event.event_type == EventType.ACTION_EXECUTED:
            if hasattr(self, "beliefs"):
                self.beliefs.update_from_action(
                    actor_id=event.source_actor_id,
                    action=event.data.get("action"),
                    result=event.data.get("result"),
                )

        elif event.event_type == EventType.WORLD_STATE_CHANGED:
            if hasattr(self, "beliefs"):
                self.beliefs.update_world_state(event.data)

    def unsubscribe_all(self) -> None:
        """Unsubscribe from all events"""
        if self._event_broker:
            for subscription in self._subscriptions:
                self._event_broker.unsubscribe(self.id, subscription)
        self._subscriptions.clear()

    def get_observation_stats(self) -> Dict[str, Any]:
        """Get event subscription statistics"""
        return {
            "actor_id": self.id,
            "active_subscriptions": len(self._subscriptions),
            "pending_observations": self._pending_observations.qsize(),
            "subscriptions": [
                {
                    "event_types": (list(sub.event_filter.event_types) if sub.event_filter.event_types else None),
                    "events_received": sub.event_count,
                }
                for sub in self._subscriptions
            ],
        }


class ReactiveAutonomousActor(EventDrivenActor):
    """Phase 9: Actor with event-driven observation

    Combines Phase 8 autonomous execution with Phase 9 reactive observation.
    Observation is now push-based (events) instead of pull-based (polling).
    """

    async def observe(self, world=None) -> dict:
        """Updated observe() for reactive model

        Replaces polling world state with processing queued events.
        """
        # Process events that arrived since last cycle
        events = await self.process_pending_observations()

        observations = {
            "event_count": len(events),
            "events": events,
            "timestamp": datetime.now(),
        }

        if events:
            observations["event_types"] = list(set(e.event_type for e in events))

        return observations

    def believe_from_events(self, events: List[ContextEvent]) -> bool:
        """Update belief directly from received events

        Part of reactive observe→believe flow.
        """
        if not events or not hasattr(self, "beliefs"):
            return False

        belief_updated = False
        for event in events:
            try:
                if event.event_type == EventType.ENTITY_UPDATED:
                    self.beliefs.incorporate_observation(
                        {
                            "type": "entity_update",
                            "entity": event.affected_entity_id,
                            "state": event.data.get("new_state"),
                        }
                    )
                    belief_updated = True

                elif event.event_type == EventType.ACTION_EXECUTED:
                    self.beliefs.incorporate_observation(
                        {
                            "type": "action_execution",
                            "actor": event.source_actor_id,
                            "action": event.data.get("action"),
                            "result": event.data.get("result"),
                        }
                    )
                    belief_updated = True

            except Exception as e:
                print(f"Error updating belief from event: {e}")

        return belief_updated
