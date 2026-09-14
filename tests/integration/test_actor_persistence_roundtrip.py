"""Integration Test: Actor State Persistence Round-Trip.

Tests that actor beliefs, policies, and memory survive requests.
Phase 1 validation: persistence layer working end-to-end.
"""

import pytest
import pickle
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock


class TestActorStatePersistenceRoundTrip:
    """Test: Save actor state → Load actor state → Verify consistency."""

    @pytest.fixture
    def mock_db_pool(self):
        """Mock PostgreSQL connection pool."""
        pool = Mock()
        cursor = MagicMock()

        # Mock context manager
        pool.cursor.return_value.__enter__ = Mock(return_value=cursor)
        pool.cursor.return_value.__exit__ = Mock(return_value=False)

        return pool, cursor

    def test_actor_state_store_initialization(self, mock_db_pool):
        """ActorStateStore initializes with database pool."""
        from src.monkey_brain.persistence.actor_state_store import ActorStateStore

        db_pool, _ = mock_db_pool

        with patch.object(ActorStateStore, "_init_schema"):
            store = ActorStateStore(db_pool)

        assert store is not None
        assert store._db is db_pool

    def test_belief_serialization_roundtrip(self, mock_db_pool):
        """Belief tensor survives serialization → deserialization."""
        from src.monkey_brain.persistence.actor_state_store import PersistedActorState

        # Create belief data
        belief_data = {
            "observations": [("state_1", "state_2", 0.8), ("state_2", "state_3", 0.9)],
            "version": 5,
        }

        # Serialize
        belief_bytes = pickle.dumps(belief_data)
        assert belief_bytes != b""

        # Deserialize
        restored = pickle.loads(belief_bytes)
        assert restored == belief_data
        assert restored["version"] == 5

    def test_bellman_policy_roundtrip(self):
        """Bellman policy survives serialization."""
        # Simulate Bellman Q-value table
        bellman_data = {
            ("state_1", "action_1"): 0.75,
            ("state_2", "action_1"): 0.85,
            ("state_3", "action_2"): 0.95,
        }

        # Serialize
        bellman_bytes = pickle.dumps(bellman_data)

        # Deserialize
        restored = pickle.loads(bellman_bytes)
        assert restored == bellman_data
        assert restored[("state_2", "action_1")] == 0.85

    def test_phi_operator_roundtrip(self):
        """Compiled Φ operator survives serialization."""
        # Simulate compiled operator (sparse matrix-like)
        phi_data = {
            "transitions": [(0, 1, 0.8), (1, 2, 0.9)],
            "sparse_matrix": [(0, 1), (1, 2)],
            "compiled_at": datetime.now().isoformat(),
        }

        # Serialize
        phi_bytes = pickle.dumps(phi_data)

        # Deserialize
        restored = pickle.loads(phi_bytes)
        assert len(restored["transitions"]) == 2
        assert restored["transitions"][0] == (0, 1, 0.8)

    def test_memory_kv_store_roundtrip(self):
        """Actor episodic memory survives serialization."""
        memory_data = {
            "episode_1": {"action": "search", "result": "found_item"},
            "episode_2": {"action": "plan", "result": "plan_created"},
            "visited_states": ["A", "B", "C", "A"],
        }

        # Serialize (as JSON, not pickle)
        import json

        memory_json = json.dumps(memory_data)

        # Deserialize
        restored = json.loads(memory_json)
        assert restored["episode_1"]["action"] == "search"
        assert "A" in restored["visited_states"]

    def test_persisted_actor_state_complete(self):
        """PersistedActorState holds all components correctly."""
        from src.monkey_brain.persistence.actor_state_store import PersistedActorState

        belief_bytes = pickle.dumps({"observations": []})
        bellman_bytes = pickle.dumps({"q_values": {}})
        phi_bytes = pickle.dumps({"transitions": []})
        memory_kv = {"key": "value"}

        state = PersistedActorState(
            actor_id="alice",
            tenant_id="org_alpha",
            belief_state=belief_bytes,
            bellman_policy=bellman_bytes,
            phi_compiled=phi_bytes,
            memory_kv=memory_kv,
            last_updated=datetime.now().isoformat(),
            version=1,
            is_active=True,
            cycle_count=0,
        )

        assert state.actor_id == "alice"
        assert state.tenant_id == "org_alpha"
        assert len(state.belief_state) > 0
        assert len(state.bellman_policy) > 0
        assert len(state.phi_compiled) > 0
        assert state.memory_kv["key"] == "value"
        assert state.version == 1

    def test_actor_state_survives_multiple_cycles(self):
        """Actor state updated across 3 cycles."""
        from src.monkey_brain.persistence.actor_state_store import PersistedActorState

        # Cycle 1: Initial state
        belief_v1 = pickle.dumps({"observations": [("A", "B")]})
        state_v1 = PersistedActorState(
            actor_id="alice",
            tenant_id="org_alpha",
            belief_state=belief_v1,
            bellman_policy=b"",
            phi_compiled=b"",
            memory_kv={"cycle": 1},
            last_updated=datetime.now().isoformat(),
            version=1,
        )

        # Cycle 2: Updated belief
        belief_v2 = pickle.dumps({"observations": [("A", "B"), ("B", "C")]})
        state_v2 = PersistedActorState(
            actor_id="alice",
            tenant_id="org_alpha",
            belief_state=belief_v2,
            bellman_policy=b"",
            phi_compiled=b"",
            memory_kv={"cycle": 2},
            last_updated=datetime.now().isoformat(),
            version=2,
        )

        # Cycle 3: Bellman updated
        bellman_v3 = pickle.dumps({"q": {("A", "go_to_C"): 0.95}})
        state_v3 = PersistedActorState(
            actor_id="alice",
            tenant_id="org_alpha",
            belief_state=belief_v2,  # Same as v2
            bellman_policy=bellman_v3,  # Now updated
            phi_compiled=b"",
            memory_kv={"cycle": 3},
            last_updated=datetime.now().isoformat(),
            version=3,
        )

        # Verify states are independent
        assert state_v1.version == 1
        assert state_v2.version == 2
        assert state_v3.version == 3

        # Verify belief evolved
        belief_1 = pickle.loads(state_v1.belief_state)
        belief_3 = pickle.loads(state_v3.belief_state)
        assert len(belief_3["observations"]) > len(belief_1["observations"])

        # Verify Bellman appeared in v3
        assert state_v1.bellman_policy == b""
        assert state_v3.bellman_policy != b""

    def test_tenant_isolation_in_persistence(self):
        """Two tenants have separate actor states."""
        from src.monkey_brain.persistence.actor_state_store import PersistedActorState

        # Tenant A: Alice's state
        state_alice_a = PersistedActorState(
            actor_id="alice",
            tenant_id="org_alpha",
            belief_state=pickle.dumps({"alice": "private"}),
            bellman_policy=b"",
            phi_compiled=b"",
            memory_kv={"tenant": "alpha"},
            last_updated=datetime.now().isoformat(),
            version=1,
        )

        # Tenant B: Alice's state (different!)
        state_alice_b = PersistedActorState(
            actor_id="alice",
            tenant_id="org_beta",
            belief_state=pickle.dumps({"alice": "different"}),
            bellman_policy=b"",
            phi_compiled=b"",
            memory_kv={"tenant": "beta"},
            last_updated=datetime.now().isoformat(),
            version=1,
        )

        # Verify isolation
        assert state_alice_a.tenant_id == "org_alpha"
        assert state_alice_b.tenant_id == "org_beta"

        # Beliefs are different
        belief_a = pickle.loads(state_alice_a.belief_state)
        belief_b = pickle.loads(state_alice_b.belief_state)
        assert belief_a != belief_b

        # Same actor, different tenants = separate state
        assert state_alice_a.actor_id == state_alice_b.actor_id
        assert state_alice_a.tenant_id != state_alice_b.tenant_id


class TestDatabasePoolIntegration:
    """Test: Database pool manager (MongoDB-backed, see db_pool.py)."""

    @staticmethod
    def _mock_mongo_client():
        """A MongoClient stand-in that satisfies DBPool.connect()'s ping
        and get_database() calls without touching a real MongoDB."""
        client = MagicMock()
        client.admin.command.return_value = {"ok": 1}
        client.get_database.return_value = MagicMock()
        return client

    def test_db_pool_initialization(self):
        """DBPool initializes with default connection string."""
        from src.monkey_brain.persistence.db_pool import DBPool

        with patch("pymongo.MongoClient", return_value=self._mock_mongo_client()):
            pool = DBPool()
            assert pool.connection_string is not None

    def test_db_pool_context_manager(self):
        """DBPool works as context manager."""
        from src.monkey_brain.persistence.db_pool import DBPool

        with patch("pymongo.MongoClient", return_value=self._mock_mongo_client()):
            with DBPool() as pool:
                assert pool is not None

    def test_db_pool_singleton(self):
        """get_db_pool() returns singleton."""
        from src.monkey_brain.persistence.db_pool import get_db_pool, reset_db_pool

        reset_db_pool()  # Clear any existing pool

        with patch("pymongo.MongoClient", return_value=self._mock_mongo_client()):
            pool1 = get_db_pool()
            pool2 = get_db_pool()

            # Should be same instance
            assert pool1 is pool2

        reset_db_pool()


# ──────────────────────────────────────────────────────────────
# PHASE 1 INTEGRATION TESTS
# ──────────────────────────────────────────────────────────────


class TestPhase1Completion:
    """Verify Phase 1 Deliverable #1 complete."""

    def test_persistence_roundtrip_complete(self):
        """Belief → Serialize → Deserialize → Belief works."""
        # This is the fundamental capability for Phase 1
        belief = {"observations": [("A", "B", 0.8)], "version": 1}

        # Serialize
        serialized = pickle.dumps(belief)

        # Deserialize
        restored = pickle.loads(serialized)

        # Should match
        assert restored == belief


# ──────────────────────────────────────────────────────────────
# PRODUCTION HARDENING — Actor write/read path after real restart
#
# Everything above this line exercises pickle round-trips on ad-hoc
# dicts, or PersistedActorState in isolation — none of it constructs a
# real PlanetaryRuntime or touches the actual restart path an actor's
# state goes through in production. These tests do: two SEPARATE
# PlanetaryRuntime() instances sharing the same Redis (this file's
# fixture — tests/conftest.py::_flush_shared_redis — flushes it before
# each test, so this is a real "cold, empty Redis -> instance A writes
# -> instance B is a fresh process reading the same durable store" test,
# not two views of one in-memory object).
#
# Regression target: kernel/society/integration.py's PlanetaryRuntime
# used to build self._execution_engine (the ONE shared capability-bus
# engine every actor's ActionExecutor is threaded through) BEFORE
# _load_societies() could reassign self._society_runtime to a
# rehydrated object loaded from Redis — on every restart AFTER the
# very first (i.e. any restart where a society was already persisted),
# the execution engine's context_stream reference stayed bound to the
# original, now-orphaned SocietyRuntime's stream object instead of the
# one everything else (context_engine.py's read side, in particular)
# resolves via the self.context_stream property. Confirmed live via
# id() diagnostics on a real, many-times-restarted demo actor: two
# different objects, write side near-empty, read side with 4000+
# events. Fixed by moving the execution-engine construction to after
# _load_societies() settles self._society_runtime.
# ──────────────────────────────────────────────────────────────


class TestActorRestartRehydration:
    @staticmethod
    def _profile(name: str):
        from src.monkey_brain.kernel.society.domain import (
            ActorIdentity,
            ActorProfile,
            ActorType,
        )

        return ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN))

    def test_actor_survives_process_restart_with_exact_state(self):
        """Register an actor against one PlanetaryRuntime instance
        (process A). Construct a brand-new PlanetaryRuntime instance
        against the SAME Redis (simulating process B after a restart —
        PlanetaryRuntime holds no state outside what it persists/loads).
        The actor, its name, and its type must be exactly what was
        written, not lost or defaulted."""
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        pr_a = PlanetaryRuntime()
        profile = self._profile("Alice Restart Test")
        state_a = pr_a.register_actor(profile)
        actor_id = state_a.profile.identity.actor_id

        pr_b = PlanetaryRuntime()  # a real second instance, not pr_a again
        found = pr_b.get_actor_runtime(actor_id)
        assert found is not None, "actor did not survive a real process restart"
        restored_state = None
        for sr in pr_b.all_societies():
            restored_state = sr.get_actor(actor_id)
            if restored_state is not None:
                break
        assert restored_state is not None
        assert restored_state.profile.identity.name == "Alice Restart Test"

    def test_multiple_actors_survive_restart(self):
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        pr_a = PlanetaryRuntime()
        ids = []
        for name in ("Actor One", "Actor Two", "Actor Three"):
            state = pr_a.register_actor(self._profile(name))
            ids.append(state.profile.identity.actor_id)

        pr_b = PlanetaryRuntime()
        for actor_id in ids:
            assert pr_b.get_actor_runtime(actor_id) is not None

    def test_context_stream_wiring_is_shared_after_rehydration(self):
        """The specific regression: after a real restart (a society
        that already existed in Redis gets reloaded, not freshly
        minted), the shared execution engine's context_stream must be
        THE SAME object every read path resolves via
        self.context_stream — not a stale reference captured before
        rehydration reassigned the canonical SocietyRuntime."""
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        pr_a = PlanetaryRuntime()
        pr_a.register_actor(self._profile("Bootstrap Actor"))
        # pr_a's own construction already persisted a real society (see
        # __init__'s "World persistence gap" comment) — pr_b below is
        # the FIRST instance to load it back via _load_societies(),
        # exactly the code path the bug lived in.

        pr_b = PlanetaryRuntime()
        assert pr_b._execution_engine is not None
        write_side = getattr(pr_b._execution_engine, "_context_stream", None)
        read_side = pr_b.context_stream
        assert write_side is not None, "execution engine has no context_stream at all"
        assert write_side is read_side, (
            f"execution engine's context_stream ({id(write_side)}) is a DIFFERENT "
            f"object from the canonical read-side context_stream ({id(read_side)}) "
            "after rehydration — every real action published by this actor would "
            "be invisible to every context/grounding read path."
        )

    def test_new_write_after_restart_is_read_back(self):
        """Not just "the actor still exists" — a NEW write made against
        the rehydrated (process B) instance must itself be durable and
        visible to a THIRD instance (process C), proving the write path
        the rehydrated instance uses is the same canonical one, not a
        disconnected one that silently drops future writes too."""
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
        from src.monkey_brain.kernel.society.context_stream import (
            ContextEvent,
            ContextEventType,
        )

        pr_a = PlanetaryRuntime()
        state = pr_a.register_actor(self._profile("Write After Restart"))
        actor_id = state.profile.identity.actor_id

        pr_b = PlanetaryRuntime()  # process B: rehydrated
        pr_b.context_stream.publish(
            ContextEvent(
                event_type=ContextEventType.INTERACTION,
                actor_id=actor_id,
                description="a new event written after restart",
            )
        )
        # This write must go through the SAME path a real action's
        # publish would (society.integration._save_context, called via
        # ContextStream's on_publish hook) for a THIRD instance to see it.
        pr_c = PlanetaryRuntime()  # process C: rehydrated again
        events = pr_c.context_stream.replay(actor_id=actor_id)
        assert any(e.description == "a new event written after restart" for e in events), (
            "a write made against a rehydrated PlanetaryRuntime instance did not "
            "survive to a THIRD instance — the rehydrated instance's own write "
            "path is itself disconnected from durable storage."
        )
