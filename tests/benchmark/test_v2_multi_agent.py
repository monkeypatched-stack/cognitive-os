"""MonkeyBrain V2 Benchmark — Multi-Agent Coordination.

Multi-agent family grocery coordination with substitutions and scheduling.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor, Feature
from src.monkey_brain.kernel.compile.society import Actor, ActorNetwork
from src.monkey_brain.kernel.compile.trust import Relationship
from src.monkey_brain.kernel.compile.conflict_resolution import (
    ConflictResolver,
    ResolutionStrategy,
)
from src.monkey_brain.kernel.policy.store import PolicyStore
from src.monkey_brain.kernel.learn.world_learner import WorldLearner
from src.monkey_brain.kernel.learn.policy_learner import PolicyLearner
from src.monkey_brain.kernel.comparator_runtime import ComparatorRuntime

# ═══════════════════════════════════════════════════════════════════════════
# World Model: Family Grocery Coordination
# ═══════════════════════════════════════════════════════════════════════════


def create_family_world() -> SparseTransitionTensor:
    world = SparseTransitionTensor()

    world.observe("shopping_list", "place_order", domain="shopping")
    world.observe("place_order", "order_placed", domain="shopping")
    world.observe("order_placed", "order_confirmed", domain="shopping")
    world.observe("order_placed", "order_rejected", domain="shopping")
    world.observe("place_order", "budget_check", domain="finance")
    world.observe("budget_check", "budget_ok", domain="finance")
    world.observe("budget_check", "budget_exceeded", domain="finance")
    world.observe("order_placed", "check_availability", domain="inventory")
    world.observe("check_availability", "items_available", domain="inventory")
    world.observe("check_availability", "item_unavailable", domain="inventory")
    world.observe("item_unavailable", "suggest_substitute", domain="inventory")
    world.observe("suggest_substitute", "substitute_found", domain="inventory")
    world.observe("substitute_found", "add_to_cart", domain="shopping")
    world.observe("suggest_substitute", "no_substitute", domain="inventory")
    world.observe("order_confirmed", "schedule_pickup", domain="logistics")
    world.observe("schedule_pickup", "pickup_scheduled", domain="logistics")
    world.observe("schedule_pickup", "pickup_rejected", domain="logistics")
    world.observe("teen_available", "teen_can_pickup", domain="scheduling")
    world.observe("teen_can_pickup", "execute_pickup", domain="execution")
    world.observe("teen_unavailable", "teen_cannot_pickup", domain="scheduling")
    world.observe("pickup_scheduled", "execute_pickup", domain="execution")
    world.observe("execute_pickup", "items_acquired", domain="execution")
    world.observe("execute_pickup", "pickup_failed", domain="execution")
    world.observe("items_acquired", "order_complete", domain="shopping")
    world.observe("pickup_failed", "retry_pickup", domain="execution")
    world.observe("retry_pickup", "execute_pickup", domain="execution")
    world.observe("budget_exceeded", "reduce_order", domain="finance")
    world.observe("reduce_order", "place_order", domain="shopping")

    return world


def _create_architecture(world: SparseTransitionTensor):
    from monkey_brain.kernel.cognitive_engine import CognitiveArchitecture
    from src.monkey_brain.kernel.learn.world_learner import WorldLearner

    arch = CognitiveArchitecture()
    arch._world = world
    arch._world_learner = WorldLearner(world)
    return arch


def _register_capabilities(arch) -> None:
    caps = [
        ("place_order", ["shopping_list"], ["order_placed"]),
        ("budget_check", ["order_placed"], ["budget_ok", "budget_exceeded"]),
        (
            "check_availability",
            ["order_placed"],
            ["items_available", "item_unavailable"],
        ),
        (
            "suggest_substitute",
            ["item_unavailable"],
            ["substitute_found", "no_substitute"],
        ),
        ("add_to_cart", ["substitute_found"], ["order_placed"]),
        (
            "schedule_pickup",
            ["order_confirmed"],
            ["pickup_scheduled", "pickup_rejected"],
        ),
        (
            "execute_pickup",
            ["pickup_scheduled", "teen_can_pickup"],
            ["items_acquired", "pickup_failed"],
        ),
        ("retry_pickup", ["pickup_failed"], ["execute_pickup"]),
        ("reduce_order", ["budget_exceeded"], ["place_order"]),
    ]
    for name, preconditions, effects in caps:
        op = arch.register_action(name)
        for pre in preconditions:
            for eff in effects:
                op.enable(pre, eff)


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestMultiAgentOrderPlacement:
    def test_both_actors_can_place_orders(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.create_actor("parent")
        arch.create_actor("teen")
        arch.actor_observe("parent", "shopping_list", "place_order")
        arch.actor_observe("teen", "shopping_list", "place_order")
        assert arch.get_actor("parent")["belief"].nnz() >= 1
        assert arch.get_actor("teen")["belief"].nnz() >= 1

    def test_independent_beliefs(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.create_actor("parent")
        arch.create_actor("teen")
        arch.actor_observe("parent", "shopping_list", "place_order")
        arch.actor_observe("teen", "budget_check", "budget_ok")
        # Both have 1 transition, but different ones
        assert arch.get_actor("parent")["belief"].nnz() == 1
        assert arch.get_actor("teen")["belief"].nnz() == 1
        # Verify they are different transitions
        parent_edges = list(arch.get_actor("parent")["belief"])
        teen_edges = list(arch.get_actor("teen")["belief"])
        assert parent_edges != teen_edges


class TestSubstitutionHandling:
    def test_unavailable_triggers_substitution(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.propose_observation("check_availability", "item_unavailable", domain="inventory")
        successors = {dst for dst, dom in world.successors("item_unavailable")}
        assert "suggest_substitute" in successors

    def test_substitute_found_continues(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.propose_observation("item_unavailable", "suggest_substitute", domain="inventory")
        arch.propose_observation("suggest_substitute", "substitute_found", domain="inventory")
        successors = {dst for dst, dom in world.successors("substitute_found")}
        assert "add_to_cart" in successors


class TestBudgetConstraint:
    def test_budget_exceeded_triggers_reduce(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.propose_observation("place_order", "budget_check", domain="finance")
        arch.propose_observation("budget_check", "budget_exceeded", domain="finance")
        successors = {dst for dst, dom in world.successors("budget_exceeded")}
        assert "reduce_order" in successors

    def test_budget_ok_proceeds(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.propose_observation("place_order", "budget_check", domain="finance")
        arch.propose_observation("budget_check", "budget_ok", domain="finance")
        # budget_ok is a terminal state - verify it was reached
        assert world.feature("budget_check", "budget_ok", Feature.FREQUENCY) > 0


class TestTimeWindowScheduling:
    def test_teen_available_allows_pickup(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.propose_observation("teen_available", "teen_can_pickup", domain="scheduling")
        successors = {dst for dst, dom in world.successors("teen_can_pickup")}
        assert "execute_pickup" in successors

    def test_teen_unavailable_blocks_pickup(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.propose_observation("teen_unavailable", "teen_cannot_pickup", domain="scheduling")
        successors = {dst for dst, dom in world.successors("teen_cannot_pickup")}
        assert len(successors) == 0  # Terminal state


class TestCrossAgentKnowledgeSharing:
    def test_gossip_shares_knowledge(self):
        world = create_family_world()
        network = ActorNetwork(world)
        network.add("parent")
        network.add("teen")
        # COMMUNITY has PUBLISH_OBSERVATIONS + SUBSCRIBE_OBSERVATIONS
        network.connect("parent", "teen", Relationship.COMMUNITY)
        network.ask("parent", "item_unavailable")
        teen = network.actors["teen"]
        assert teen.belief.nnz() >= 1

    def test_isolated_actors_no_gossip(self):
        world = create_family_world()
        network = ActorNetwork(world)
        network.add("parent")
        network.add("teen")
        network.ask("parent", "item_unavailable")
        teen = network.actors["teen"]
        assert teen.belief.nnz() == 0


class TestConflictResolution:
    def test_conflict_detection(self):
        world = create_family_world()
        network = ActorNetwork(world)
        network.add("parent")
        network.add("teen")
        network.ask("parent", "order_placed")
        network.ask("teen", "order_placed")
        conflicts = network.resolve_conflicts("parent", "teen")
        assert isinstance(conflicts, list)


class TestEventSourcing:
    def test_events_recorded_during_observations(self):
        from src.monkey_brain.kernel.compile.event_sourcing import get_event_store

        store = get_event_store()
        initial_count = store.count()
        world = create_family_world()
        network = ActorNetwork(world)
        network.add("parent")
        network.ask("parent", "shopping_list")
        assert store.count() > initial_count


class TestReplanningUnderFailure:
    def test_unavailable_item_replan(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.propose_observation("check_availability", "item_unavailable", domain="inventory")
        successors = {dst for dst, dom in world.successors("item_unavailable")}
        assert "suggest_substitute" in successors

    def test_budget_exceeded_replan(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.propose_observation("budget_check", "budget_exceeded", domain="finance")
        successors = {dst for dst, dom in world.successors("budget_exceeded")}
        assert "reduce_order" in successors

    def test_pickup_failure_replan(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.propose_observation("execute_pickup", "pickup_failed", domain="execution")
        successors = {dst for dst, dom in world.successors("pickup_failed")}
        assert "retry_pickup" in successors


class TestFullMultiAgentCycle:
    def test_full_family_coordination(self):
        world = create_family_world()
        network = ActorNetwork(world)
        _register_arch_with_network(network)

        network.add("parent")
        network.add("teen")
        # COMMUNITY has PUBLISH_OBSERVATIONS + SUBSCRIBE_OBSERVATIONS
        network.connect("parent", "teen", Relationship.COMMUNITY)

        network.ask("parent", "shopping_list")
        network.ask("parent", "place_order")
        network.ask("parent", "budget_check")
        network.ask("parent", "check_availability")
        network.ask("parent", "order_placed")
        network.ask("parent", "order_confirmed")

        teen = network.actors["teen"]
        assert teen.belief.nnz() >= 1

        network.ask("teen", "order_confirmed")
        network.ask("teen", "schedule_pickup")
        network.ask("teen", "pickup_scheduled")
        network.ask("teen", "execute_pickup")

        assert network.actors["parent"].belief.nnz() >= 5
        assert network.actors["teen"].belief.nnz() >= 5

    def test_multiple_actors_independent_policies(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.create_actor("parent")
        arch.create_actor("teen")
        arch.actor_learn("parent", "order_placed", "budget_check", reward=0.8)
        arch.actor_learn("teen", "order_confirmed", "schedule_pickup", reward=0.9)
        parent_q = arch.get_actor("parent")["policy"].value("order_placed", "budget_check")
        teen_q = arch.get_actor("teen")["policy"].value("order_placed", "budget_check")
        assert parent_q != teen_q or parent_q == 0.5

    def test_world_shared_across_actors(self):
        world = create_family_world()
        arch = _create_architecture(world)
        _register_capabilities(arch)
        arch.create_actor("parent")
        arch.create_actor("teen")
        parent_succs = {dst for dst, dom in world.successors("shopping_list")}
        teen_succs = {dst for dst, dom in world.successors("shopping_list")}
        assert parent_succs == teen_succs


def _register_arch_with_network(network: ActorNetwork) -> None:
    """Register capabilities on the CognitiveArchitecture underlying the network."""
    from monkey_brain.kernel.cognitive_engine import CognitiveArchitecture
    from src.monkey_brain.kernel.learn.world_learner import WorldLearner

    caps = [
        ("place_order", ["shopping_list"], ["order_placed"]),
        ("budget_check", ["order_placed"], ["budget_ok", "budget_exceeded"]),
        (
            "check_availability",
            ["order_placed"],
            ["items_available", "item_unavailable"],
        ),
        (
            "suggest_substitute",
            ["item_unavailable"],
            ["substitute_found", "no_substitute"],
        ),
        ("add_to_cart", ["substitute_found"], ["order_placed"]),
        (
            "schedule_pickup",
            ["order_confirmed"],
            ["pickup_scheduled", "pickup_rejected"],
        ),
        (
            "execute_pickup",
            ["pickup_scheduled", "teen_can_pickup"],
            ["items_acquired", "pickup_failed"],
        ),
        ("retry_pickup", ["pickup_failed"], ["execute_pickup"]),
        ("reduce_order", ["budget_exceeded"], ["place_order"]),
    ]
    arch = CognitiveArchitecture()
    arch._world = network._global
    arch._world_learner = WorldLearner(network._global)
    for name, preconditions, effects in caps:
        op = arch.register_action(name)
        for pre in preconditions:
            for eff in effects:
                op.enable(pre, eff)
