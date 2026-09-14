"""MB-3050 Analytics — sales/inventory/customers update scenario.

Update: sales, inventory, customers.

No merchant-facing analytics existed. Built kernel/domains/commerce.py::
get_store_analytics() — a real-time snapshot computed fresh from actual
KG state on every call (the same "always current, never stale" principle
trace_supply_chain()/get_product_detail() already follow), not a
separately maintained dashboard number that could drift. Ownership-
checked like every other merchant-facing operation (require_store_owner()).

- sales: revenue/order count from real, PAID orders only (a pending
  order, or one from a different store entirely, must never count).
- inventory: total stock, out-of-stock, and low-stock counts, scoped to
  currently LISTED products — a removed listing (MB-3040) doesn't count.
- customers: distinct buyers and repeat-customer count, mined the same
  way customers_also_bought() mines real order history.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    get_store_analytics,
    list_product,
    onboard_merchant,
    remove_product,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

MERCHANT_ID = "merchant_bob"


def _seed_store_with_activity() -> tuple[KnowledgeGraph, str]:
    kg = KnowledgeGraph()
    store_id = onboard_merchant(kg, MERCHANT_ID, "Bob's Store")["store_id"]
    p1 = list_product(kg, store_id, MERCHANT_ID, "Oat Milk", price=4.5, quantity=0)["product_id"]
    p2 = list_product(kg, store_id, MERCHANT_ID, "Bread", price=3.0, quantity=3)["product_id"]
    p3 = list_product(kg, store_id, MERCHANT_ID, "Eggs", price=6.0, quantity=50)["product_id"]
    p4 = list_product(kg, store_id, MERCHANT_ID, "Discontinued Item", price=1.0, quantity=10)["product_id"]
    remove_product(kg, p4, MERCHANT_ID)

    kg.add_entity(
        "ORD-1",
        EntityType.EVENT,
        "Order",
        {
            "items": [{"product_id": p2, "qty": 1}],
            "total": 3.0,
            "buyer_id": "alice",
            "payment_status": "paid",
        },
    )
    kg.add_entity(
        "ORD-2",
        EntityType.EVENT,
        "Order",
        {
            "items": [{"product_id": p3, "qty": 2}],
            "total": 12.0,
            "buyer_id": "alice",
            "payment_status": "paid",
        },
    )
    kg.add_entity(
        "ORD-3",
        EntityType.EVENT,
        "Order",
        {
            "items": [{"product_id": p3, "qty": 1}],
            "total": 6.0,
            "buyer_id": "carol",
            "payment_status": "paid",
        },
    )
    kg.add_entity(
        "ORD-4-unpaid",
        EntityType.EVENT,
        "Order",
        {
            "items": [{"product_id": p3, "qty": 1}],
            "total": 6.0,
            "buyer_id": "dave",
            "payment_status": "pending",
        },
    )
    kg.add_entity(
        "ORD-other-store",
        EntityType.EVENT,
        "Order",
        {
            "items": [{"product_id": "unrelated_product"}],
            "total": 999.0,
            "buyer_id": "eve",
            "payment_status": "paid",
        },
    )
    return kg, store_id


def test_mb3050_sales_only_counts_real_paid_orders_for_this_store():
    kg, store_id = _seed_store_with_activity()

    result = get_store_analytics(kg, store_id, MERCHANT_ID)

    assert result["success"] is True
    assert result["sales"]["total_revenue"] == 21.0
    assert result["sales"]["order_count"] == 3


def test_mb3050_inventory_excludes_removed_listings_and_flags_stock_levels():
    kg, store_id = _seed_store_with_activity()

    result = get_store_analytics(kg, store_id, MERCHANT_ID)

    inventory = result["inventory"]
    assert inventory["listed_product_count"] == 3
    assert inventory["total_stock"] == 53
    assert inventory["out_of_stock_count"] == 1
    assert inventory["low_stock_count"] == 1


def test_mb3050_customers_counts_distinct_and_repeat_buyers():
    kg, store_id = _seed_store_with_activity()

    result = get_store_analytics(kg, store_id, MERCHANT_ID)

    customers = result["customers"]
    assert customers["customer_count"] == 2
    assert customers["repeat_customer_count"] == 1


def test_mb3050_cannot_view_another_merchants_store_analytics():
    kg, store_id = _seed_store_with_activity()

    result = get_store_analytics(kg, store_id, "merchant_eve")

    assert result["success"] is False
    assert "does not own" in result["error"]


def test_mb3050_unknown_store_is_an_honest_failure():
    kg, _store_id = _seed_store_with_activity()

    result = get_store_analytics(kg, "does-not-exist", MERCHANT_ID)

    assert result["success"] is False
    assert "no such store" in result["error"]


def test_mb3050_get_store_analytics_via_capability():
    kg, store_id = _seed_store_with_activity()
    cap = CommerceCapability()

    assert cap.can_handle("get_store_analytics")
    result = cap.invoke("get_store_analytics", kg, store_id, MERCHANT_ID)

    assert result["success"] is True
