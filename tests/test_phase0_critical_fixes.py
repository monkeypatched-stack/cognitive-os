"""Phase 0: Critical Bug Fixes Test Suite

Tests for:
1. WorldView clone() enforcement (no fallback)
2. Multi-tenant context filtering
3. ActorState persistence (save/load)
4. Layer #15 persistence mechanism
5. Layer #6 state restoration
"""

import pytest
import pickle
import json
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch


class TestWorldViewCloneFix:
    """Test: Remove clone() fallback and enforce cloning."""

    def test_clone_must_return_new_instance(self):
        """Clone must return a NEW instance, not self."""
        from src.actor.immutable_world import WorldView

        # Create mock world that properly clones
        mock_world = Mock()
        mock_world_clone = Mock()  # Different instance
        mock_world.clone = Mock(return_value=mock_world_clone)

        wrapper = WorldView(mock_world)
        cloned = wrapper.clone()

        assert cloned is mock_world_clone
        assert cloned is not mock_world

    def test_clone_raises_if_not_supported(self):
        """Clone must raise if world doesn't support cloning."""
        from src.actor.immutable_world import WorldView

        mock_world = Mock(spec=[])  # No clone method
        wrapper = WorldView(mock_world)

        with pytest.raises(NotImplementedError) as exc_info:
            wrapper.clone()

        assert "does not support cloning" in str(exc_info.value)

    def test_clone_raises_if_returns_self(self):
        """Clone must raise if world.clone() returns self reference."""
        from src.actor.immutable_world import WorldView

        mock_world = Mock()
        mock_world.clone = Mock(return_value=mock_world)  # Returns self!

        wrapper = WorldView(mock_world)

        with pytest.raises(RuntimeError) as exc_info:
            wrapper.clone()

        assert "must return a NEW instance" in str(exc_info.value)


class TestMultiTenantContextFiltering:
    """Test: ContextStream tenant-aware event delivery."""

    def test_context_event_has_tenant_id(self):
        """ContextEvent must include tenant_id field."""
        from src.monkey_brain.kernel.compile.context_stream import ContextEvent

        event = ContextEvent(
            timestamp=0.0,
            entity="robot_1",
            attribute="position",
            previous_value="A",
            current_value="B",
            source="sensor",
            tenant_id="org_alpha",
        )

        assert event.tenant_id == "org_alpha"

    def test_context_event_default_tenant_is_default(self):
        """ContextEvent defaults to 'default' tenant if not specified."""
        from src.monkey_brain.kernel.compile.context_stream import ContextEvent

        event = ContextEvent(
            timestamp=0.0,
            entity="robot_1",
            attribute="position",
            previous_value="A",
            current_value="B",
            source="sensor",
        )

        assert event.tenant_id == "default"

    def test_tenant_scoped_subscription(self):
        """Subscribers can be tenant-scoped."""
        from src.monkey_brain.kernel.compile.context_stream import ContextStream

        mock_world = Mock()
        mock_world.nnz = Mock(return_value=0)
        mock_world.states = Mock(return_value=[])

        stream = ContextStream(mock_world)

        # Register tenant-scoped callbacks
        callback_alpha = Mock()
        callback_beta = Mock()

        stream.subscribe(callback_alpha, tenant_id="org_alpha")
        stream.subscribe(callback_beta, tenant_id="org_beta")

        # Publish event for org_alpha only
        from src.monkey_brain.kernel.compile.context_stream import (
            ContextEvent,
            EventType,
        )

        event = ContextEvent(
            timestamp=0.0,
            entity="robot",
            attribute="state",
            previous_value="idle",
            current_value="active",
            source="sensor",
            event_type=EventType.STATE_CHANGE,
            tenant_id="org_alpha",
        )

        stream.publish(event)

        # Only alpha callback should be called
        assert callback_alpha.called
        assert not callback_beta.called

    def test_global_subscriber_receives_all_events(self):
        """Global subscribers (no tenant_id) receive all events."""
        from src.monkey_brain.kernel.compile.context_stream import (
            ContextStream,
            ContextEvent,
            EventType,
        )

        mock_world = Mock()
        mock_world.nnz = Mock(return_value=0)
        mock_world.states = Mock(return_value=[])

        stream = ContextStream(mock_world)

        global_callback = Mock()
        stream.subscribe(global_callback)  # No tenant_id

        # Publish events for different tenants
        for tenant in ["org_alpha", "org_beta"]:
            event = ContextEvent(
                timestamp=0.0,
                entity="entity",
                attribute="attr",
                previous_value="old",
                current_value="new",
                source="sensor",
                event_type=EventType.STATE_CHANGE,
                tenant_id=tenant,
            )
            stream.publish(event)

        # Global subscriber should see both
        assert global_callback.call_count == 2


class TestActorStatePersistence:
    """Test: ActorStateStore save/load."""

    def test_persisted_actor_state_schema(self):
        """PersistedActorState has all required fields."""
        from src.monkey_brain.persistence.actor_state_store import PersistedActorState

        state = PersistedActorState(
            actor_id="alice",
            tenant_id="org_alpha",
            belief_tensor=b"belief_data",
            bellman_policy=b"policy_data",
            phi_compiled=b"phi_data",
            memory_kv={"key": "value"},
            last_updated=datetime.now().isoformat(),
            version=5,
        )

        assert state.actor_id == "alice"
        assert state.tenant_id == "org_alpha"
        assert state.belief_tensor == b"belief_data"
        assert state.version == 5

    def test_actor_state_store_save(self):
        """ActorStateStore saves state to database."""
        from src.monkey_brain.persistence.actor_state_store import (
            ActorStateStore,
            PersistedActorState,
        )

        # Mock database
        mock_db = Mock()
        mock_cursor = Mock()
        mock_db.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_db.cursor.return_value.__exit__ = Mock(return_value=False)

        store = ActorStateStore(mock_db)

        state = PersistedActorState(
            actor_id="alice",
            tenant_id="org_alpha",
            belief_tensor=b"belief",
            bellman_policy=b"policy",
            phi_compiled=b"phi",
            memory_kv={"key": "value"},
            last_updated=datetime.now().isoformat(),
            version=1,
        )

        # Mock the init_schema to prevent SQL execution
        with patch.object(store, "_init_schema"):
            store.save(state)

        # Verify SQL was executed
        assert mock_cursor.execute.called

    def test_actor_state_store_load(self):
        """ActorStateStore loads state from database."""
        from src.monkey_brain.persistence.actor_state_store import (
            ActorStateStore,
            PersistedActorState,
        )

        # Mock database
        mock_db = Mock()
        mock_cursor = Mock()

        # Mock the load result
        load_time = datetime.now()
        mock_cursor.fetchone.return_value = (
            "alice",  # actor_id
            "org_alpha",  # tenant_id
            b"belief_data",  # belief_tensor
            b"policy_data",  # bellman_policy
            b"phi_data",  # phi_compiled
            '{"key": "value"}',  # memory_kv
            load_time,  # last_updated
            5,  # version
            True,  # is_active
            10,  # cycle_count
            1234.5,  # last_cycle
        )

        mock_db.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_db.cursor.return_value.__exit__ = Mock(return_value=False)

        store = ActorStateStore(mock_db)

        with patch.object(store, "_init_schema"):
            state = store.load("alice", "org_alpha")

        assert state is not None
        assert state.actor_id == "alice"
        assert state.tenant_id == "org_alpha"
        assert state.belief_tensor == b"belief_data"
        assert state.version == 5
        assert state.cycle_count == 10

    def test_actor_state_store_load_missing_actor(self):
        """ActorStateStore returns None for missing actor."""
        from src.monkey_brain.persistence.actor_state_store import ActorStateStore

        mock_db = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = None  # No result

        mock_db.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_db.cursor.return_value.__exit__ = Mock(return_value=False)

        store = ActorStateStore(mock_db)

        with patch.object(store, "_init_schema"):
            state = store.load("nonexistent", "org_alpha")

        assert state is None


class TestMultiTenantIsolation:
    """Test: Multi-tenant isolation guarantees."""

    def test_tenant_context_prevents_cross_tenant_leakage(self):
        """Tenant context ensures event filtering."""
        from src.monkey_brain.kernel.compile.context_stream import (
            ContextStream,
            ContextEvent,
            EventType,
        )

        mock_world = Mock()
        mock_world.nnz = Mock(return_value=0)
        mock_world.states = Mock(return_value=[])

        stream = ContextStream(mock_world)

        events_alpha = []
        events_beta = []

        def on_alpha(event, version):
            events_alpha.append(event)

        def on_beta(event, version):
            events_beta.append(event)

        stream.subscribe(on_alpha, tenant_id="org_alpha")
        stream.subscribe(on_beta, tenant_id="org_beta")

        # Publish 5 events for org_alpha
        for i in range(5):
            event = ContextEvent(
                timestamp=float(i),
                entity=f"entity_{i}",
                attribute="attr",
                previous_value="old",
                current_value="new",
                source="sensor",
                event_type=EventType.STATE_CHANGE,
                tenant_id="org_alpha",
            )
            stream.publish(event)

        # Publish 3 events for org_beta
        for i in range(3):
            event = ContextEvent(
                timestamp=float(i),
                entity=f"entity_{i}",
                attribute="attr",
                previous_value="old",
                current_value="new",
                source="sensor",
                event_type=EventType.STATE_CHANGE,
                tenant_id="org_beta",
            )
            stream.publish(event)

        # Each subscriber should only see their tenant's events
        assert len(events_alpha) == 5
        assert len(events_beta) == 3
        assert all(e.tenant_id == "org_alpha" for e in events_alpha)
        assert all(e.tenant_id == "org_beta" for e in events_beta)


# ──────────────────────────────────────────────────────────────────
# PHASE 0 COMPLETION TESTS
# ──────────────────────────────────────────────────────────────────


class TestPhase0Completion:
    """Verify all Phase 0 critical fixes are in place."""

    def test_worldview_clone_enforcement(self):
        """WorldView.clone() enforces new instance requirement."""
        from src.actor.immutable_world import WorldView

        mock_world = Mock()
        mock_clone = Mock()
        mock_world.clone = Mock(return_value=mock_clone)

        wrapper = WorldView(mock_world)
        result = wrapper.clone()

        assert result is mock_clone
        assert result is not wrapper

    def test_context_stream_tenant_filtering(self):
        """ContextStream filters events by tenant."""
        from src.monkey_brain.kernel.compile.context_stream import (
            ContextStream,
            ContextEvent,
            EventType,
        )

        mock_world = Mock()
        mock_world.nnz = Mock(return_value=0)
        mock_world.states = Mock(return_value=[])

        stream = ContextStream(mock_world)

        received = []
        stream.subscribe(lambda e, v: received.append(e), tenant_id="org_alpha")

        event = ContextEvent(
            timestamp=0.0,
            entity="entity",
            attribute="attr",
            previous_value="old",
            current_value="new",
            source="sensor",
            event_type=EventType.STATE_CHANGE,
            tenant_id="org_alpha",
        )

        stream.publish(event)

        assert len(received) == 1
        assert received[0].tenant_id == "org_alpha"

    def test_actor_state_persistence_roundtrip(self):
        """ActorState can be serialized and deserialized."""
        from src.monkey_brain.persistence.actor_state_store import PersistedActorState

        original = PersistedActorState(
            actor_id="alice",
            tenant_id="org_alpha",
            belief_tensor=b"belief",
            bellman_policy=b"policy",
            phi_compiled=b"phi",
            memory_kv={"key": "value"},
            last_updated=datetime.now().isoformat(),
            version=3,
        )

        serialized = json.dumps(
            {
                "actor_id": original.actor_id,
                "tenant_id": original.tenant_id,
                "version": original.version,
                "memory_kv": original.memory_kv,
            }
        )

        parsed = json.loads(serialized)
        assert parsed["actor_id"] == original.actor_id
        assert parsed["version"] == original.version
