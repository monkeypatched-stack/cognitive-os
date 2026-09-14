"""MB-3028 Inventory Restock — returned inventory restored scenario.

Returned inventory restored.

Like MB-3010/3012/3013/3015, no new code was needed: approve_return()
(MB-3026) already restocks every line item of a returned order back
onto its product's attributes["quantity"] as part of reversing the
purchase — the exact symmetric inverse of confirm_reservation()'s real
stock decrement, not a separate, parallel restock mechanism that could
drift from how stock is actually depleted. This file verifies that
directly: a real try_reserve()/confirm_reservation() purchase followed
by a full return_order()/approve_return() cycle restores the product's
quantity to precisely its starting value, plus the multi-item,
stock-increment (not overwrite), and discontinued-product edge cases.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.grocery import (
    approve_return,
    confirm_reservation,
    return_order,
    try_reserve,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph


def test_mb3028_restock_is_symmetric_with_a_real_purchase_decrement():
    kg = KnowledgeGraph()
    kg.add_entity(
        "wallet_1",
        EntityType.ACCOUNT,
        "Alice Wallet",
        {"account_type": "debit", "balance": 20.0},
    )
    kg.add_entity("prod_1", EntityType.ASSET, "Oat Milk", {"price": 4.5, "quantity": 10})

    # Real purchase path: reserve, then confirm — the actual stock
    # decrement mechanism, not a hand-seeded order fixture.
    ok, msg = try_reserve(kg, "prod_1", "alice", qty=3)
    assert ok, msg
    ok, msg = confirm_reservation(kg, "prod_1", "alice")
    assert ok, msg
    assert kg.get_entity("prod_1").attributes["quantity"] == 7

    kg.add_entity(
        "ORD-1",
        EntityType.EVENT,
        "Grocery Order",
        {
            "items": [{"product_id": "prod_1", "qty": 3}],
            "total": 13.5,
            "status": "delivered",
            "paid_wallet_id": "wallet_1",
            "paid_amount": 13.5,
            "payment_status": "paid",
        },
    )

    return_order(kg, "ORD-1", actor_id="alice")
    result = approve_return(kg, "ORD-1", approved_by="merchant")

    assert result["success"] is True
    assert kg.get_entity("prod_1").attributes["quantity"] == 10


def _seed_multi_item_order() -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(
        "wallet_1",
        EntityType.ACCOUNT,
        "Alice Wallet",
        {"account_type": "debit", "balance": 0.0},
    )
    kg.add_entity("prod_1", EntityType.ASSET, "Oat Milk", {"price": 4.5, "quantity": 5})
    kg.add_entity("prod_2", EntityType.ASSET, "Bread", {"price": 3.0, "quantity": 2})
    kg.add_entity(
        "ORD-1",
        EntityType.EVENT,
        "Grocery Order",
        {
            "items": [
                {"product_id": "prod_1", "qty": 3},
                {"product_id": "prod_2", "qty": 1},
            ],
            "total": 16.5,
            "status": "delivered",
            "paid_wallet_id": "wallet_1",
            "paid_amount": 16.5,
            "payment_status": "paid",
        },
    )
    return kg


def test_mb3028_every_line_item_is_restocked():
    kg = _seed_multi_item_order()
    return_order(kg, "ORD-1", actor_id="alice")

    result = approve_return(kg, "ORD-1", approved_by="merchant")

    assert kg.get_entity("prod_1").attributes["quantity"] == 8
    assert kg.get_entity("prod_2").attributes["quantity"] == 3
    assert {"product_id": "prod_1", "qty": 3} in result["restocked"]
    assert {"product_id": "prod_2", "qty": 1} in result["restocked"]


def test_mb3028_restock_increments_current_stock_not_overwrites_it():
    kg = _seed_multi_item_order()
    # Simulate a real restock shipment arriving before the return is approved.
    kg.update_entity("prod_1", attributes={"quantity": 100})
    return_order(kg, "ORD-1", actor_id="alice")

    approve_return(kg, "ORD-1", approved_by="merchant")

    assert kg.get_entity("prod_1").attributes["quantity"] == 103


def test_mb3028_discontinued_product_is_silently_skipped():
    kg = _seed_multi_item_order()
    kg._entities.pop("prod_2")  # discontinued since purchase
    return_order(kg, "ORD-1", actor_id="alice")

    result = approve_return(kg, "ORD-1", approved_by="merchant")

    assert result["success"] is True
    assert kg.get_entity("prod_1").attributes["quantity"] == 8
    assert result["restocked"] == [{"product_id": "prod_1", "qty": 3}]
