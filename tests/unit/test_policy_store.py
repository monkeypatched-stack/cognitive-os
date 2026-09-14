"""Tests for PolicyStore — Phase 1 of World-Policy Separation Refactor.

Verifies that PolicyStore produces identical Q-values to the existing QTable
when given the same inputs. This is the mirror-write validation.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.monkey_brain.kernel.policy.store import PolicyStore


class TestPolicyStoreCore:
    """Core Q-value operations."""

    def test_initial_q_is_neutral_prior(self):
        store = PolicyStore()
        assert store.value("state_a", "action_x") == 0.5

    def test_update_single_transition(self):
        store = PolicyStore(lr=0.1, discount=0.95)
        store.update("s1", "a1", reward=1.0)
        q = store.value("s1", "a1")
        # Terminal update: Q = 0.5 + 0.1 * (1.0 - 0.5) = 0.55
        assert abs(q - 0.55) < 1e-6

    def test_update_with_bootstrap(self):
        store = PolicyStore(lr=0.1, discount=0.95)
        # First learn s2/a2 has Q=1.0
        store.update("s2", "a2", reward=1.0)
        # Now update s1/a1 with next_state=s2
        store.update("s1", "a1", reward=0.5, next_state="s2")
        # target = 0.5 + 0.95 * max_q(s2) = 0.5 + 0.95 * 1.0 = 1.45
        # But max_q(s2) = Q(s2, a2) = 0.55 (after first update)
        # Actually: Q(s2, a2) after update = 0.5 + 0.1 * (1.0 - 0.5) = 0.55
        # target = 0.5 + 0.95 * 0.55 = 1.0225
        # new_q = 0.5 + 0.1 * (1.0225 - 0.5) = 0.5 + 0.05225 = 0.55225
        q = store.value("s1", "a1")
        assert q > 0.5  # Should be above prior

    def test_best_action_selects_highest_q(self):
        store = PolicyStore()
        store.update("s", "a1", reward=0.3)
        store.update("s", "a2", reward=0.9)
        best = store.best_action("s", ["a1", "a2"])
        assert best == "a2"

    def test_best_action_empty_legal_actions(self):
        store = PolicyStore()
        best = store.best_action("s", [])
        assert best == ""

    def test_best_action_tie_breaks_by_insertion_order(self):
        store = PolicyStore()
        store.update("s", "a1", reward=0.5)
        store.update("s", "a2", reward=0.5)
        # Both have same Q, max returns first encountered
        best = store.best_action("s", ["a1", "a2"])
        assert best == "a1"

    def test_legal_actions_filters_by_threshold(self):
        store = PolicyStore()
        store.update("s", "a1", reward=0.1)
        store.update("s", "a2", reward=0.9)
        legal = store.legal_actions("s", ["a1", "a2"], threshold=0.5)
        assert "a1" not in legal
        assert "a2" in legal


class TestPolicyStoreBatch:
    """Batch operations."""

    def test_update_batch(self):
        store = PolicyStore(lr=0.1)
        transitions = [
            {"state": "s1", "action": "a1", "reward": 1.0},
            {"state": "s2", "action": "a2", "reward": 0.8},
        ]
        count = store.update_batch(transitions)
        assert count == 2
        assert store.value("s1", "a1") > 0.5
        assert store.value("s2", "a2") > 0.5

    def test_apply_delta(self):
        store = PolicyStore()
        store.apply_delta({("s1", "a1"): 0.9, ("s2", "a2"): 0.1})
        assert store.value("s1", "a1") == 0.9
        assert store.value("s2", "a2") == 0.1


class TestPolicyStoreReplay:
    """Replay buffer operations."""

    def test_replay_sample_size(self):
        store = PolicyStore()
        for i in range(10):
            store.update(f"s{i}", f"a{i}", reward=float(i))
        sample = store.replay_sample(batch_size=5)
        assert len(sample) == 5

    def test_replay_sample_respects_buffer_size(self):
        store = PolicyStore(replay_capacity=5)
        for i in range(10):
            store.update(f"s{i}", f"a{i}", reward=float(i))
        assert store.replay_size == 5

    def test_replay_update_returns_stats(self):
        store = PolicyStore()
        for i in range(5):
            store.update(f"s{i}", f"a{i}", reward=float(i))
        result = store.replay_update(batch_size=3)
        assert result["updated"] == 3
        assert result["avg_td_error"] >= 0


class TestPolicyStorePersistence:
    """Save / load / snapshot / restore."""

    def test_snapshot_restore_roundtrip(self):
        store = PolicyStore()
        store.update("s1", "a1", reward=0.8)
        store.update("s2", "a2", reward=0.3)
        snap = store.snapshot()
        store2 = PolicyStore()
        store2.restore(snap)
        assert store2.value("s1", "a1") == store.value("s1", "a1")
        assert store2.value("s2", "a2") == store.value("s2", "a2")

    def test_save_load_roundtrip(self):
        store = PolicyStore()
        store.update("s1", "a1", reward=0.7)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "policy.json"
            store.save(path)
            store2 = PolicyStore()
            store2.load(path)
            assert store2.value("s1", "a1") == store.value("s1", "a1")

    def test_load_nonexistent_file_is_noop(self):
        store = PolicyStore()
        store.load("/nonexistent/path.json")
        assert store.size == 0


class TestPolicyStoreIntrospection:
    """Summary and properties."""

    def test_summary(self):
        store = PolicyStore(lr=0.2, discount=0.8)
        store.update("s1", "a1", reward=1.0)
        summary = store.summary()
        assert summary["size"] == 1
        assert summary["learning_rate"] == 0.2
        assert summary["discount_factor"] == 0.8
        assert summary["update_count"] == 1
        assert summary["avg_q"] > 0.5

    def test_size(self):
        store = PolicyStore()
        assert store.size == 0
        store.update("s", "a", reward=0.5)
        assert store.size == 1

    def test_values_returns_copy(self):
        store = PolicyStore()
        store.update("s", "a", reward=0.5)
        vals = store.values()
        vals[("s", "a")] = 999.0
        assert store.value("s", "a") != 999.0


class TestPolicyStoreConfig:
    """Environment variable configuration."""

    def test_default_lr_from_env(self, monkeypatch):
        monkeypatch.setenv("POLICY_LEARNING_RATE", "0.25")
        store = PolicyStore()
        assert store.learning_rate == 0.25

    def test_default_discount_from_env(self, monkeypatch):
        monkeypatch.setenv("POLICY_DISCOUNT_FACTOR", "0.8")
        store = PolicyStore()
        assert store.discount_factor == 0.8

    def test_constructor_overrides_env(self, monkeypatch):
        monkeypatch.setenv("POLICY_LEARNING_RATE", "0.25")
        store = PolicyStore(lr=0.5)
        assert store.learning_rate == 0.5


class TestPolicyStoreConcurrency:
    """Thread safety."""

    def test_concurrent_updates(self):
        import threading

        store = PolicyStore()
        errors = []

        def update_worker(worker_id):
            try:
                for i in range(100):
                    store.update(f"s{worker_id}", f"a{i}", reward=float(i) / 100)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=update_worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert store.size > 0


class TestPolicyStoreMatchesQTable:
    """Phase 1 validation: PolicyStore produces identical Q-values to QTable.

    This test mirrors the QTable's Bellman update logic to ensure the
    standalone PolicyStore is behaviorally equivalent.
    """

    def test_terminal_update_matches_qtable(self):
        """QTable.update(agent, reward) with no successor = terminal update."""
        lr = 0.1
        store = PolicyStore(lr=lr, discount=0.95)
        # QTable: old = 0.5, new = old + lr * (reward - old)
        store.update("agent", "action", reward=0.8)
        expected = 0.5 + lr * (0.8 - 0.5)
        assert abs(store.value("agent", "action") - expected) < 1e-6

    def test_td_update_matches_qtable(self):
        """QTable.update_with_replay(agent, reward, next_agent) with successor."""
        lr = 0.1
        gamma = 0.95
        store = PolicyStore(lr=lr, discount=gamma)
        # First: learn next_agent's Q
        store.update("next", "a_next", reward=1.0)
        next_q = store.value("next", "a_next")
        # Then: update agent with bootstrap
        store.update("agent", "a_agent", reward=0.5, next_state="next")
        # QTable logic: td_target = reward + gamma * Q(next) = 0.5 + 0.95 * next_q
        td_target = 0.5 + gamma * next_q
        expected = 0.5 + lr * (td_target - 0.5)
        assert abs(store.value("agent", "a_agent") - expected) < 1e-6

    def test_snapshot_format_matches_qtable(self):
        """Snapshot keys are "state|action" strings, matching QTable's agent-key format."""
        store = PolicyStore()
        store.update("agent1", "transition", reward=0.9)
        snap = store.snapshot()
        assert "agent1|transition" in snap
        assert snap["agent1|transition"] > 0.5


class TestPhase2AuthorityFlip:
    """Phase 2 validation: QTable reads from PolicyStore, writes to PolicyStore.

    After the authority flip, QTable._read_q() returns PolicyStore values,
    and QTable._write_q() updates PolicyStore as primary + mirrors to tensor.
    """

    def test_qtable_reads_from_policy_store(self):
        from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
        from src.monkey_brain.kernel.graph_manager import QTable

        tensor = SparseTransitionTensor()
        qt = QTable(tensor)

        # Write directly to PolicyStore
        qt.policy_store.update("agent1", "__self__", reward=0.9)
        # QTable should read from PolicyStore
        assert qt.get("agent1") == qt.policy_store.value("agent1", "__self__")

    def test_qtable_writes_to_policy_store(self):
        from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
        from src.monkey_brain.kernel.graph_manager import QTable

        tensor = SparseTransitionTensor()
        qt = QTable(tensor)

        # Write via QTable
        qt.update("agent1", reward=0.8)
        # PolicyStore should have the value
        ps_q = qt.policy_store.value("agent1", "__self__")
        assert ps_q > 0.5

    def test_qtable_mirrors_to_tensor(self):
        from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
        from src.monkey_brain.kernel.graph_manager import QTable

        tensor = SparseTransitionTensor()
        qt = QTable(tensor)

        # Write via QTable
        qt.update("agent1", reward=0.8)
        # Tensor self-loop should also have the value (mirror)
        i = tensor.intern("agent1")
        cell = tensor._cells.get((i, i))
        assert cell is not None
        assert cell.q > 0.5

    def test_consistency_check(self):
        from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
        from src.monkey_brain.kernel.graph_manager import QTable

        tensor = SparseTransitionTensor()
        qt = QTable(tensor)

        qt.update("agent1", reward=0.8)
        qt.update("agent2", reward=0.3)
        result = qt.verify_consistency()
        assert result["consistent"]

    def test_snapshot_returns_policy_store_values(self):
        from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
        from src.monkey_brain.kernel.graph_manager import QTable

        tensor = SparseTransitionTensor()
        qt = QTable(tensor)

        qt.update("agent1", reward=0.8)
        snap = qt.snapshot()
        # Should contain PolicyStore format keys
        assert "agent1|__self__" in snap

    def test_restore_populates_both(self):
        from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
        from src.monkey_brain.kernel.graph_manager import QTable

        tensor = SparseTransitionTensor()
        qt = QTable(tensor)

        qt.restore({"agent1|__self__": 0.9, "agent2|__self__": 0.1})
        # PolicyStore should have values
        assert qt.policy_store.value("agent1", "__self__") == 0.9
        # Tensor should also have mirrored values
        i1 = tensor.intern("agent1")
        assert tensor._cells.get((i1, i1)).q == 0.9


class TestPolicyStoreOwnershipEnforcement:
    """Per-Actor CognitiveOS Isolation refactor: the (state, action) key
    shape has no actor_id component, so isolation has always depended on
    every real caller constructing its own instance — never structurally
    enforced. owner_id/bind_owner() close that gap: rebinding one
    instance to a second, different actor now fails loudly instead of
    silently mixing two actors' Q-values."""

    def test_owner_id_set_at_construction(self):
        store = PolicyStore(owner_id="alice")
        assert store.owner_id == "alice"

    def test_default_owner_id_is_none_backward_compatible(self):
        store = PolicyStore()
        assert store.owner_id is None

    def test_bind_owner_sets_unbound_store(self):
        store = PolicyStore()
        store.bind_owner("alice")
        assert store.owner_id == "alice"

    def test_rebind_to_same_owner_is_a_noop(self):
        store = PolicyStore(owner_id="alice")
        store.bind_owner("alice")  # must not raise
        assert store.owner_id == "alice"

    def test_rebind_to_a_different_owner_raises(self):
        store = PolicyStore(owner_id="alice")
        with pytest.raises(RuntimeError, match="alice"):
            store.bind_owner("bob")
        assert store.owner_id == "alice"  # unchanged after the rejected rebind

    def test_real_actor_construction_sites_bind_owner_id(self):
        """ActorBelief and CognitiveActor — the two confirmed live,
        per-actor construction sites — must actually pass owner_id
        through, not just have the parameter available unused."""
        from src.monkey_brain.kernel.compile.actor_belief import ActorBelief

        belief = ActorBelief("owner-check-actor")
        assert belief._policy_store.owner_id == "owner-check-actor"
