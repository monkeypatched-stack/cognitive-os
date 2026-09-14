"""Tests for Event Sourcing Layer."""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
from src.monkey_brain.kernel.compile.society import Actor, ActorNetwork
from src.monkey_brain.kernel.compile.trust import Relationship
from src.monkey_brain.kernel.compile.event_sourcing import (
    EventStore,
    EventType,
    Event,
    Snapshot,
    EventReplayEngine,
    SubscriptionManager,
)


class TestEvent:
    """Event dataclass."""

    def test_create_event(self):
        event = Event(
            event_type=EventType.TRANSITION,
            actor_id="alice",
            src="s1",
            dst="s2",
            domain="manufacturing",
        )
        assert event.event_type == EventType.TRANSITION
        assert event.actor_id == "alice"

    def test_event_to_dict(self):
        event = Event(
            event_type=EventType.BELIEF,
            actor_id="alice",
            src="s1",
            dst="s2",
            new_value=0.8,
        )
        d = event.to_dict()
        assert d["event_type"] == "world.belief"
        assert d["actor_id"] == "alice"
        assert d["new_value"] == 0.8

    def test_event_from_dict(self):
        d = {
            "event_id": "test-123",
            "event_type": "world.transition",
            "timestamp": 1234567890.0,
            "actor_id": "alice",
            "src": "s1",
            "dst": "s2",
        }
        event = Event.from_dict(d)
        assert event.event_id == "test-123"
        assert event.event_type == EventType.TRANSITION


class TestSnapshot:
    """Snapshot dataclass."""

    def test_create_snapshot(self):
        snapshot = Snapshot(
            event_index=100,
            world_state={"transitions": 50},
            actor_states={"alice": {"belief_size": 10}},
        )
        assert snapshot.event_index == 100
        assert snapshot.world_state["transitions"] == 50

    def test_snapshot_to_dict(self):
        snapshot = Snapshot(
            event_index=100,
            world_state={"transitions": 50},
            actor_states={"alice": {"belief_size": 10}},
        )
        d = snapshot.to_dict()
        assert d["event_index"] == 100
        assert d["world_state"]["transitions"] == 50

    def test_snapshot_from_dict(self):
        d = {
            "snapshot_id": "snap-123",
            "timestamp": 1234567890.0,
            "event_index": 100,
            "world_state": {"transitions": 50},
            "actor_states": {"alice": {"belief_size": 10}},
        }
        snapshot = Snapshot.from_dict(d)
        assert snapshot.snapshot_id == "snap-123"
        assert snapshot.event_index == 100


class TestEventStore:
    """EventStore append-only event storage."""

    def test_append_event(self):
        store = EventStore()
        event = Event(event_type=EventType.TRANSITION, src="s1", dst="s2")
        store.append(event)
        assert store.count() == 1

    def test_query_by_type(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION, src="s1", dst="s2"))
        store.append(Event(event_type=EventType.BELIEF, src="s1", dst="s2"))
        store.append(Event(event_type=EventType.TRANSITION, src="s3", dst="s4"))

        result = store.query(event_type=EventType.TRANSITION)
        assert len(result) == 2

    def test_query_by_actor(self):
        store = EventStore()
        store.append(Event(event_type=EventType.BELIEF, actor_id="alice"))
        store.append(Event(event_type=EventType.BELIEF, actor_id="bob"))

        result = store.query(actor_id="alice")
        assert len(result) == 1

    def test_replay(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION, timestamp=1.0))
        store.append(Event(event_type=EventType.BELIEF, timestamp=2.0))
        store.append(Event(event_type=EventType.TRANSITION, timestamp=3.0))

        result = store.replay(since=2.0)
        assert len(result) == 2

    def test_subscribe(self):
        store = EventStore()
        received = []
        store.subscribe(lambda e: received.append(e))

        store.append(Event(event_type=EventType.TRANSITION))
        assert len(received) == 1

    def test_snapshotting(self):
        store = EventStore()

        # Create some events
        for i in range(10):
            store.append(Event(event_type=EventType.TRANSITION, src=f"s{i}", dst=f"s{i + 1}"))

        # Create a snapshot
        snapshot = store.create_snapshot(
            world_state={"transitions": 10},
            actor_states={"alice": {"belief_size": 5}},
        )
        assert snapshot.event_index == 10

        # Get latest snapshot
        latest = store.get_latest_snapshot()
        assert latest is not None
        assert latest.event_index == 10

    def test_replay_from_snapshot(self):
        store = EventStore()

        # Create events
        for i in range(5):
            store.append(Event(event_type=EventType.TRANSITION, src=f"s{i}", dst=f"s{i + 1}"))

        # Create snapshot at event 3
        snapshot = store.create_snapshot(
            world_state={"transitions": 3},
            actor_states={},
        )
        snapshot.event_index = 3

        # More events
        for i in range(5, 10):
            store.append(Event(event_type=EventType.TRANSITION, src=f"s{i}", dst=f"s{i + 1}"))

        # Replay from snapshot
        events_since = store.replay_from_snapshot(snapshot)
        assert len(events_since) == 7  # events 3-9

    def test_rebuild_from_snapshot(self):
        store = EventStore()

        # Create events
        for i in range(5):
            store.append(Event(event_type=EventType.TRANSITION, src=f"s{i}", dst=f"s{i + 1}"))

        # Create snapshot
        snapshot = store.create_snapshot(
            world_state={"transitions": 5},
            actor_states={"alice": {"belief_size": 3}},
        )

        # Rebuild
        state = store.rebuild_from_snapshot(snapshot)
        assert state["snapshot"]["world_state"]["transitions"] == 5
        assert state["events_since"] == 0  # No events after snapshot


class TestActorNetworkEventSourcing:
    """ActorNetwork records events during operations."""

    def test_ask_records_events(self):
        world = SparseTransitionTensor()
        world.observe("s1", "s2")

        network = ActorNetwork(world)
        network.add("alice")

        network.ask("alice", "s1")

        events = network.get_event_history()
        assert len(events) >= 1
        assert any(e["event_type"] == "world.belief" for e in events)

    def test_gossip_records_events(self):
        from src.monkey_brain.kernel.compile.trust import Relationship

        world = SparseTransitionTensor()
        world.observe("s1", "s2")

        network = ActorNetwork(world)
        network.add("alice")
        network.add("bob")
        network.connect("alice", "bob", Relationship.COMMUNITY)

        network.ask("alice", "s1")

        events = network.get_event_history()
        gossip_events = [e for e in events if e["event_type"] == "world.gossip"]
        assert len(gossip_events) >= 1

    def test_get_actor_events(self):
        world = SparseTransitionTensor()
        world.observe("s1", "s2")

        network = ActorNetwork(world)
        network.add("alice")
        network.add("bob")

        network.ask("alice", "s1")
        network.ask("bob", "s1")

        alice_events = network.get_actor_events("alice")
        bob_events = network.get_actor_events("bob")

        assert len(alice_events) >= 1
        assert len(bob_events) >= 1

    def test_event_history_in_summary(self):
        world = SparseTransitionTensor()
        network = ActorNetwork(world)
        network.add("alice")

        summary = network.summary()
        assert "conflicts" in summary


class TestEventProjection:
    """Event projection builds read-optimized views from events."""

    def test_project_actor_beliefs(self):
        store = EventStore()
        store.append(
            Event(
                event_type=EventType.BELIEF,
                actor_id="alice",
                src="s1",
                dst="s2",
                new_value=0.8,
            )
        )
        store.append(
            Event(
                event_type=EventType.BELIEF,
                actor_id="bob",
                src="s1",
                dst="s2",
                new_value=0.6,
            )
        )
        store.append(
            Event(
                event_type=EventType.BELIEF,
                actor_id="alice",
                src="s2",
                dst="s3",
                new_value=0.9,
            )
        )

        beliefs = store.project_actor_beliefs()
        assert "alice" in beliefs
        assert "bob" in beliefs
        assert "s1→s2" in beliefs["alice"]
        assert beliefs["alice"]["s1→s2"] == 0.8

    def test_project_world_transitions(self):
        store = EventStore()
        store.append(
            Event(
                event_type=EventType.TRANSITION,
                src="s1",
                dst="s2",
                domain="manufacturing",
                new_value=0.7,
            )
        )
        store.append(
            Event(
                event_type=EventType.TRANSITION,
                src="s1",
                dst="s2",
                domain="manufacturing",
                new_value=0.8,
            )
        )
        store.append(
            Event(
                event_type=EventType.TRANSITION,
                src="s2",
                dst="s3",
                domain="manufacturing",
                new_value=0.5,
            )
        )

        transitions = store.project_world_transitions()
        assert "s1→s2" in transitions
        assert transitions["s1→s2"]["count"] == 2
        assert transitions["s1→s2"]["last_value"] == 0.8

    def test_project_conflicts(self):
        store = EventStore()
        store.append(
            Event(
                event_type=EventType.CONFLICT,
                src="s1",
                dst="s2",
                actor_id="alice",
                old_value=0.9,
                new_value=0.7,
                metadata={"strategy": "trust_weighted", "severity": 0.5},
            )
        )

        conflicts = store.project_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0]["strategy"] == "trust_weighted"

    def test_project_statistics(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION, actor_id="alice"))
        store.append(Event(event_type=EventType.BELIEF, actor_id="alice"))
        store.append(Event(event_type=EventType.BELIEF, actor_id="bob"))

        stats = store.project_statistics()
        assert stats["total_events"] == 3
        assert stats["by_type"]["world.transition"] == 1
        assert stats["by_type"]["world.belief"] == 2
        assert stats["by_actor"]["alice"] == 2
        assert stats["by_actor"]["bob"] == 1

    def test_projections_are_read_optimized(self):
        """Projections build views from events, not from raw event list."""
        store = EventStore()

        # Add many events
        for i in range(100):
            store.append(Event(event_type=EventType.TRANSITION, src=f"s{i}", dst=f"s{i + 1}"))

        # Projection should be fast (built from events)
        stats = store.project_statistics()
        assert stats["total_events"] == 100
        assert "world.transition" in stats["by_type"]


class TestEventReplay:
    """Event replay reconstructs state from events."""

    def test_replay_to_time(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION, timestamp=1.0))
        store.append(Event(event_type=EventType.BELIEF, timestamp=2.0))
        store.append(Event(event_type=EventType.TRANSITION, timestamp=3.0))

        events = store.replay_to_time(2.0)
        assert len(events) == 2

    def test_replay_range(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION, timestamp=1.0))
        store.append(Event(event_type=EventType.BELIEF, timestamp=2.0))
        store.append(Event(event_type=EventType.TRANSITION, timestamp=3.0))
        store.append(Event(event_type=EventType.BELIEF, timestamp=4.0))

        events = store.replay_range(2.0, 3.0)
        assert len(events) == 2

    def test_replay_by_actor(self):
        store = EventStore()
        store.append(Event(event_type=EventType.BELIEF, actor_id="alice", timestamp=1.0))
        store.append(Event(event_type=EventType.BELIEF, actor_id="bob", timestamp=2.0))
        store.append(Event(event_type=EventType.BELIEF, actor_id="alice", timestamp=3.0))

        events = store.replay_by_actor("alice")
        assert len(events) == 2

    def test_replay_by_type(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION, timestamp=1.0))
        store.append(Event(event_type=EventType.BELIEF, timestamp=2.0))
        store.append(Event(event_type=EventType.TRANSITION, timestamp=3.0))

        events = store.replay_by_type(EventType.TRANSITION)
        assert len(events) == 2


class TestEventReplayEngine:
    """EventReplayEngine reconstructs state from events."""

    def test_rebuild_world_state(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION, src="s1", dst="s2", new_value=0.8))
        store.append(Event(event_type=EventType.TRANSITION, src="s2", dst="s3", new_value=0.6))

        engine = EventReplayEngine(store)
        state = engine.rebuild_world_state()

        assert ("s1", "s2") in state
        assert state[("s1", "s2")] == 0.8

    def test_rebuild_actor_beliefs(self):
        store = EventStore()
        store.append(
            Event(
                event_type=EventType.BELIEF,
                actor_id="alice",
                src="s1",
                dst="s2",
                new_value=0.9,
            )
        )
        store.append(
            Event(
                event_type=EventType.BELIEF,
                actor_id="bob",
                src="s1",
                dst="s2",
                new_value=0.3,
            )
        )

        engine = EventReplayEngine(store)
        alice_beliefs = engine.rebuild_actor_beliefs("alice")

        assert "s1→s2" in alice_beliefs
        assert alice_beliefs["s1→s2"] == 0.9

    def test_rebuild_all_actor_beliefs(self):
        store = EventStore()
        store.append(
            Event(
                event_type=EventType.BELIEF,
                actor_id="alice",
                src="s1",
                dst="s2",
                new_value=0.9,
            )
        )
        store.append(
            Event(
                event_type=EventType.BELIEF,
                actor_id="bob",
                src="s1",
                dst="s2",
                new_value=0.3,
            )
        )

        engine = EventReplayEngine(store)
        all_beliefs = engine.rebuild_all_actor_beliefs()

        assert "alice" in all_beliefs
        assert "bob" in all_beliefs

    def test_rebuild_conflict_history(self):
        store = EventStore()
        store.append(
            Event(
                event_type=EventType.CONFLICT,
                src="s1",
                dst="s2",
                old_value=0.9,
                new_value=0.7,
                metadata={"strategy": "trust_weighted", "severity": 0.5},
            )
        )

        engine = EventReplayEngine(store)
        history = engine.rebuild_conflict_history()

        assert len(history) == 1
        assert history[0]["strategy"] == "trust_weighted"

    def test_temporal_query(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION, src="s1", dst="s2", timestamp=1.0))
        store.append(
            Event(
                event_type=EventType.BELIEF,
                actor_id="alice",
                src="s1",
                dst="s2",
                timestamp=2.0,
            )
        )
        store.append(Event(event_type=EventType.TRANSITION, src="s2", dst="s3", timestamp=3.0))

        engine = EventReplayEngine(store)
        state = engine.temporal_query(2.0)

        assert "world_transitions" in state
        assert "actor_beliefs" in state
        assert state["events_at_time"] == 2

    def test_replay_events(self):
        store = EventStore()
        events = [
            Event(event_type=EventType.TRANSITION, src="s1", dst="s2"),
            Event(event_type=EventType.BELIEF, actor_id="alice", src="s1", dst="s2"),
            Event(event_type=EventType.CONFLICT, src="s1", dst="s2"),
        ]

        engine = EventReplayEngine(store)
        result = engine.replay_events(events)

        assert result["world_transitions"] == 1
        assert result["actor_count"] == 1
        assert result["conflict_count"] == 1
        assert result["events_replayed"] == 3


class TestUndoRedo:
    """Undo/redo functionality for event sourcing."""

    def test_undo_returns_inverse_event(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION, src="s1", dst="s2", new_value=0.8))

        inverse = store.undo()
        assert inverse is not None
        assert inverse.old_value == 0.8  # Swapped
        assert inverse.new_value == 0.0
        assert inverse.metadata["action"] == "undo"

    def test_undo_returns_none_when_empty(self):
        store = EventStore()
        assert store.undo() is None

    def test_redo_reapplies_event(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION, src="s1", dst="s2", new_value=0.8))

        store.undo()
        redo_event = store.redo()
        assert redo_event is not None
        assert redo_event.new_value == 0.8
        assert redo_event.metadata["action"] == "redo"

    def test_redo_returns_none_when_empty(self):
        store = EventStore()
        assert store.redo() is None

    def test_can_undo_redo(self):
        store = EventStore()
        assert not store.can_undo()

        store.append(Event(event_type=EventType.TRANSITION))
        assert store.can_undo()
        assert not store.can_redo()

        store.undo()
        assert store.can_undo()
        assert store.can_redo()

    def test_undo_redo_counts(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION))
        store.append(Event(event_type=EventType.BELIEF))

        # 2 events + 0 undo stack
        assert store.undo_count() == 2
        assert store.redo_count() == 0

        store.undo()
        # 3 events (2 original + 1 inverse) + 1 in undo stack
        assert store.undo_count() == 3
        assert store.redo_count() == 1

    def test_clear_undo_stack(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION))
        store.undo()

        assert store.can_redo()
        store.clear_undo_stack()
        assert not store.can_redo()

    def test_undo_redo_preserves_event_type(self):
        store = EventStore()
        store.append(Event(event_type=EventType.BELIEF, actor_id="alice", src="s1", dst="s2"))

        inverse = store.undo()
        assert inverse.event_type == EventType.BELIEF
        assert inverse.actor_id == "alice"

    def test_multiple_undo_redo(self):
        store = EventStore()
        store.append(Event(event_type=EventType.TRANSITION, src="s1", dst="s2"))
        store.append(Event(event_type=EventType.BELIEF, src="s2", dst="s3"))

        store.undo()  # +1 inverse event
        store.undo()  # +1 inverse event
        # 2 original + 2 inverse = 4 events, 2 in undo stack
        assert store.undo_count() == 4
        assert store.redo_count() == 2

        store.redo()  # removes from undo stack, adds redo event
        store.redo()  # removes from undo stack, adds redo event
        # 2 original + 2 inverse + 2 redo = 6 events, 0 in undo stack
        assert store.undo_count() == 6
        assert store.redo_count() == 0


class TestEventPersistence:
    """Event persistence survives restarts."""

    def test_save_and_load(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "events.json")

            # Create and save
            store1 = EventStore(persist_path=path)
            store1.append(Event(event_type=EventType.TRANSITION, src="s1", dst="s2"))
            store1.append(Event(event_type=EventType.BELIEF, actor_id="alice", src="s2", dst="s3"))

            # Load in new instance
            store2 = EventStore(persist_path=path)
            assert store2.count() == 2

    def test_auto_save(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "events.json")

            store = EventStore(persist_path=path)
            store.append(Event(event_type=EventType.TRANSITION, src="s1", dst="s2"))

            # File should exist after append
            assert Path(path).exists()

    def test_load_preserves_snapshots(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "events.json")

            store1 = EventStore(persist_path=path)
            for i in range(10):
                store1.append(Event(event_type=EventType.TRANSITION, src=f"s{i}", dst=f"s{i + 1}"))
            store1.create_snapshot(world_state={"transitions": 10}, actor_states={})

            store2 = EventStore(persist_path=path)
            assert store2.count() == 10
            assert len(store2._snapshots) == 1

    def test_save_manual(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "events.json")

            store = EventStore()  # No persist_path
            store.append(Event(event_type=EventType.TRANSITION, src="s1", dst="s2"))
            store._persist_path = path
            store.save()

            assert Path(path).exists()

    def test_load_returns_count(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "events.json")

            # Create and save events
            store1 = EventStore(persist_path=path)
            store1.append(Event(event_type=EventType.TRANSITION, src="s1", dst="s2"))

            # Create empty store (no persist_path) and manually load
            store2 = EventStore()
            store2._persist_path = path
            loaded = store2.load()
            assert loaded == 1

    def test_load_nonexistent_file(self):
        store = EventStore(persist_path="/nonexistent/events.json")
        assert store.count() == 0


class TestEventSubscriptions:
    """Event subscriptions for reacting to specific events."""

    def test_subscribe_by_type(self):
        store = EventStore()
        received = []

        sub_id = store.subscribe(
            lambda e: received.append(e),
            event_type=EventType.BELIEF,
        )

        store.append(Event(event_type=EventType.TRANSITION))
        store.append(Event(event_type=EventType.BELIEF))
        store.append(Event(event_type=EventType.TRANSITION))

        assert len(received) == 1  # Only BELIEF events

    def test_subscribe_by_actor(self):
        store = EventStore()
        received = []

        sub_id = store.subscribe(
            lambda e: received.append(e),
            actor_id="alice",
        )

        store.append(Event(event_type=EventType.BELIEF, actor_id="alice"))
        store.append(Event(event_type=EventType.BELIEF, actor_id="bob"))
        store.append(Event(event_type=EventType.BELIEF, actor_id="alice"))

        assert len(received) == 2  # Only alice's events

    def test_unsubscribe(self):
        store = EventStore()
        received = []

        sub_id = store.subscribe(lambda e: received.append(e))
        store.append(Event(event_type=EventType.TRANSITION))
        assert len(received) == 1

        store.unsubscribe(sub_id)
        store.append(Event(event_type=EventType.TRANSITION))
        assert len(received) == 1  # No new events after unsubscribe

    def test_priority_ordering(self):
        store = EventStore()
        order = []

        store.subscribe(lambda e: order.append("low"), priority=0)
        store.subscribe(lambda e: order.append("high"), priority=10)
        store.subscribe(lambda e: order.append("medium"), priority=5)

        store.append(Event(event_type=EventType.TRANSITION))

        assert order == ["high", "medium", "low"]

    def test_subscription_manager_pattern(self):
        store = EventStore()
        mgr = SubscriptionManager(store)
        received = []

        sub_id = mgr.subscribe_pattern(
            lambda e: received.append(e),
            pattern={"event_type": EventType.BELIEF, "actor_id": "alice"},
        )

        store.append(Event(event_type=EventType.BELIEF, actor_id="alice"))
        store.append(Event(event_type=EventType.BELIEF, actor_id="bob"))
        store.append(Event(event_type=EventType.TRANSITION, actor_id="alice"))

        assert len(received) == 1  # Only alice's BELIEF events

    def test_one_time_subscription(self):
        store = EventStore()
        mgr = SubscriptionManager(store)
        received = []

        sub_id = mgr.subscribe_one_time(
            lambda e: received.append(e),
            event_type=EventType.BELIEF,
        )

        store.append(Event(event_type=EventType.BELIEF))
        store.append(Event(event_type=EventType.BELIEF))

        assert len(received) == 1  # Only first event

    def test_subscription_history(self):
        store = EventStore()
        mgr = SubscriptionManager(store)

        mgr.subscribe_pattern(lambda e: None, pattern={"event_type": EventType.BELIEF})
        mgr.subscribe_one_time(lambda e: None, event_type=EventType.TRANSITION)

        history = mgr.get_subscription_history()
        assert len(history) == 2
        # subscribe_pattern records via _store.subscribe, so action is "subscribe"
        # subscribe_one_time also records via _store.subscribe
        assert history[0]["action"] == "subscribe"
        assert history[1]["action"] == "subscribe_one_time"
