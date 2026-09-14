"""Integration test: full cognitive architecture end-to-end.

Wires together:
    WorldLearner → World Tensor
    PolicyLearner → PolicyStore
    ActionOperator → Legal actions
    Comparator → Hierarchical losses
    Actor → Belief + Policy
"""

from __future__ import annotations

import pytest

from monkey_brain.kernel.cognitive_engine import CognitiveArchitecture
from src.monkey_brain.kernel.compile.tensor import Feature
from src.monkey_brain.kernel.compile.action_operator import ActionOperator
from src.monkey_brain.kernel.comparator_runtime import ComparatorRuntime


class TestCognitiveArchitectureEndToEnd:
    """Full integration: World → Actors → Learning → Comparison."""

    def test_create_architecture(self):
        arch = CognitiveArchitecture()
        summary = arch.summary()
        assert summary["world_states"] == 0
        assert summary["actors"] == 0

    def test_world_learner_proposes_observations(self):
        arch = CognitiveArchitecture()

        # Propose observations
        assert arch.propose_observation("s1", "s2", origin="sensor_a") is True
        assert arch.propose_observation("s2", "s3", origin="sensor_b") is True

        # World tensor updated
        assert arch.world.nnz() == 2
        assert arch.world_learner.update_count == 2

    def test_actor_creates_with_belief_and_policy(self):
        arch = CognitiveArchitecture()
        arch.propose_observation("s1", "s2")

        actor = arch.create_actor("operator_1")
        assert actor["id"] == "operator_1"
        assert actor["belief"].nnz() == 0  # belief starts empty
        assert actor["policy"].size == 0

    def test_actor_observes_folds_into_belief(self):
        arch = CognitiveArchitecture()
        arch.propose_observation("s1", "s2")
        arch.propose_observation("s2", "s3")

        arch.create_actor("operator_1")
        arch.actor_observe("operator_1", "s1", "s2")

        actor = arch.get_actor("operator_1")
        assert actor["belief"].nnz() == 1

    def test_actor_learn_updates_policy(self):
        arch = CognitiveArchitecture()
        arch.create_actor("operator_1")

        metrics = arch.actor_learn("operator_1", "s1", "action_1", reward=0.8)
        assert metrics["td_error"] > 0
        assert metrics["old_q"] == 0.5
        assert metrics["new_q"] > 0.5

    def test_multiple_actors_have_isolated_policies(self):
        arch = CognitiveArchitecture()
        arch.create_actor("actor_a")
        arch.create_actor("actor_b")

        arch.actor_learn("actor_a", "s1", "monitor", reward=0.9)
        arch.actor_learn("actor_b", "s1", "diagnose", reward=0.3)

        # Policies are isolated (Bellman update: new_q = 0.5 + 0.1*(reward - 0.5))
        # actor_a: monitor = 0.5 + 0.1*(0.9 - 0.5) = 0.54
        # actor_b: diagnose = 0.5 + 0.1*(0.3 - 0.5) = 0.48
        assert arch.get_actor("actor_a")["policy"].value("s1", "monitor") > 0.5
        assert arch.get_actor("actor_b")["policy"].value("s1", "monitor") == 0.5  # neutral
        assert arch.get_actor("actor_b")["policy"].value("s1", "diagnose") < 0.5  # updated but below prior
        assert arch.get_actor("actor_a")["policy"].value("s1", "diagnose") == 0.5  # neutral

    def test_action_operators_register_and_compute_legal(self):
        arch = CognitiveArchitecture()
        arch.propose_observation("s1", "s2")
        arch.propose_observation("s1", "s3")

        # Register actions
        op1 = arch.register_action("query")
        op1.enable("s1", "s2")
        op1.enable("s1", "s3")

        op2 = arch.register_action("execute")
        op2.enable("s1", "s2")

        # Compute legal actions
        legal = arch.legal_actions("s1")
        assert "query" in legal
        assert "execute" in legal

    def test_legal_actions_filter_by_capabilities(self):
        arch = CognitiveArchitecture()
        arch.propose_observation("s1", "s2")
        arch.propose_observation("s1", "s3")

        op1 = arch.register_action("query")
        op1.enable("s1", "s2")
        op1.enable("s1", "s3")

        op2 = arch.register_action("execute")
        op2.enable("s1", "s2")

        # Only query is legal
        legal = arch.legal_actions("s1", capabilities={"query"})
        assert "query" in legal
        assert "execute" not in legal

    def test_comparator_emits_hierarchical_losses(self):
        arch = CognitiveArchitecture()

        sim = {
            "graph_id": "sim1",
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "edges": [{"from": "n1", "to": "n2"}],
            "execution_order": [["n1"], ["n2"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"answer": "ok"},
                    "predicted_reward": 0.8,
                    "grounding_score": 0.9,
                    "operations": ["a", "b"],
                    "events": ["done"],
                    "artifacts": ["out"],
                }
            },
        }
        exec_result = {
            "graph_id": "sim1",
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "edges": [{"from": "n1", "to": "n2"}],
            "execution_order": [["n1"], ["n2"]],
            "state": {"answer": "ok"},
            "operations": ["a", "b"],
            "events": ["done"],
            "artifacts": ["out"],
            "reward": 0.8,
            "confidence": 0.9,
        }

        import asyncio

        cmp = ComparatorRuntime()
        result = asyncio.run(cmp.compare(sim, exec_result))
        d = result.to_dict()

        # All hierarchical losses present
        assert "topology_loss" in d
        assert "epistemic_loss" in d
        assert "world_loss" in d
        assert "policy_loss" in d
        assert "actor_loss" in d

        # Invariants
        assert abs(d["world_loss"] - (d["topology_loss"] + d["epistemic_loss"])) < 0.01 or d["world_loss"] == 1.0
        assert abs(d["actor_loss"] - (d["world_loss"] + d["policy_loss"])) < 0.01 or d["actor_loss"] == 1.0

    def test_full_cognitive_cycle(self):
        """Full cycle: observe → learn → compare → learn."""
        arch = CognitiveArchitecture()

        # 1. World has transitions
        arch.propose_observation("inventory_10", "inventory_9", domain="manufacturing")
        arch.propose_observation("inventory_9", "inventory_8", domain="manufacturing")
        arch.propose_observation("daylight", "night", domain="environment")

        # 2. Actor discovers and learns
        arch.create_actor("operator_1")
        arch.actor_observe("operator_1", "inventory_10", "inventory_9")
        arch.actor_learn("operator_1", "inventory_10", "monitor", reward=0.8)
        arch.actor_learn(
            "operator_1",
            "inventory_9",
            "restock",
            reward=0.6,
            next_state="inventory_10",
        )

        # 3. Actor's policy has learned
        actor = arch.get_actor("operator_1")
        assert actor["policy"].value("inventory_10", "monitor") > 0.5
        assert actor["policy"].value("inventory_9", "restock") > 0.5

        # 4. Legal actions computed
        legal = arch.legal_actions("inventory_10", actor_id="operator_1")
        assert isinstance(legal, list)

        # 5. Summary
        summary = arch.summary()
        assert summary["world_transitions"] == 3
        assert summary["actors"] == 1
        assert summary["actor_ids"] == ["operator_1"]


class TestPartialObservability:
    """Different actors see different parts of the same world."""

    def test_actors_have_different_beliefs(self):
        arch = CognitiveArchitecture()

        # World has many transitions
        for i in range(5):
            arch.propose_observation(f"s{i}", f"s{i + 1}")

        # Actor A only observes first half
        arch.create_actor("actor_a")
        arch.actor_observe("actor_a", "s0", "s1")
        arch.actor_observe("actor_a", "s1", "s2")

        # Actor B only observes second half
        arch.create_actor("actor_b")
        arch.actor_observe("actor_b", "s3", "s4")
        arch.actor_observe("actor_b", "s4", "s5")

        # Different beliefs
        assert arch.get_actor("actor_a")["belief"].nnz() == 2
        assert arch.get_actor("actor_b")["belief"].nnz() == 2

        # Same world
        assert arch.world.nnz() == 5

    def test_isolated_policies(self):
        arch = CognitiveArchitecture()

        arch.create_actor("actor_a")
        arch.create_actor("actor_b")

        # Different experiences → different policies
        arch.actor_learn("actor_a", "s1", "action_1", reward=0.9)
        arch.actor_learn("actor_b", "s1", "action_2", reward=0.3)

        # Policies don't see each other (Bellman: new_q = 0.5 + 0.1*(reward - 0.5))
        assert arch.get_actor("actor_a")["policy"].value("s1", "action_1") > 0.5
        assert arch.get_actor("actor_b")["policy"].value("s1", "action_1") == 0.5  # neutral
        assert arch.get_actor("actor_b")["policy"].value("s1", "action_2") < 0.5  # below prior
        assert arch.get_actor("actor_a")["policy"].value("s1", "action_2") == 0.5  # neutral
