"""Tests for Phase 4: ActionOperator and ActionLegality.

Verifies that:
1. Actions are sparse masks over the world tensor
2. Actions never duplicate transitions
3. Actions filter transitions correctly
4. Legal actions are computed from capabilities, constraints, and world state
5. A · W composition still works with action operators
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor, Feature
from src.monkey_brain.kernel.compile.action_operator import (
    ActionOperator,
    ActionLegality,
)


def _world_with_transitions() -> SparseTransitionTensor:
    """Create a world tensor with known transitions for testing."""
    t = SparseTransitionTensor()
    # s1 → s2 (prob ~0.67), s1 → s3 (prob ~0.33)
    t.observe("s1", "s2", domain="d1")
    t.observe("s1", "s2", domain="d1")
    t.observe("s1", "s3", domain="d1")
    # s2 → s4
    t.observe("s2", "s4", domain="d1")
    # s3 → s4
    t.observe("s3", "s4", domain="d1")
    return t


class TestActionOperator:
    """ActionOperator is a sparse mask over the world tensor."""

    def test_enable_and_apply(self):
        world = _world_with_transitions()
        op = ActionOperator("query", world)
        op.enable("s1", "s2")
        op.enable("s1", "s3")

        result = op.apply("s1")
        assert "s2" in result
        assert "s3" in result
        assert abs(sum(result.values()) - 1.0) < 1e-6

    def test_apply_respects_mask_weights(self):
        world = _world_with_transitions()
        op = ActionOperator("query", world)
        op.enable("s1", "s2", weight=2.0)
        op.enable("s1", "s3", weight=1.0)

        result = op.apply("s1")
        assert result["s2"] > result["s3"]

    def test_apply_filters_by_mask(self):
        world = _world_with_transitions()
        op = ActionOperator("query", world)
        # Only enable s1 → s2, not s1 → s3
        op.enable("s1", "s2")

        result = op.apply("s1")
        assert "s2" in result
        assert "s3" not in result

    def test_apply_returns_empty_for_unknown_state(self):
        world = _world_with_transitions()
        op = ActionOperator("query", world)
        op.enable("s1", "s2")

        result = op.apply("s99")
        assert result == {}

    def test_disable_transition(self):
        world = _world_with_transitions()
        op = ActionOperator("query", world)
        op.enable("s1", "s2")
        op.enable("s1", "s3")
        op.disable("s1", "s3")

        result = op.apply("s1")
        assert "s2" in result
        assert "s3" not in result

    def test_is_enabled(self):
        world = _world_with_transitions()
        op = ActionOperator("query", world)
        op.enable("s1", "s2")

        assert op.is_enabled("s1", "s2")
        assert not op.is_enabled("s1", "s3")

    def test_transitions_list(self):
        world = _world_with_transitions()
        op = ActionOperator("query", world)
        op.enable("s1", "s2", weight=1.0)
        op.enable("s1", "s3", weight=0.5)

        transitions = op.transitions()
        assert len(transitions) == 2
        assert ("s1", "s2", 1.0) in transitions
        assert ("s1", "s3", 0.5) in transitions

    def test_size(self):
        world = _world_with_transitions()
        op = ActionOperator("query", world)
        assert op.size == 0
        op.enable("s1", "s2")
        assert op.size == 1

    def test_summary(self):
        world = _world_with_transitions()
        op = ActionOperator("query", world)
        op.enable("s1", "s2")
        op.enable("s1", "s3")
        summary = op.summary()
        assert summary["action"] == "query"
        assert summary["transitions"] == 2

    def test_action_never_duplicates_transitions(self):
        """Actions reference existing transitions, never duplicate them."""
        world = _world_with_transitions()
        op = ActionOperator("query", world)
        op.enable("s1", "s2")

        # The world tensor should be unchanged
        from src.monkey_brain.kernel.compile.tensor import Feature

        world_freq = world.feature("s1", "s2", Feature.FREQUENCY)
        assert world_freq == 2.0  # Original frequency preserved

    def test_multiple_actions_over_same_world(self):
        """Multiple actions can mask the same world tensor independently."""
        world = _world_with_transitions()
        op1 = ActionOperator("query", world)
        op1.enable("s1", "s2")

        op2 = ActionOperator("execute", world)
        op2.enable("s1", "s3")

        r1 = op1.apply("s1")
        r2 = op2.apply("s1")

        assert "s2" in r1 and "s3" not in r1
        assert "s3" in r2 and "s2" not in r2


class TestActionLegality:
    """ActionLegality computes legal actions from capabilities, constraints, and world."""

    def test_all_actions_legal_without_constraints(self):
        world = _world_with_transitions()
        ops = {
            "query": ActionOperator("query", world),
            "execute": ActionOperator("execute", world),
        }
        ops["query"].enable("s1", "s2")
        ops["execute"].enable("s1", "s3")

        legality = ActionLegality(world)
        legal = legality.compute("s1", action_operators=ops)
        assert "query" in legal
        assert "execute" in legal

    def test_capabilities_filter(self):
        world = _world_with_transitions()
        ops = {
            "query": ActionOperator("query", world),
            "execute": ActionOperator("execute", world),
        }
        ops["query"].enable("s1", "s2")
        ops["execute"].enable("s1", "s3")

        legality = ActionLegality(world)
        legal = legality.compute("s1", capabilities={"query"}, action_operators=ops)
        assert "query" in legal
        assert "execute" not in legal

    def test_no_actions_for_unknown_state(self):
        world = _world_with_transitions()
        ops = {"query": ActionOperator("query", world)}
        ops["query"].enable("s1", "s2")

        legality = ActionLegality(world)
        legal = legality.compute("s99", action_operators=ops)
        assert legal == []

    def test_domain_constraint(self):
        world = _world_with_transitions()
        ops = {"query": ActionOperator("query", world)}
        ops["query"].enable("s1", "s2")

        legality = ActionLegality(world)
        # s1 is in domain "d1", which is allowed
        legal = legality.compute("s1", constraints={"allowed_domains": {"d1"}}, action_operators=ops)
        assert "query" in legal

        # Domain not allowed
        legal = legality.compute("s1", constraints={"allowed_domains": {"d2"}}, action_operators=ops)
        assert legal == []

    def test_forbidden_state_constraint(self):
        world = _world_with_transitions()
        ops = {"query": ActionOperator("query", world)}
        ops["query"].enable("s1", "s2")

        legality = ActionLegality(world)
        legal = legality.compute("s1", constraints={"forbidden_states": {"s1"}}, action_operators=ops)
        assert legal == []

    def test_fallback_to_world_successors(self):
        """When no action operators are provided, use world successors."""
        world = _world_with_transitions()
        legality = ActionLegality(world)
        legal = legality.compute("s1")
        # Should return destinations reachable from s1
        assert "s2" in legal or "s3" in legal


class TestActionOperatorComposition:
    """ActionOperator integrates with A · W composition."""

    def test_action_operator_as_mask(self):
        """ActionOperator filters the world tensor like a mask."""
        world = _world_with_transitions()
        op = ActionOperator("query", world)
        op.enable("s1", "s2")

        # Apply the action
        result = op.apply("s1")

        # Should contain s2 (normalized to 1.0 since it's the only enabled transition)
        assert "s2" in result
        assert abs(result["s2"] - 1.0) < 1e-6  # Only one transition, normalized to 1.0

    def test_compose_action_with_world_operator(self):
        """A · W composition: action operator applied to world produces effect."""
        from src.monkey_brain.kernel.compile.actor import ActorModel

        world = _world_with_transitions()
        op = ActionOperator("query", world)
        op.enable("s1", "s2")

        # Build ActorModel from action operator
        model = ActorModel("test", world)
        for src, dst, weight in op.transitions():
            model.record("query", src, weight=weight)

        # Compose with world operator
        from src.monkey_brain.kernel.compile.compiler import GraphCompiler
        from src.monkey_brain.kernel.compile.types import SemanticGraphSnapshot

        nodes = [{"id": s, "name": s} for s in world.states()]
        edges = [{"from": s, "to": d} for (s, d) in world]
        strengths = {(s, d): world.feature(s, d, Feature.PROBABILITY) for (s, d) in world}
        sg = SemanticGraphSnapshot(nodes=nodes, edges=edges, strengths=strengths)
        compiler = GraphCompiler()
        compiled = compiler.compile(sg)

        effect = model.compose(compiled)
        assert "query" in effect.actions()
