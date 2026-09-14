"""MB-3006 Recommendation Engine — "customers also bought" scenario.

Customers also bought.

Verify:
    - Recommendation workflow.

Backed by kernel/domains/commerce.py::customers_also_bought(), mined
directly from real Order history: kernel/domains/grocery.py::
OrderCreationCapability already persists every confirmed order as an
EntityType.EVENT entity with attributes["items"] (a list of
{"product_id": ...} line items) — real purchase data was already flowing
through the system, unlike MB-3005's images/reviews/variants, which had
no backing data at all. The recommendation workflow is: real customers
place real Orders -> co-purchase signal accumulates across those Orders
-> querying a product surfaces what other customers bought alongside it,
ranked by how often that happened. No separate, hand-maintained
recommendation dataset that could drift from actual purchases.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    CommerceCapabilityBus,
    customers_also_bought,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

MILK = "prod_milk"
BREAD = "prod_bread"
EGGS = "prod_eggs"
SODA = "prod_soda"


def _place_order(kg: KnowledgeGraph, order_id: str, buyer_id: str, product_ids: list[str]) -> None:
    kg.add_entity(
        order_id,
        EntityType.EVENT,
        "Grocery Order",
        {
            "buyer_id": buyer_id,
            "items": [{"product_id": pid} for pid in product_ids],
        },
    )


def _seed_purchase_history() -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(MILK, EntityType.ASSET, "Milk", {"price": 3.99})
    kg.add_entity(BREAD, EntityType.ASSET, "Bread", {"price": 2.99})
    kg.add_entity(EGGS, EntityType.ASSET, "Eggs", {"price": 4.49})
    kg.add_entity(SODA, EntityType.ASSET, "Soda", {"price": 1.99})

    # Three different customers all bought milk + bread together; one of
    # them also bought eggs. A fourth customer bought only soda, never
    # alongside milk.
    _place_order(kg, "ord1", "alice", [MILK, BREAD])
    _place_order(kg, "ord2", "bob", [MILK, BREAD])
    _place_order(kg, "ord3", "carol", [MILK, BREAD, EGGS])
    _place_order(kg, "ord4", "dave", [SODA])
    return kg


def test_mb3006_customers_also_bought_ranks_by_co_purchase_frequency():
    kg = _seed_purchase_history()

    recommendations = customers_also_bought(kg, MILK)

    assert [r.entity_id for r in recommendations] == [BREAD, EGGS]
    assert recommendations[0].name == "Bread"
    assert recommendations[0].co_purchase_count == 3
    assert recommendations[1].co_purchase_count == 1

    # Never recommends the product to itself, and never surfaces a
    # product that real customers never actually bought alongside it.
    assert MILK not in {r.entity_id for r in recommendations}
    assert SODA not in {r.entity_id for r in recommendations}


def test_mb3006_respects_limit():
    kg = _seed_purchase_history()
    assert len(customers_also_bought(kg, MILK, limit=1)) == 1


def test_mb3006_product_never_ordered_returns_empty_not_error():
    kg = _seed_purchase_history()

    # A real product that simply has no purchase history yet.
    kg.add_entity("prod_never_bought", EntityType.ASSET, "New Product", {"price": 9.99})
    assert customers_also_bought(kg, "prod_never_bought") == ()

    # No orders at all in a brand-new catalog.
    assert customers_also_bought(KnowledgeGraph(), MILK) == ()


def test_mb3006_recommendation_workflow_via_capability_bus():
    kg = _seed_purchase_history()
    bus = CommerceCapabilityBus([CommerceCapability()])

    found = bus.discover_operation("customers_also_bought")
    assert found is not None, "commerce capability must expose a customers_also_bought operation"

    via_bus = bus.invoke("customers_also_bought", kg, MILK)
    assert via_bus == customers_also_bought(kg, MILK)
