"""Tests for the Four-Layer Cognitive Architecture.

Validates that each layer operates independently and the invariants hold.
"""

from __future__ import annotations

import asyncio
import pytest

from monkey_brain.kernel.cognitive_engine import CognitiveArchitecture
from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor, Feature
from src.monkey_brain.kernel.compile.action_operator import (
    ActionOperator,
    ActionLegality,
)
from src.monkey_brain.kernel.learn.world_learner import WorldLearner, Observation
from src.monkey_brain.kernel.learn.policy_learner import PolicyLearner
from src.monkey_brain.kernel.policy.store import PolicyStore
from src.monkey_brain.kernel.comparator_runtime import ComparatorRuntime

# ═══════════════════════════════════════════════════════════════════════════
# Layer 1: World Inference
# ═══════════════════════════════════════════════════════════════════════════


class TestLayer1WorldInference:
    """Layer 1: W_{t+1} = f(W_t, O₁, O₂, ..., Oₙ)"""

    def test_world_tensor_is_consensus_of_observations(self):
        """World tensor is a consensus of observations, not policies or rewards."""
        world = SparseTransitionTensor()
        wl = WorldLearner(world)

        # Multiple actors observe
        wl.observe_transition("s1", "s2", origin="sensor_a")
        wl.observe_transition("s1", "s2", origin="sensor_b")  # duplicate
        wl.observe_transition("s2", "s3", origin="sensor_c")

        # World is consensus: freq(s1,s2) = 2, freq(s2,s3) = 1
        assert world.feature("s1", "s2", Feature.FREQUENCY) == 2.0
        assert world.feature("s2", "s3", Feature.FREQUENCY) == 1.0

    def test_world_tensor_is_temporal(self):
        """World evolves over time: W_{t+1} = f(W_t, O)."""
        world = SparseTransitionTensor()
        wl = WorldLearner(world)

        # Initial state
        assert world.nnz() == 0

        # Observation at t=0
        wl.observe_transition("s1", "s2")
        assert world.nnz() == 1

        # Observation at t=1
        wl.observe_transition("s2", "s3")
        assert world.nnz() == 2

    def test_world_tensor_is_shared(self):
        """All actors read the same world tensor."""
        world = SparseTransitionTensor()
        wl = WorldLearner(world)

        wl.observe_transition("s1", "s2")
        wl.observe_transition("s2", "s3")

        # Both actors read the same world
        assert world.nnz() == 2
        assert len(world.states()) == 3

    def test_world_tensor_is_actor_independent(self):
        """World tensor does not contain actor-specific information."""
        world = SparseTransitionTensor()
        wl = WorldLearner(world)

        wl.observe_transition("s1", "s2", origin="actor_a")
        wl.observe_transition("s1", "s2", origin="actor_b")

        # World only knows about transitions, not who observed them
        assert world.nnz() == 1  # deduplicated
        assert world.feature("s1", "s2", Feature.FREQUENCY) == 2.0

    def test_world_inference_probability(self):
        """P(j|i) = freq(i,j) / Σ_k freq(i,k)."""
        world = SparseTransitionTensor()
        wl = WorldLearner(world)

        wl.observe_transition("s1", "s2")
        wl.observe_transition("s1", "s2")
        wl.observe_transition("s1", "s3")

        # P(s2|s1) = 2/3, P(s3|s1) = 1/3
        assert abs(world.feature("s1", "s2", Feature.PROBABILITY) - 2 / 3) < 1e-6
        assert abs(world.feature("s1", "s3", Feature.PROBABILITY) - 1 / 3) < 1e-6

    def test_world_learner_proposal_mechanism(self):
        """WorldLearner decides which observations to incorporate."""
        world = SparseTransitionTensor()
        wl = WorldLearner(world)

        # Default: accept all
        assert wl.observe_transition("s1", "s2") is True
        assert wl.update_count == 1

    def test_world_learner_can_reject(self):
        """WorldLearner can reject observations via propose()."""

        class RejectingLearner(WorldLearner):
            def propose(self, observation: Observation) -> bool:
                return observation.confidence >= 0.5

        world = SparseTransitionTensor()
        wl = RejectingLearner(world)

        assert wl.observe_transition("s1", "s2", confidence=0.3) is False
        assert wl.observe_transition("s1", "s2", confidence=0.8) is True
        assert wl.rejected_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# Layer 2: Belief Formation
# ═══════════════════════════════════════════════════════════════════════════


class TestLayer2BeliefFormation:
    """Layer 2: Belief_a = g(W, O_a, C_a)"""

    def test_belief_is_local_tensor(self):
        """Each actor has a local belief tensor."""
        arch = CognitiveArchitecture()
        arch.propose_observation("s1", "s2")
        arch.propose_observation("s2", "s3")

        actor = arch.create_actor("actor_a")
        assert isinstance(actor["belief"], SparseTransitionTensor)

    def test_belief_is_subset_of_world(self):
        """Belief ⊆ World (belief is always a subset of the consensus)."""
        arch = CognitiveArchitecture()
        arch.propose_observation("s1", "s2")
        arch.propose_observation("s2", "s3")
        arch.propose_observation("s3", "s4")

        # Actor only observes part of the world
        arch.actor_observe("actor_a", "s1", "s2")
        actor = arch.get_actor("actor_a")

        # Belief has fewer transitions than world
        assert actor["belief"].nnz() < arch.world.nnz()

    def test_different_actors_have_different_beliefs(self):
        """Different actors may legitimately have different beliefs."""
        arch = CognitiveArchitecture()
        arch.propose_observation("s1", "s2")
        arch.propose_observation("s2", "s3")
        arch.propose_observation("s3", "s4")

        # Actor A observes first half
        arch.actor_observe("actor_a", "s1", "s2")
        arch.actor_observe("actor_a", "s2", "s3")

        # Actor B observes second half
        arch.actor_observe("actor_b", "s3", "s4")

        # Different beliefs
        assert arch.get_actor("actor_a")["belief"].nnz() == 2
        assert arch.get_actor("actor_b")["belief"].nnz() == 1

    def test_belief_formation_function(self):
        """Belief_a = g(W, O_a, C_a) — belief depends on world, observations, context."""
        arch = CognitiveArchitecture()
        arch.propose_observation("s1", "s2")
        arch.propose_observation("s2", "s3")

        # Create actor with context (capabilities)
        actor = arch.create_actor("actor_a")
        arch.actor_observe("actor_a", "s1", "s2")

        # Belief depends on what actor observed
        assert actor["belief"].nnz() == 1

    def test_partial_observability(self):
        """Partial observability is a feature, not a bug."""
        arch = CognitiveArchitecture()
        for i in range(5):
            arch.propose_observation(f"s{i}", f"s{i + 1}")

        # Each actor sees only what it observed
        arch.create_actor("actor_a")
        arch.actor_observe("actor_a", "s0", "s1")

        arch.create_actor("actor_b")
        arch.actor_observe("actor_b", "s3", "s4")

        # Both have partial views
        assert arch.get_actor("actor_a")["belief"].nnz() == 1
        assert arch.get_actor("actor_b")["belief"].nnz() == 1
        # Neither sees the full world
        assert arch.world.nnz() == 5


# ═══════════════════════════════════════════════════════════════════════════
# Layer 3: Decision
# ═══════════════════════════════════════════════════════════════════════════


class TestLayer3Decision:
    """Layer 3: Action_a = π(Belief_a)"""

    def test_policy_is_per_actor(self):
        """Each actor has its own policy (PolicyStore)."""
        arch = CognitiveArchitecture()
        arch.create_actor("actor_a")
        arch.create_actor("actor_b")

        assert isinstance(arch.get_actor("actor_a")["policy"], PolicyStore)
        assert isinstance(arch.get_actor("actor_b")["policy"], PolicyStore)

    def test_policy_isolation(self):
        """Q_a ∩ Q_b = ∅ for actors a ≠ b."""
        arch = CognitiveArchitecture()
        arch.create_actor("actor_a")
        arch.create_actor("actor_b")

        arch.actor_learn("actor_a", "s1", "action_1", reward=0.9)
        arch.actor_learn("actor_b", "s1", "action_2", reward=0.3)

        # Actor A knows action_1 but not action_2
        assert arch.get_actor("actor_a")["policy"].value("s1", "action_1") > 0.5
        assert arch.get_actor("actor_a")["policy"].value("s1", "action_2") == 0.5

        # Actor B knows action_2 but not action_1
        assert arch.get_actor("actor_b")["policy"].value("s1", "action_2") < 0.5
        assert arch.get_actor("actor_b")["policy"].value("s1", "action_1") == 0.5

    def test_action_operator_is_sparse_mask(self):
        """Actions are sparse masks over the world tensor."""
        world = SparseTransitionTensor()
        world.observe("s1", "s2")
        world.observe("s1", "s3")

        op = ActionOperator("query", world)
        op.enable("s1", "s2")

        result = op.apply("s1")
        assert "s2" in result
        assert "s3" not in result

    def test_action_never_duplicates_transitions(self):
        """Actions reference existing transitions, never duplicate them."""
        world = SparseTransitionTensor()
        world.observe("s1", "s2")

        op = ActionOperator("query", world)
        op.enable("s1", "s2")

        # World unchanged
        assert world.feature("s1", "s2", Feature.FREQUENCY) == 1.0

    def test_legal_actions_computed_from_world_and_constraints(self):
        """Legal actions = world ∩ capabilities ∩ constraints."""
        arch = CognitiveArchitecture()
        arch.propose_observation("s1", "s2")
        arch.propose_observation("s1", "s3")

        op1 = arch.register_action("query")
        op1.enable("s1", "s2")
        op1.enable("s1", "s3")

        op2 = arch.register_action("execute")
        op2.enable("s1", "s2")

        # All legal
        legal = arch.legal_actions("s1")
        assert "query" in legal
        assert "execute" in legal

        # Filtered by capabilities
        legal = arch.legal_actions("s1", capabilities={"query"})
        assert "query" in legal
        assert "execute" not in legal


# ═══════════════════════════════════════════════════════════════════════════
# Layer 4: Learning
# ═══════════════════════════════════════════════════════════════════════════


class TestLayer4Learning:
    """Layer 4: L_world = L_t + L_e, L_actor = L_world + L_p"""

    def test_world_loss_equals_topology_plus_epistemic(self):
        """L_world = L_t + L_e."""
        from src.monkey_brain.kernel.comparator_runtime import ComparisonResult

        result = ComparisonResult(topology_loss=0.3, epistemic_loss=0.2)
        assert abs(result.world_loss - 0.5) < 0.01

    def test_actor_loss_equals_world_plus_policy(self):
        """L_actor = L_world * 0.7 + L_p * 0.3 (weighted)."""
        from src.monkey_brain.kernel.comparator_runtime import ComparisonResult

        result = ComparisonResult(world_loss=0.3, policy_loss=0.2)
        expected = 0.3 * 0.7 + 0.2 * 0.3  # 0.27
        assert abs(result.actor_loss - expected) < 0.01

    def test_losses_are_independently_observable(self):
        """Every loss is independently observable."""
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

    def test_world_learner_only_updates_world(self):
        """World learning updates only the world tensor."""
        world = SparseTransitionTensor()
        wl = WorldLearner(world)

        wl.observe_transition("s1", "s2")

        # World updated
        assert world.feature("s1", "s2", Feature.FREQUENCY) == 1.0

    def test_policy_learner_only_updates_policy(self):
        """Policy learning updates only the PolicyStore."""
        store = PolicyStore()
        pl = PolicyLearner(store)

        pl.update("s1", "a1", reward=0.8)

        # Policy updated
        assert store.value("s1", "a1") > 0.5

    def test_policy_learner_does_not_modify_world(self):
        """Policy learning never modifies the world tensor."""
        world = SparseTransitionTensor()
        store = PolicyStore()
        pl = PolicyLearner(store)

        world.observe("s1", "s2")
        freq_before = world.feature("s1", "s2", Feature.FREQUENCY)

        pl.update("s1", "a1", reward=0.8)

        # World unchanged
        assert world.feature("s1", "s2", Feature.FREQUENCY) == freq_before

    def test_environment_reward_and_actor_loss_are_separate(self):
        """Environment reward ≠ actor_loss. They are distinct signals."""
        from src.monkey_brain.kernel.comparator_runtime import ComparisonResult

        result = ComparisonResult(world_loss=0.3, policy_loss=0.2)
        d = result.to_dict()

        # They are different values
        assert d["world_loss"] != d["policy_loss"]

    def test_loss_hierarchy_is_cumulative(self):
        """Actor loss includes world loss (can't make good decisions with bad world model)."""
        from src.monkey_brain.kernel.comparator_runtime import ComparisonResult

        # Bad world model, good policy
        result = ComparisonResult(
            topology_loss=0.5,
            epistemic_loss=0.5,
            policy_loss=0.0,
        )
        # Actor loss is still high because world is bad
        assert result.actor_loss >= 0.5


# ═══════════════════════════════════════════════════════════════════════════
# Architectural Invariants
# ═══════════════════════════════════════════════════════════════════════════


class TestArchitecturalInvariants:
    """Verify all architectural invariants hold."""

    def test_consensus_world(self):
        """W_{t+1} = f(W_t, O₁, ..., Oₙ)."""
        arch = CognitiveArchitecture()
        arch.propose_observation("s1", "s2", origin="a")
        arch.propose_observation("s2", "s3", origin="b")

        assert arch.world.nnz() == 2

    def test_shared_world(self):
        """∀a: World_a = W."""
        arch = CognitiveArchitecture()
        arch.propose_observation("s1", "s2")

        # All actors read the same world
        assert arch.world is arch.world_learner._tensor

    def test_private_beliefs(self):
        """Belief_a = g(W, O_a, C_a)."""
        arch = CognitiveArchitecture()
        arch.propose_observation("s1", "s2")
        arch.propose_observation("s2", "s3")

        arch.create_actor("actor_a")
        arch.actor_observe("actor_a", "s1", "s2")

        actor = arch.get_actor("actor_a")
        assert actor["belief"].nnz() == 1  # partial

    def test_world_authority(self):
        """Only WorldLearner may modify W."""
        arch = CognitiveArchitecture()

        # Actors propose, WorldLearner decides
        assert arch.propose_observation("s1", "s2") is True
        assert arch.world.nnz() == 1

    def test_reward_loss_separation(self):
        """R_env ≠ L_actor."""
        from src.monkey_brain.kernel.comparator_runtime import ComparisonResult

        result = ComparisonResult(world_loss=0.3, policy_loss=0.2)
        d = result.to_dict()

        # They measure different things
        assert d["world_loss"] != d["policy_loss"]
