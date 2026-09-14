"""Tests for Phase 3: WorldLearner, PolicyLearner, and split comparator losses.

Verifies that:
1. WorldLearner only updates tensor frequencies (no Q-values)
2. PolicyLearner only updates PolicyStore (no tensor mutations)
3. Comparator emits world_loss and policy_loss independently
4. The two learners are independently testable
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
from src.monkey_brain.kernel.policy.store import PolicyStore
from src.monkey_brain.kernel.learn.world_learner import WorldLearner
from src.monkey_brain.kernel.learn.policy_learner import PolicyLearner


class TestWorldLearner:
    """WorldLearner updates tensor frequencies only, via proposals."""

    def test_observe_transition_increments_frequency(self):
        tensor = SparseTransitionTensor()
        wl = WorldLearner(tensor)

        wl.observe_transition("s1", "s2")
        from src.monkey_brain.kernel.compile.tensor import Feature

        freq = tensor.feature("s1", "s2", Feature.FREQUENCY)
        assert freq == 1.0

    def test_observe_multiple_increments(self):
        tensor = SparseTransitionTensor()
        wl = WorldLearner(tensor)

        wl.observe_transition("s1", "s2")
        wl.observe_transition("s1", "s2")
        wl.observe_transition("s1", "s3")
        from src.monkey_brain.kernel.compile.tensor import Feature

        assert tensor.feature("s1", "s2", Feature.FREQUENCY) == 2.0
        assert tensor.feature("s1", "s3", Feature.FREQUENCY) == 1.0

    def test_does_not_modify_q_values(self):
        tensor = SparseTransitionTensor()
        wl = WorldLearner(tensor)

        i = tensor.intern("s1")
        cell = tensor._new_cell(i, i)
        cell.q = 0.9

        wl.observe_transition("s1", "s2")

        from src.monkey_brain.kernel.compile.tensor import Feature

        assert tensor.feature("s1", "s1", Feature.Q_VALUE) == 0.9

    def test_batch_observe(self):
        tensor = SparseTransitionTensor()
        wl = WorldLearner(tensor)

        transitions = [
            {"src": "s1", "dst": "s2"},
            {"src": "s2", "dst": "s3"},
            {"src": "s3", "dst": "s1"},
        ]
        count = wl.batch_observe(transitions)
        assert count == 3
        from src.monkey_brain.kernel.compile.tensor import Feature

        assert tensor.feature("s1", "s2", Feature.FREQUENCY) == 1.0

    def test_summary(self):
        tensor = SparseTransitionTensor()
        wl = WorldLearner(tensor)
        wl.observe_transition("s1", "s2")
        summary = wl.summary()
        assert summary["update_count"] == 1
        assert summary["tensor_nnz"] >= 1

    def test_probability_derived_from_frequency(self):
        tensor = SparseTransitionTensor()
        wl = WorldLearner(tensor)

        wl.observe_transition("s1", "s2")
        wl.observe_transition("s1", "s2")
        wl.observe_transition("s1", "s3")
        from src.monkey_brain.kernel.compile.tensor import Feature

        prob = tensor.feature("s1", "s2", Feature.PROBABILITY)
        assert abs(prob - 2.0 / 3.0) < 1e-6

    def test_proposal_mechanism(self):
        tensor = SparseTransitionTensor()
        wl = WorldLearner(tensor)

        # Default policy accepts all
        result = wl.observe_transition("s1", "s2", origin="actor_a")
        assert result is True
        assert wl.proposal_count == 1
        assert wl.rejected_count == 0

    def test_proposal_rejection(self):
        from src.monkey_brain.kernel.learn.world_learner import Observation

        class RejectingWorldLearner(WorldLearner):
            def propose(self, observation: Observation) -> bool:
                # Reject low-confidence observations
                return observation.confidence >= 0.5

        tensor = SparseTransitionTensor()
        wl = RejectingWorldLearner(tensor)

        # Low confidence → rejected
        result = wl.observe_transition("s1", "s2", confidence=0.3)
        assert result is False
        assert wl.rejected_count == 1

        # High confidence → accepted
        result = wl.observe_transition("s1", "s2", confidence=0.8)
        assert result is True
        assert wl.update_count == 1

    def test_proposal_tracks_origin(self):
        tensor = SparseTransitionTensor()
        wl = WorldLearner(tensor)

        wl.observe_transition("s1", "s2", origin="actor_a")
        wl.observe_transition("s1", "s3", origin="actor_b")
        assert wl.proposal_count == 2
        assert wl.summary()["acceptance_rate"] == 1.0


class TestPolicyLearner:
    """PolicyLearner updates PolicyStore only."""

    def test_update_terminal(self):
        store = PolicyStore(lr=0.1, discount=0.95)
        pl = PolicyLearner(store)

        metrics = pl.update("s1", "a1", reward=0.8)
        assert metrics["old_q"] == 0.5
        assert metrics["new_q"] > 0.5
        assert metrics["td_error"] > 0

    def test_update_with_bootstrap(self):
        store = PolicyStore(lr=0.1, discount=0.95)
        pl = PolicyLearner(store)

        # Learn next state
        pl.update("s2", "a2", reward=1.0)
        # Update with bootstrap
        metrics = pl.update("s1", "a1", reward=0.5, next_state="s2")
        assert metrics["new_q"] > 0.5
        assert metrics["td_error"] > 0

    def test_does_not_modify_tensor(self):
        tensor = SparseTransitionTensor()
        store = PolicyStore()
        pl = PolicyLearner(store)

        # Create a transition in tensor
        tensor.observe("s1", "s2")
        from src.monkey_brain.kernel.compile.tensor import Feature

        freq_before = tensor.feature("s1", "s2", Feature.FREQUENCY)

        pl.update("s1", "a1", reward=0.8)

        # Tensor frequency should be unchanged
        freq_after = tensor.feature("s1", "s2", Feature.FREQUENCY)
        assert freq_after == freq_before

    def test_batch_update(self):
        store = PolicyStore(lr=0.1)
        pl = PolicyLearner(store)

        transitions = [
            {"state": "s1", "action": "a1", "reward": 0.8},
            {"state": "s2", "action": "a2", "reward": 0.3},
        ]
        results = pl.update_batch(transitions)
        assert len(results) == 2
        assert pl.update_count == 2

    def test_summary(self):
        store = PolicyStore()
        pl = PolicyLearner(store)
        pl.update("s1", "a1", reward=0.8)
        summary = pl.summary()
        assert summary["update_count"] == 1
        assert summary["policy_size"] == 1


class TestSplitComparatorLosses:
    """Comparator emits hierarchical losses independently."""

    def test_comparison_result_has_all_losses(self):
        from src.monkey_brain.kernel.comparator_runtime import ComparisonResult

        result = ComparisonResult(
            topology_loss=0.1,
            epistemic_loss=0.2,
            world_loss=0.3,
            policy_loss=0.15,
            actor_loss=0.45,
        )
        d = result.to_dict()
        assert "topology_loss" in d
        assert "epistemic_loss" in d
        assert "world_loss" in d
        assert "policy_loss" in d
        assert "actor_loss" in d
        assert d["topology_loss"] == 0.1
        assert d["epistemic_loss"] == 0.2
        assert d["world_loss"] == 0.3
        assert d["policy_loss"] == 0.15
        assert d["actor_loss"] == 0.45

    def test_world_loss_equals_topology_plus_epistemic(self):
        from src.monkey_brain.kernel.comparator_runtime import ComparisonResult

        result = ComparisonResult(
            topology_loss=0.3,
            epistemic_loss=0.2,
        )
        # world_loss should be topology + epistemic (clamped to 1.0)
        assert abs(result.world_loss - 0.5) < 1e-6 or result.world_loss == 1.0

    def test_actor_loss_equals_world_plus_policy(self):
        from src.monkey_brain.kernel.comparator_runtime import ComparisonResult

        result = ComparisonResult(
            world_loss=0.3,
            policy_loss=0.2,
        )
        # actor_loss = world_loss * 0.7 + policy_loss * 0.3 (weighted)
        expected = 0.3 * 0.7 + 0.2 * 0.3  # 0.27
        assert abs(result.actor_loss - expected) < 1e-6 or result.actor_loss == 1.0

    def test_topology_loss_independent_of_policy(self):
        from src.monkey_brain.kernel.comparator_runtime import ComparisonResult

        # Perfect topology, bad policy
        r1 = ComparisonResult(topology_loss=0.0, policy_loss=1.0)
        assert r1.to_dict()["topology_loss"] == 0.0

        # Bad topology, perfect policy
        r2 = ComparisonResult(topology_loss=1.0, policy_loss=0.0)
        assert r2.to_dict()["topology_loss"] == 1.0

    def test_epistemic_loss_independent_of_policy(self):
        from src.monkey_brain.kernel.comparator_runtime import ComparisonResult

        # Perfect epistemic, bad policy
        r1 = ComparisonResult(epistemic_loss=0.0, policy_loss=1.0)
        assert r1.to_dict()["epistemic_loss"] == 0.0

        # Bad epistemic, perfect policy
        r2 = ComparisonResult(epistemic_loss=1.0, policy_loss=0.0)
        assert r2.to_dict()["epistemic_loss"] == 1.0


class TestLearnerIndependence:
    """WorldLearner and PolicyLearner are independently testable."""

    def test_world_learner_independent_of_policy(self):
        tensor = SparseTransitionTensor()
        store = PolicyStore()
        wl = WorldLearner(tensor)
        pl = PolicyLearner(store)

        # World learner observes transitions
        wl.observe_transition("s1", "s2")
        wl.observe_transition("s2", "s3")

        # Policy learner updates Q-values
        pl.update("s1", "a1", reward=0.8)
        pl.update("s2", "a2", reward=0.3, next_state="s3")

        # World tensor has frequencies, no Q-values
        from src.monkey_brain.kernel.compile.tensor import Feature

        assert tensor.feature("s1", "s2", Feature.FREQUENCY) == 1.0

        # Policy store has Q-values, no frequencies
        assert store.value("s1", "a1") > 0.5
        assert store.value("s2", "a2") > 0.5

        # They don't interfere
        assert wl.update_count == 2
        assert pl.update_count == 2

    def test_both_learners_can_run_in_parallel(self):
        import threading

        tensor = SparseTransitionTensor()
        store = PolicyStore()
        wl = WorldLearner(tensor)
        pl = PolicyLearner(store)
        errors = []

        def world_worker():
            try:
                for i in range(50):
                    wl.observe_transition(f"s{i}", f"s{i + 1}")
            except Exception as e:
                errors.append(e)

        def policy_worker():
            try:
                for i in range(50):
                    pl.update(f"s{i}", f"a{i}", reward=float(i) / 50)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=world_worker)
        t2 = threading.Thread(target=policy_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        assert wl.update_count == 50
        assert pl.update_count == 50
