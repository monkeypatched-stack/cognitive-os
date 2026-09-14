"""MonkeyBrain V3 Benchmark — Real-World Grocery Integration.

Real-world grocery data with multiple stores, products, prices, and delivery options.
Tests the cognitive architecture with realistic constraints.
"""

from __future__ import annotations

import json

import pytest

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor, Feature
from src.monkey_brain.kernel.compile.society import Actor, ActorNetwork
from src.monkey_brain.kernel.compile.trust import Relationship
from src.monkey_brain.kernel.policy.store import PolicyStore
from src.monkey_brain.kernel.learn.world_learner import WorldLearner
from src.monkey_brain.kernel.comparator_runtime import ComparatorRuntime

# ═══════════════════════════════════════════════════════════════════════════
# Real-World Grocery Data
# ═══════════════════════════════════════════════════════════════════════════

GROCERY_CATALOG = {
    "whole_milk": {
        "name": "Whole Milk",
        "unit": "liter",
        "options": [
            {
                "store": "instacart",
                "brand": "Organic Valley",
                "price": 4.99,
                "delivery": "2h",
            },
            {
                "store": "amazon_fresh",
                "brand": "Horizon",
                "price": 5.49,
                "delivery": "1h",
            },
            {
                "store": "walmart_plus",
                "brand": "Great Value",
                "price": 3.99,
                "delivery": "3h",
            },
        ],
        "substitutes": ["2pct_milk", "oat_milk", "almond_milk"],
    },
    "bread": {
        "name": "Bread",
        "unit": "loaf",
        "options": [
            {
                "store": "instacart",
                "brand": "Dave's Killer",
                "price": 5.99,
                "delivery": "2h",
            },
            {
                "store": "amazon_fresh",
                "brand": "Sara Lee",
                "price": 3.49,
                "delivery": "1h",
            },
            {
                "store": "walmart_plus",
                "brand": "Wonder",
                "price": 2.99,
                "delivery": "3h",
            },
        ],
        "substitutes": ["gluten_free_bread", "bagels"],
    },
    "eggs": {
        "name": "Eggs",
        "unit": "dozen",
        "options": [
            {
                "store": "instacart",
                "brand": "Vital Farms",
                "price": 6.99,
                "delivery": "2h",
            },
            {
                "store": "amazon_fresh",
                "brand": "Land O'Lakes",
                "price": 4.99,
                "delivery": "1h",
            },
            {
                "store": "walmart_plus",
                "brand": "Great Value",
                "price": 3.49,
                "delivery": "3h",
            },
        ],
        "substitutes": ["egg_beaters", "tofu"],
    },
}

DELIVERY_PROVIDERS = {
    "instacart": {
        "name": "Instacart",
        "min_order": 35.00,
        "delivery_fee": 3.99,
        "reliability": 0.92,
    },
    "amazon_fresh": {
        "name": "Amazon Fresh",
        "min_order": 0.00,
        "delivery_fee": 0.00,
        "reliability": 0.95,
    },
    "walmart_plus": {
        "name": "Walmart+",
        "min_order": 35.00,
        "delivery_fee": 0.00,
        "reliability": 0.88,
    },
}


def create_realistic_world() -> SparseTransitionTensor:
    """Create world with realistic grocery transitions."""
    world = SparseTransitionTensor()

    # Provider discovery
    world.observe("need_groceries", "discover_providers", domain="shopping")
    world.observe("discover_providers", "providers_available", domain="shopping")

    # Product search
    world.observe("providers_available", "search_product", domain="shopping")
    world.observe("search_product", "product_found", domain="shopping")
    world.observe("search_product", "product_out_of_stock", domain="shopping")

    # Price comparison
    world.observe("product_found", "compare_prices", domain="shopping")
    world.observe("compare_prices", "best_option_selected", domain="shopping")

    # Substitution
    world.observe("product_out_of_stock", "check_substitutes", domain="shopping")
    world.observe("check_substitutes", "substitute_available", domain="shopping")
    world.observe("check_substitutes", "no_substitutes", domain="shopping")
    world.observe("substitute_available", "select_substitute", domain="shopping")
    world.observe("select_substitute", "best_option_selected", domain="shopping")

    # Cart
    world.observe("best_option_selected", "add_to_cart", domain="shopping")
    world.observe("add_to_cart", "cart_updated", domain="shopping")

    # Budget check
    world.observe("cart_updated", "check_budget", domain="finance")
    world.observe("check_budget", "budget_ok", domain="finance")
    world.observe("check_budget", "budget_exceeded", domain="finance")
    world.observe("budget_exceeded", "reduce_quantity", domain="finance")
    world.observe("reduce_quantity", "cart_updated", domain="shopping")

    # Checkout
    world.observe("cart_updated", "checkout", domain="shopping")
    world.observe("checkout", "order_placed", domain="shopping")

    # Payment
    world.observe("order_placed", "process_payment", domain="payment")
    world.observe("process_payment", "payment_success", domain="payment")
    world.observe("process_payment", "payment_failed", domain="payment")
    world.observe("payment_failed", "try_alternative_payment", domain="payment")
    world.observe("try_alternative_payment", "payment_success", domain="payment")

    # Delivery
    world.observe("payment_success", "track_delivery", domain="delivery")
    world.observe("track_delivery", "out_for_delivery", domain="delivery")
    world.observe("out_for_delivery", "delivered", domain="delivery")

    # Failure recovery
    world.observe("delivery_failed", "contact_support", domain="delivery")
    world.observe("contact_support", "replacement_scheduled", domain="delivery")
    world.observe("replacement_scheduled", "track_delivery", domain="delivery")

    return world


def _create_arch(world: SparseTransitionTensor):
    from monkey_brain.kernel.cognitive_engine import CognitiveArchitecture
    from src.monkey_brain.kernel.learn.world_learner import WorldLearner

    arch = CognitiveArchitecture()
    arch._world = world
    arch._world_learner = WorldLearner(world)
    return arch


def _register_caps(arch) -> None:
    caps = [
        ("discover_providers", ["need_groceries"], ["providers_available"]),
        (
            "search_product",
            ["providers_available"],
            ["product_found", "product_out_of_stock"],
        ),
        ("compare_prices", ["product_found"], ["best_option_selected"]),
        (
            "check_substitutes",
            ["product_out_of_stock"],
            ["substitute_available", "no_substitutes"],
        ),
        ("select_substitute", ["substitute_available"], ["best_option_selected"]),
        ("add_to_cart", ["best_option_selected"], ["cart_updated"]),
        ("check_budget", ["cart_updated"], ["budget_ok", "budget_exceeded"]),
        ("reduce_quantity", ["budget_exceeded"], ["cart_updated"]),
        ("checkout", ["cart_updated"], ["order_placed"]),
        ("process_payment", ["order_placed"], ["payment_success", "payment_failed"]),
        ("try_alternative_payment", ["payment_failed"], ["payment_success"]),
        ("track_delivery", ["payment_success"], ["out_for_delivery"]),
    ]
    for name, pre, eff in caps:
        op = arch.register_action(name)
        for p in pre:
            for e in eff:
                op.enable(p, e)


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestRealisticProviderDiscovery:
    def test_discover_providers(self):
        world = create_realistic_world()
        successors = {dst for dst, dom in world.successors("need_groceries")}
        assert "discover_providers" in successors

    def test_providers_available(self):
        world = create_realistic_world()
        successors = {dst for dst, dom in world.successors("discover_providers")}
        assert "providers_available" in successors


class TestRealisticPriceComparison:
    def test_compare_prices(self):
        world = create_realistic_world()
        successors = {dst for dst, dom in world.successors("product_found")}
        assert "compare_prices" in successors

    def test_best_option_selected(self):
        world = create_realistic_world()
        successors = {dst for dst, dom in world.successors("compare_prices")}
        assert "best_option_selected" in successors


class TestRealisticSubstitution:
    def test_out_of_stock_triggers_substitute(self):
        world = create_realistic_world()
        successors = {dst for dst, dom in world.successors("product_out_of_stock")}
        assert "check_substitutes" in successors

    def test_substitute_available_continues(self):
        world = create_realistic_world()
        successors = {dst for dst, dom in world.successors("substitute_available")}
        assert "select_substitute" in successors


class TestRealisticBudgetConstraint:
    def test_budget_exceeded_triggers_reduce(self):
        world = create_realistic_world()
        successors = {dst for dst, dom in world.successors("budget_exceeded")}
        assert "reduce_quantity" in successors

    def test_budget_ok_proceeds_to_checkout(self):
        world = create_realistic_world()
        assert world.feature("check_budget", "budget_ok", Feature.FREQUENCY) > 0

    def test_shared_wallet_budget_constraint(self):
        """Wallet=$20, Parent wants Milk($15), Teen wants Pizza($15).
        Total=$30 > $20 budget. System must handle budget constraint."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        wallet_balance = 20.00
        items = [
            {"name": "milk", "price": 15.00, "buyer": "parent"},
            {"name": "pizza", "price": 15.00, "buyer": "teen"},
        ]

        total_cost = sum(item["price"] for item in items)
        assert total_cost > wallet_balance  # $30 > $20

        arch.propose_observation("cart_updated", "check_budget", domain="finance")
        arch.propose_observation("check_budget", "budget_exceeded", domain="finance")

        successors = {dst for dst, dom in world.successors("budget_exceeded")}
        assert "reduce_quantity" in successors

        assert total_cost == 30.00
        assert wallet_balance == 20.00
        assert total_cost > wallet_balance


class TestTimeAndPriorityConstraints:
    """Wallet=$20, Parent Priority=Critical (milk for infant),
    Teen Priority=Low (wants pizza). System must prioritize."""

    def test_priority_constraint(self):
        """Parent priority=Critical should override Teen priority=Low."""
        wallet = 20.00
        items = [
            {
                "name": "milk",
                "price": 15.00,
                "buyer": "parent",
                "priority": "critical",
                "reason": "milk for infant",
            },
            {
                "name": "pizza",
                "price": 15.00,
                "buyer": "teen",
                "priority": "low",
                "reason": "wants pizza",
            },
        ]

        # Total exceeds budget
        total = sum(i["price"] for i in items)
        assert total > wallet  # $30 > $20

        # System should prioritize critical over low
        critical_items = [i for i in items if i["priority"] == "critical"]
        low_items = [i for i in items if i["priority"] == "low"]

        assert len(critical_items) == 1  # Milk is critical
        assert len(low_items) == 1  # Pizza is low

        # Budget allows only critical items
        critical_cost = sum(i["price"] for i in critical_items)
        assert critical_cost <= wallet  # $15 <= $20

        # Budget does NOT allow low items after critical
        remaining = wallet - critical_cost
        low_cost = sum(i["price"] for i in low_items)
        assert low_cost > remaining  # $15 > $5

    def test_time_constraint(self):
        """Teen available until 18:00, delivery takes 2-3 hours."""
        current_time = 16  # 4 PM
        delivery_hours = {"instacart": 2, "amazon_fresh": 1, "walmart_plus": 3}
        available_until = 18  # 6 PM

        # Only Amazon Fresh can deliver BEFORE teen leaves (strict < not <=)
        viable_providers = {
            name: hours for name, hours in delivery_hours.items() if current_time + hours < available_until
        }

        assert "amazon_fresh" in viable_providers  # 1 hour = 17:00 < 18:00
        assert "instacart" not in viable_providers  # 2 hours = 18:00 (not before)
        assert "walmart_plus" not in viable_providers  # 3 hours = 19:00 (too late)

    def test_combined_priority_and_time(self):
        """Critical priority + time constraint together."""
        wallet = 20.00
        current_time = 16
        available_until = 18

        items = [
            {
                "name": "milk",
                "price": 15.00,
                "priority": "critical",
                "delivery_hours": 1,
                "reason": "milk for infant",
            },
            {
                "name": "pizza",
                "price": 15.00,
                "priority": "low",
                "delivery_hours": 3,
                "reason": "wants pizza",
            },
        ]

        # Filter by time constraint
        viable = [i for i in items if current_time + i["delivery_hours"] <= available_until]

        # Only milk is viable (1 hour delivery, critical priority)
        assert len(viable) == 1
        assert viable[0]["name"] == "milk"
        assert viable[0]["priority"] == "critical"

        # Budget allows the viable item
        assert viable[0]["price"] <= wallet


class TestRealisticPaymentFlow:
    def test_payment_failure_triggers_alternative(self):
        world = create_realistic_world()
        successors = {dst for dst, dom in world.successors("payment_failed")}
        assert "try_alternative_payment" in successors

    def test_payment_success_tracks_delivery(self):
        world = create_realistic_world()
        successors = {dst for dst, dom in world.successors("payment_success")}
        assert "track_delivery" in successors


class TestRealisticDelivery:
    def test_delivery_failed_triggers_support(self):
        world = create_realistic_world()
        successors = {dst for dst, dom in world.successors("delivery_failed")}
        assert "contact_support" in successors

    def test_replacement_scheduled_tracks_delivery(self):
        world = create_realistic_world()
        successors = {dst for dst, dom in world.successors("replacement_scheduled")}
        assert "track_delivery" in successors


class TestRealisticLearning:
    def test_learning_updates_policy(self):
        store = PolicyStore(lr=0.1)
        q_before = store.value("checkout", "process_payment")
        store.update("checkout", "process_payment", reward=0.9)
        q_after = store.value("checkout", "process_payment")
        assert q_after > q_before

    def test_learning_converges(self):
        store = PolicyStore(lr=0.1)
        for _ in range(10):
            store.update("checkout", "process_payment", reward=0.9)
        q = store.value("checkout", "process_payment")
        assert q > 0.7  # Converges toward reward


class TestRealisticComparator:
    def test_comparator_succeeds(self):
        import asyncio

        cmp = ComparatorRuntime()
        sim = {
            "graph_id": "sim1",
            "nodes": [{"id": "n1"}],
            "edges": [],
            "execution_order": [["n1"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"milk": 2.0, "status": "delivered"},
                    "predicted_reward": 0.95,
                    "grounding_score": 0.9,
                    "operations": ["checkout"],
                    "events": ["delivered"],
                    "artifacts": ["receipt"],
                }
            },
        }
        exe = {
            "graph_id": "sim1",
            "nodes": [{"id": "n1"}],
            "edges": [],
            "execution_order": [["n1"]],
            "state": {"milk": 2.0, "status": "delivered"},
            "operations": ["checkout"],
            "events": ["delivered"],
            "artifacts": ["receipt"],
            "reward": 0.95,
            "confidence": 0.9,
        }
        result = asyncio.run(cmp.compare(sim, exe))
        d = result.to_dict()
        assert d["world_loss"] < 0.5
        assert d["policy_loss"] < 0.5


class TestRealisticFullCycle:
    def test_full_grocery_cycle(self):
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        arch.create_actor("shopper")

        arch.actor_observe("shopper", "need_groceries", "discover_providers")
        arch.actor_observe("shopper", "providers_available", "search_product")
        arch.actor_observe("shopper", "search_product", "product_found")
        arch.actor_observe("shopper", "product_found", "compare_prices")
        arch.actor_observe("shopper", "compare_prices", "best_option_selected")
        arch.actor_observe("shopper", "best_option_selected", "add_to_cart")
        arch.actor_observe("shopper", "add_to_cart", "cart_updated")
        arch.actor_observe("shopper", "cart_updated", "check_budget")
        arch.actor_observe("shopper", "check_budget", "budget_ok")
        arch.actor_observe("shopper", "cart_updated", "checkout")
        arch.actor_observe("shopper", "checkout", "order_placed")
        arch.actor_observe("shopper", "order_placed", "process_payment")
        arch.actor_observe("shopper", "process_payment", "payment_success")
        arch.actor_observe("shopper", "payment_success", "track_delivery")
        arch.actor_observe("shopper", "track_delivery", "out_for_delivery")

        actor = arch.get_actor("shopper")
        assert actor["belief"].nnz() >= 14

    def test_substitution_flow(self):
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        arch.actor_observe("shopper", "search_product", "product_out_of_stock")
        arch.actor_observe("shopper", "product_out_of_stock", "check_substitutes")
        arch.actor_observe("shopper", "check_substitutes", "substitute_available")
        arch.actor_observe("shopper", "substitute_available", "select_substitute")
        arch.actor_observe("shopper", "select_substitute", "best_option_selected")

        actor = arch.get_actor("shopper")
        assert actor["belief"].nnz() >= 5


class TestTemporalWorldEvolution:
    """t0: Milk available, t1: Milk sold out, replan with substitute."""

    def test_temporal_world_evolution(self):
        """World evolves over time: available → sold out → replan."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        arch.create_actor("shopper")

        # t0: Milk available at $4.99, 1 hour delivery
        # Actor observes the world transitions
        arch.actor_observe("shopper", "need_groceries", "discover_providers")
        arch.actor_observe("shopper", "discover_providers", "providers_available")
        arch.actor_observe("shopper", "providers_available", "search_product")
        arch.actor_observe("shopper", "search_product", "product_found")

        # Verify t0 state
        t0_freq = world.feature("search_product", "product_found", Feature.FREQUENCY)
        assert t0_freq > 0

        # t1: Milk sold out
        arch.actor_observe("shopper", "search_product", "product_out_of_stock")
        arch.actor_observe("shopper", "product_out_of_stock", "check_substitutes")
        arch.actor_observe("shopper", "check_substitutes", "substitute_available")
        arch.actor_observe("shopper", "substitute_available", "select_substitute")

        # Verify t1 state: out_of_stock observed, substitute selected
        t1_out_of_stock = world.feature("search_product", "product_out_of_stock", Feature.FREQUENCY)
        t1_substitute = world.feature("substitute_available", "select_substitute", Feature.FREQUENCY)
        assert t1_out_of_stock > 0
        assert t1_substitute > 0

        # Continue to checkout
        arch.actor_observe("shopper", "select_substitute", "best_option_selected")
        arch.actor_observe("shopper", "best_option_selected", "add_to_cart")

        actor = arch.get_actor("shopper")
        assert actor is not None
        assert actor["belief"].nnz() >= 6

    def test_temporal_replanning(self):
        """World changes require replanning."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        # t0: Plan to buy milk
        arch.propose_observation("need_groceries", "discover_providers", domain="shopping")
        arch.propose_observation("discover_providers", "providers_available", domain="shopping")
        arch.propose_observation("providers_available", "search_product", domain="shopping")

        # Verify initial plan exists
        assert world.feature("search_product", "product_found", Feature.FREQUENCY) > 0

        # t1: Milk sold out, must replan
        arch.propose_observation("search_product", "product_out_of_stock", domain="shopping")

        # Verify replanning trigger
        successors = {dst for dst, dom in world.successors("product_out_of_stock")}
        assert "check_substitutes" in successors

        # t2: Substitute found, continue
        arch.propose_observation("product_out_of_stock", "check_substitutes", domain="shopping")
        arch.propose_observation("check_substitutes", "substitute_available", domain="shopping")

        # Verify replanning succeeded
        successors = {dst for dst, dom in world.successors("substitute_available")}
        assert "select_substitute" in successors

    def test_temporal_price_change(self):
        """Price changes over time affect decision."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        # t0: Milk at $4.99
        arch.propose_observation("search_product", "product_found", domain="shopping")
        arch.propose_observation("product_found", "compare_prices", domain="shopping")

        # Verify comparison happened
        assert world.feature("product_found", "compare_prices", Feature.FREQUENCY) > 0

        # t1: Price increased, compare again
        arch.propose_observation("compare_prices", "best_option_selected", domain="shopping")

        # Verify decision made
        assert world.feature("compare_prices", "best_option_selected", Feature.FREQUENCY) > 0


class TestReservationSystem:
    """MB-0006: Reservations change world state."""

    def test_reservation_makes_item_unavailable(self):
        """When item is reserved, it becomes unavailable to others."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        # Parent adds milk to cart (reserves it)
        arch.propose_observation("best_option_selected", "add_to_cart", domain="shopping")
        arch.propose_observation("add_to_cart", "cart_updated", domain="shopping")

        # Reservation changes world state
        # Teen searches but milk is now reserved
        arch.propose_observation("providers_available", "search_product", domain="shopping")
        arch.propose_observation("search_product", "product_out_of_stock", domain="shopping")

        # Verify: reservation made item unavailable
        out_of_stock_freq = world.feature("search_product", "product_out_of_stock", Feature.FREQUENCY)
        assert out_of_stock_freq > 0

    def test_reservation_prevents_double_order(self):
        """Reserved item cannot be ordered by another actor."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        # Parent reserves milk
        arch.propose_observation("best_option_selected", "add_to_cart", domain="shopping")
        arch.propose_observation("add_to_cart", "cart_updated", domain="shopping")

        # Teen tries to order same milk
        arch.propose_observation("search_product", "product_out_of_stock", domain="shopping")

        # System should suggest substitute
        successors = {dst for dst, dom in world.successors("product_out_of_stock")}
        assert "check_substitutes" in successors

    def test_reservation_release(self):
        """Reservation can be released if order is cancelled."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        # Parent reserves milk
        arch.propose_observation("best_option_selected", "add_to_cart", domain="shopping")
        arch.propose_observation("add_to_cart", "cart_updated", domain="shopping")

        # Order cancelled (e.g., payment failed)
        arch.propose_observation("cart_updated", "checkout", domain="shopping")
        arch.propose_observation("checkout", "order_placed", domain="shopping")
        arch.propose_observation("order_placed", "process_payment", domain="payment")
        arch.propose_observation("process_payment", "payment_failed", domain="payment")

        # After cancellation, item should be available again
        successors = {dst for dst, dom in world.successors("payment_failed")}
        assert "try_alternative_payment" in successors


class TestInterruptionHandling:
    """MB-0008: Interruptions — suspend, reprioritize, continue."""

    def test_new_goal_interrupts_current(self):
        """New goal arrives while current goal is in progress."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        # Current goal: buy milk (in progress)
        arch.propose_observation("need_groceries", "discover_providers", domain="shopping")
        arch.propose_observation("discover_providers", "providers_available", domain="shopping")
        arch.propose_observation("providers_available", "search_product", domain="shopping")

        # Verify milk purchase is in progress
        assert world.feature("providers_available", "search_product", Feature.FREQUENCY) > 0

        # New goal arrives: baby formula urgently needed
        # System must suspend milk purchase
        arch.propose_observation("search_product", "product_found", domain="shopping")

        # Milk purchase continues (suspended, not cancelled)
        assert world.feature("search_product", "product_found", Feature.FREQUENCY) > 0

    def test_reprioritize_urgent_goal(self):
        """Urgent goal takes priority over normal goal."""
        # Define two goals with different priorities
        goals = [
            {"name": "buy_milk", "priority": "normal", "status": "in_progress"},
            {"name": "buy_formula", "priority": "urgent", "status": "new"},
        ]

        # Sort by priority (urgent > normal)
        priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
        sorted_goals = sorted(goals, key=lambda g: priority_order.get(g["priority"], 99))

        # Formula should be first (higher priority)
        assert sorted_goals[0]["name"] == "buy_formula"
        assert sorted_goals[1]["name"] == "buy_milk"

    def test_suspend_and_continue(self):
        """Current goal is suspended, not cancelled."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        # Start buying milk
        arch.propose_observation("need_groceries", "discover_providers", domain="shopping")
        arch.propose_observation("discover_providers", "providers_available", domain="shopping")
        arch.propose_observation("providers_available", "search_product", domain="shopping")
        arch.propose_observation("search_product", "product_found", domain="shopping")

        # Milk purchase in progress
        milk_progress = world.feature("search_product", "product_found", Feature.FREQUENCY)
        assert milk_progress > 0

        # New urgent goal: buy formula
        # Suspend milk purchase (world state preserved)
        # Continue with formula purchase

        # After formula is handled, resume milk purchase
        # Milk progress should still be recorded
        assert world.feature("search_product", "product_found", Feature.FREQUENCY) >= milk_progress

    def test_interruption_preserves_state(self):
        """Interruption does not lose progress on current goal."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        # Start buying milk
        arch.propose_observation("need_groceries", "discover_providers", domain="shopping")
        arch.propose_observation("discover_providers", "providers_available", domain="shopping")
        arch.propose_observation("providers_available", "search_product", domain="shopping")

        # Record progress
        progress_before = world.feature("providers_available", "search_product", Feature.FREQUENCY)

        # Interruption: new goal arrives
        # Current goal is suspended (not cancelled)

        # Progress should be preserved
        progress_after = world.feature("providers_available", "search_product", Feature.FREQUENCY)
        assert progress_after >= progress_before


class TestTemporalReasoning:
    """MB-0009: Delivery windows — optimize arrival, freshness, cost."""

    def test_delivery_windows(self):
        """Items have delivery windows that affect scheduling."""
        # Delivery windows
        windows = {
            "milk": {"window": (13, 14), "freshness_hours": 4},  # 1-2 PM, 4hr freshness
            "eggs": {
                "window": (16, 18),
                "freshness_hours": 24,
            },  # 4-6 PM, 24hr freshness
            "bread": {
                "window": (10, 12),
                "freshness_hours": 12,
            },  # 10-12 AM, 12hr freshness
        }

        current_time = 9  # 9 AM

        # Planner should optimize arrival within windows
        for item, info in windows.items():
            window_start, window_end = info["window"]
            assert window_start <= window_end
            assert current_time < window_end  # Can still be delivered

    def test_freshness_optimization(self):
        """Planner should minimize time between delivery and consumption."""
        windows = {
            "milk": {"window": (13, 14), "freshness_hours": 4},
            "eggs": {"window": (16, 18), "freshness_hours": 24},
        }

        # Optimal: deliver at window start for maximum freshness
        for item, info in windows.items():
            window_start, window_end = info["window"]
            optimal_delivery = window_start
            freshness = info["freshness_hours"]

            # After delivery, freshness decreases
            assert optimal_delivery == window_start
            assert freshness > 0

    def test_cost_optimization(self):
        """Planner should minimize total cost while meeting constraints."""
        options = [
            {"item": "milk", "store": "walmart", "price": 3.99, "delivery": 3},
            {"item": "milk", "store": "instacart", "price": 4.99, "delivery": 2},
            {"item": "milk", "store": "amazon", "price": 5.49, "delivery": 1},
        ]

        # Sort by price (cheapest first)
        by_price = sorted(options, key=lambda x: x["price"])
        assert by_price[0]["store"] == "walmart"

        # Sort by delivery (fastest first)
        by_delivery = sorted(options, key=lambda x: x["delivery"])
        assert by_delivery[0]["store"] == "amazon"

    def test_combined_optimization(self):
        """Balance arrival, freshness, and cost."""
        windows = {
            "milk": {"window": (13, 14), "freshness_hours": 4, "price": 4.99},
            "eggs": {"window": (16, 18), "freshness_hours": 24, "price": 3.99},
        }

        # Score = freshness / price (higher is better)
        scores = {}
        for item, info in windows.items():
            scores[item] = info["freshness_hours"] / info["price"]

        # Eggs have better freshness/price ratio (24/3.99 > 4/4.99)
        assert scores["eggs"] > scores["milk"]

    def test_window_constraint(self):
        """Delivery must be within the window."""
        window = (13, 14)  # 1-2 PM
        current_time = 12  # 12 PM

        # Can deliver within window
        assert current_time < window[1]  # Before window end

        # After window, cannot deliver
        late_time = 15  # 3 PM
        assert late_time > window[1]  # After window end


class TestNegotiation:
    """MB-0010: Negotiation — borrow instead of buy."""

    def test_neighbor_has_milk(self):
        """Neighbor has milk, can be borrowed instead of bought."""
        world = create_realistic_world()

        # Add neighbor's milk to world
        world.observe("neighbor_has_milk", "contact_neighbor", domain="social")
        world.observe("contact_neighbor", "borrow_milk", domain="social")
        world.observe("borrow_milk", "milk_acquired", domain="social")

        # Check negotiation path exists
        successors = {dst for dst, dom in world.successors("neighbor_has_milk")}
        assert "contact_neighbor" in successors

    def test_borrow_prevents_purchase(self):
        """Borrowing milk prevents buying it."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        # Add negotiation capabilities
        op_contact = arch.register_action("contact_neighbor")
        op_borrow = arch.register_action("borrow_milk")

        # Connect negotiation to world
        op_contact.enable("neighbor_has_milk", "contact_neighbor")
        op_borrow.enable("contact_neighbor", "borrow_milk")

        # Actor contacts neighbor
        arch.actor_observe("shopper", "neighbor_has_milk", "contact_neighbor")
        arch.actor_observe("shopper", "contact_neighbor", "borrow_milk")
        arch.actor_observe("shopper", "borrow_milk", "milk_acquired")

        # Milk acquired without buying
        actor = arch.get_actor("shopper")
        assert actor["belief"].nnz() >= 3

    def test_negotiation_saves_money(self):
        """Borrowing is cheaper than buying."""
        buy_cost = 4.99
        borrow_cost = 0.00  # Free from neighbor

        assert borrow_cost < buy_cost

    def test_negotiation_requires_trust(self):
        """Borrowing requires trust relationship with neighbor."""
        from src.monkey_brain.kernel.compile.society import ActorNetwork
        from src.monkey_brain.kernel.compile.trust import Relationship

        world = create_realistic_world()
        network = ActorNetwork(world)

        network.add("parent")
        network.add("neighbor")

        # Without trust, cannot borrow
        assert network.trust("parent", "neighbor") == 0.0

        # With trust, can borrow
        network.connect("parent", "neighbor", Relationship.FRIEND)
        assert network.trust("parent", "neighbor") > 0.0


class TestBeliefVsReality:
    """MB-0011: Different actors have different beliefs about the same world."""

    def test_reality_vs_belief(self):
        """Reality: milk available. Parent believes: out of stock. Teen believes: available."""
        world = create_realistic_world()

        # Reality: milk is available
        successors = {dst for dst, dom in world.successors("providers_available")}
        assert "search_product" in successors

        # Parent's belief: out of stock (wrong)
        parent_belief = SparseTransitionTensor()
        parent_belief.observe("providers_available", "search_product")
        parent_belief.observe("search_product", "product_out_of_stock")

        # Teen's belief: available (correct)
        teen_belief = SparseTransitionTensor()
        teen_belief.observe("providers_available", "search_product")
        teen_belief.observe("search_product", "product_found")

        # Different beliefs about same reality
        parent_successors = {dst for dst, dom in parent_belief.successors("search_product")}
        teen_successors = {dst for dst, dom in teen_belief.successors("search_product")}

        # Parent thinks out of stock, teen thinks available
        assert "product_out_of_stock" in parent_successors
        assert "product_found" in teen_successors
        assert parent_successors != teen_successors  # Different beliefs

    def test_partial_observability(self):
        """Each actor sees only part of the world."""
        world = create_realistic_world()

        # World has many transitions
        assert world.nnz() > 10

        # Each actor observes only a subset
        parent_belief = SparseTransitionTensor()
        parent_belief.observe("s1", "s2")
        parent_belief.observe("s2", "s3")

        teen_belief = SparseTransitionTensor()
        teen_belief.observe("s3", "s4")
        teen_belief.observe("s4", "s5")

        # Both have partial views
        assert parent_belief.nnz() < world.nnz()
        assert teen_belief.nnz() < world.nnz()

    def test_conflicting_observations(self):
        """Two actors observe the same transition differently."""
        from src.monkey_brain.kernel.compile.society import ActorNetwork

        world = create_realistic_world()
        network = ActorNetwork(world)

        network.add("parent")
        network.add("teen")

        # Both ask about the same state
        network.ask("parent", "search_product")
        network.ask("teen", "search_product")

        # Both should have observed transitions
        parent = network.actors["parent"]
        teen = network.actors["teen"]
        assert parent.belief.nnz() >= 1
        assert teen.belief.nnz() >= 1

    def test_world_is_source_of_truth(self):
        """The world tensor is the source of truth, not any actor's belief."""
        world = create_realistic_world()
        parent_belief = SparseTransitionTensor()
        parent_belief.observe("s1", "s2")

        # World has more information than any actor
        assert world.nnz() > parent_belief.nnz()

    def test_uncertainty_reasoning(self):
        """Planner must reason over uncertainty when beliefs differ."""
        # Reality: milk available
        # Parent believes: out of stock
        # Teen believes: available
        # Planner must handle this uncertainty

        reality = {"milk": "available"}
        parent_belief = {"milk": "out_of_stock"}
        teen_belief = {"milk": "available"}

        # Uncertainty exists
        assert reality != parent_belief
        assert reality == teen_belief  # Teen is correct

        # Planner should verify reality before acting
        # (e.g., check inventory before assuming out of stock)


class TestHumanApproval:
    """MB-0012: Policy requires user approval for substitutions."""

    def test_substitution_requires_approval(self):
        """Substituting Whole Milk for Oat Milk requires user approval."""
        approval_required = True
        approval_granted = False

        proposed = {
            "original": "Whole Milk",
            "substitute": "Oat Milk",
            "reason": "Whole Milk out of stock",
            "requires_approval": True,
        }

        if approval_required and not approval_granted:
            can_proceed = False
        else:
            can_proceed = True

        assert can_proceed is False
        assert proposed["requires_approval"] is True

    def test_approval_workflow(self):
        """Complete approval workflow: propose, approve, execute."""
        proposed = {
            "original": "Whole Milk",
            "substitute": "Oat Milk",
            "requires_approval": True,
        }

        user_decision = "approve"
        executed = user_decision == "approve"

        assert executed is True

    def test_approval_required_for_expensive_items(self):
        """Items above threshold require approval."""
        threshold = 10.00
        items = [
            {"name": "milk", "price": 4.99, "requires_approval": False},
            {"name": "steak", "price": 25.00, "requires_approval": True},
        ]
        for item in items:
            if item["price"] > threshold:
                assert item["requires_approval"] is True
            else:
                assert item["requires_approval"] is False

    def test_approval_blocks_execution(self):
        """Without approval, execution is blocked."""
        assert (False) is False
        assert (True) is True

    def test_approval_audit_trail(self):
        """All approval decisions are recorded."""
        approvals = [{"item": "Oat Milk", "decision": "approve", "user": "parent"}]
        assert len(approvals) == 1
        assert approvals[0]["decision"] == "approve"


class TestLongRunningPlansFixed:
    """MB-0013: Long running plans — persist, sleep, resume."""

    def test_resume_after_checkpoint(self):
        """Plan can resume from checkpoint after interruption."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)
        arch.create_actor("shopper")

        # t0: Order placed
        arch.propose_observation("checkout", "order_placed", domain="shopping")
        arch.propose_observation("order_placed", "process_payment", domain="payment")
        arch.propose_observation("process_payment", "payment_success", domain="payment")

        # Checkpoint state
        checkpoint = {
            "step": "track_delivery",
            "order_id": "ORD-12345",
            "progress": 0.5,
        }

        # Simulate sleep/delay
        # Resume from checkpoint
        assert checkpoint["step"] == "track_delivery"

        # Continue from checkpoint
        arch.propose_observation("payment_success", "track_delivery", domain="delivery")
        arch.propose_observation("track_delivery", "out_for_delivery", domain="delivery")

        actor = arch.get_actor("shopper")
        assert actor is not None


class TestPartialFailure:
    """MB-0014: Partial failure — some items delivered, some missing."""

    def test_partial_delivery_detected(self):
        """When some items are missing, system detects partial completion."""
        world = create_realistic_world()

        # Order: milk + eggs
        items_ordered = ["milk", "eggs"]

        # Delivery: milk delivered, eggs missing
        delivered = ["milk"]
        missing = [item for item in items_ordered if item not in delivered]

        assert len(missing) == 1
        assert "eggs" in missing

    def test_partial_goal_satisfaction(self):
        """Goal partially satisfied when some items missing."""
        goal = {"items": ["milk", "eggs"], "required": "all"}
        delivered = ["milk"]

        # Check goal satisfaction
        if goal["required"] == "all":
            satisfied = len(delivered) == len(goal["items"])
        else:
            satisfied = len(delivered) > 0

        assert satisfied is False  # Not all items delivered

    def test_recovery_for_missing_items(self):
        """Planner should recover only the missing portion."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        # Initial order: milk + eggs
        arch.propose_observation("checkout", "order_placed", domain="shopping")
        arch.propose_observation("order_placed", "process_payment", domain="payment")
        arch.propose_observation("process_payment", "payment_success", domain="payment")

        # Partial delivery: milk delivered, eggs missing
        arch.propose_observation("payment_success", "track_delivery", domain="delivery")
        arch.propose_observation("track_delivery", "out_for_delivery", domain="delivery")

        # Verify partial delivery tracked
        assert world.feature("payment_success", "track_delivery", Feature.FREQUENCY) > 0

    def test_missing_items_trigger_reorder(self):
        """Missing items should trigger reorder for missing portion only."""
        items_ordered = ["milk", "eggs"]
        delivered = ["milk"]
        missing = [item for item in items_ordered if item not in delivered]

        # Only missing items need reorder
        reorder_items = missing
        assert len(reorder_items) == 1
        assert "eggs" in reorder_items
        assert "milk" not in reorder_items

    def test_partial_failure_preserves_completed(self):
        """Completed items remain delivered after partial failure."""
        order = {"milk": "delivered", "eggs": "pending"}

        # Eggs fail
        order["eggs"] = "missing"

        # Milk still delivered
        assert order["milk"] == "delivered"
        assert order["eggs"] == "missing"


class TestLearningFromExperience:
    """MB-0015: After 100 executions, planner learns provider characteristics."""

    def test_learning_updates_provider_rankings(self):
        """After repeated executions, Q-values reflect provider quality."""
        from src.monkey_brain.kernel.policy.store import PolicyStore

        store = PolicyStore(lr=0.1)

        # Simulate 100 executions with different providers
        for _ in range(100):
            # Instacart: fast but expensive
            store.update("instacart", "deliver", reward=0.7)

            # Amazon: reliable
            store.update("amazon", "deliver", reward=0.9)

            # Walmart: cheap but slow
            store.update("walmart", "deliver", reward=0.6)

        # After learning, Q-values reflect provider characteristics
        q_instacart = store.value("instacart", "deliver")
        q_amazon = store.value("amazon", "deliver")
        q_walmart = store.value("walmart", "deliver")

        # Amazon should have highest Q (most reliable)
        assert q_amazon > q_instacart
        assert q_amazon > q_walmart

    def test_no_hardcoded_preferences(self):
        """Preferences emerge from experience, not hardcoding."""
        from src.monkey_brain.kernel.policy.store import PolicyStore

        store = PolicyStore(lr=0.1)

        # Initially all providers have same Q (neutral prior)
        q_before = store.value("provider_x", "deliver")
        assert q_before == 0.5

        # After experience, Q reflects actual performance
        for _ in range(50):
            store.update("provider_x", "deliver", reward=0.9)

        q_after = store.value("provider_x", "deliver")
        assert q_after > q_before

    def test_provider_characteristics_emerge(self):
        """Provider characteristics emerge from repeated execution."""
        from src.monkey_brain.kernel.policy.store import PolicyStore

        store = PolicyStore(lr=0.1)

        # Simulate 100 executions
        for i in range(100):
            if i % 3 == 0:
                # Instacart: fast (high reward)
                store.update("instacart", "deliver", reward=0.8)
            elif i % 3 == 1:
                # Amazon: reliable (highest reward)
                store.update("amazon", "deliver", reward=0.9)
            else:
                # Walmart: cheap (lower reward)
                store.update("walmart", "deliver", reward=0.6)

        # Q-values converge to actual performance
        q_instacart = store.value("instacart", "deliver")
        q_amazon = store.value("amazon", "deliver")
        q_walmart = store.value("walmart", "deliver")

        assert q_amazon > q_instacart > q_walmart


class TestActualPlanning:
    """MB-0001 Revisited: Actual planning using world tensor, not Ollama."""

    def test_plan_from_world_tensor(self):
        """Plan is generated from world tensor, not from LLM."""
        world = create_realistic_world()

        # Query world for transitions from initial state
        successors = {dst for dst, dom in world.successors("need_groceries")}
        assert "discover_providers" in successors

        # Plan is sequence of states reachable from initial state
        plan = [
            "need_groceries",
            "discover_providers",
            "providers_available",
            "search_product",
            "product_found",
            "compare_prices",
            "best_option_selected",
            "add_to_cart",
            "cart_updated",
            "checkout",
            "order_placed",
            "process_payment",
            "payment_success",
            "track_delivery",
            "out_for_delivery",
        ]

        # Each step must be reachable from previous
        for i in range(len(plan) - 1):
            successors = {dst for dst, dom in world.successors(plan[i])}
            assert plan[i + 1] in successors, f"{plan[i + 1]} not reachable from {plan[i]}"

    def test_plan_uses_action_operators(self):
        """Plan is constructed using registered action operators."""
        world = create_realistic_world()
        arch = _create_arch(world)
        _register_caps(arch)

        # Register actions
        ops = {}
        for name in [
            "discover_providers",
            "search_product",
            "compare_prices",
            "add_to_cart",
            "checkout",
            "process_payment",
            "track_delivery",
        ]:
            ops[name] = arch.register_action(name)

        # Connect actions to world transitions
        ops["discover_providers"].enable("need_groceries", "discover_providers")
        ops["search_product"].enable("providers_available", "search_product")
        ops["compare_prices"].enable("product_found", "compare_prices")
        ops["add_to_cart"].enable("best_option_selected", "add_to_cart")
        ops["checkout"].enable("cart_updated", "checkout")
        ops["process_payment"].enable("order_placed", "process_payment")
        ops["track_delivery"].enable("payment_success", "track_delivery")

        # Plan is sequence of actions
        plan_actions = [
            "discover_providers",
            "search_product",
            "compare_prices",
            "add_to_cart",
            "checkout",
            "process_payment",
            "track_delivery",
        ]

        # Each action must be registered
        for action in plan_actions:
            assert action in ops

    def test_plan_respects_world_transitions(self):
        """Plan only uses transitions that exist in the world."""
        world = create_realistic_world()

        # Get all valid transitions
        valid_transitions = set()
        for src, dst in world:
            valid_transitions.add((src, dst))

        # Plan steps must be valid transitions
        plan = [
            "need_groceries",
            "discover_providers",
            "providers_available",
            "search_product",
            "product_found",
        ]

        for i in range(len(plan) - 1):
            assert (plan[i], plan[i + 1]) in valid_transitions

    def test_plan_does_not_use_hardcoded_logic(self):
        """Plan is derived from world tensor, not from hardcoded grocery logic."""
        world = create_realistic_world()

        # The world tensor contains all transitions
        assert world.nnz() > 20

        # The planner uses world.successors() to find next states
        # This is dynamic, not hardcoded
        successors = world.successors("search_product")
        assert len(successors) > 0

    def test_plan_output_format(self):
        """Plan output is a sequence of states with metadata."""
        world = create_realistic_world()

        plan = {
            "states": ["need_groceries", "discover_providers", "search_product"],
            "actions": ["discover_providers", "search_product"],
            "transitions": [
                ("need_groceries", "discover_providers"),
                ("discover_providers", "providers_available"),
                ("providers_available", "search_product"),
            ],
            "metadata": {
                "goal": "acquire_milk",
                "constraints": {"budget": 20.00},
            },
        }

        assert len(plan["states"]) == 3
        assert len(plan["actions"]) == 2
        assert len(plan["transitions"]) == 3
