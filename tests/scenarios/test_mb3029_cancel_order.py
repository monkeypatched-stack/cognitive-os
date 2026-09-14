"""MB-3029 Cancel Order — before shipping scenario.

Before shipping.

cancel_order() was real, pre-existing code, but had no shipping-status
guard at all — it would happily cancel an order already "delivered" or
"completed", contradicting return_order()'s (MB-3025) own docstring
assumption that cancellation is only for an order that hasn't shipped
yet. Per explicit design choice: added the guard — cancel_order() now
refuses any order that's already shipped (MB-3021's "delivered",
MB-3022's "completed") or already mid-return (MB-3025's
"return_requested", MB-3026's "returned"), pointing callers to
return_order() instead. The symmetric complement of return_order()'s
own "must have shipped" guard, and a dedicated test for the happy path
this ticket names: cancelling a real order before it ships.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.grocery import cancel_order
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

ORDER_ID = "ORD-1"


def _seed_paid_order(status: str = "confirmed") -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(
        "wallet_1",
        EntityType.ACCOUNT,
        "Alice Wallet",
        {"account_type": "debit", "balance": 20.0},
    )
    kg.add_entity("prod_1", EntityType.ASSET, "Oat Milk", {"price": 4.5, "quantity": 5})
    kg.add_entity(
        ORDER_ID,
        EntityType.EVENT,
        "Grocery Order",
        {
            "items": [{"product_id": "prod_1", "qty": 2}],
            "total": 9.0,
            "status": status,
            "paid_wallet_id": "wallet_1",
            "paid_amount": 9.0,
            "payment_status": "paid",
        },
    )
    return kg


def test_mb3029_cancel_before_shipping_refunds_and_restocks():
    kg = _seed_paid_order(status="confirmed")

    result = cancel_order(kg, ORDER_ID, actor_id="alice")

    assert result["success"] is True
    assert result["refunded"] == 9.0
    assert kg.get_entity("wallet_1").attributes["balance"] == 29.0
    assert kg.get_entity("prod_1").attributes["quantity"] == 7
    assert kg.get_entity(ORDER_ID).attributes["status"] == "cancelled"


def test_mb3029_cannot_cancel_an_order_that_already_shipped():
    for status in ("delivered", "completed", "return_requested", "returned"):
        kg = _seed_paid_order(status=status)

        result = cancel_order(kg, ORDER_ID, actor_id="alice")

        assert result["success"] is False, status
        assert "already shipped" in result["error"], status
        assert kg.get_entity("wallet_1").attributes["balance"] == 20.0, status


def test_mb3029_already_cancelled_order_gets_a_distinct_error():
    kg = _seed_paid_order(status="cancelled")

    result = cancel_order(kg, ORDER_ID)

    assert result["success"] is False
    assert "already cancelled" in result["error"]


def test_mb3029_unpaid_order_cannot_be_cancelled():
    kg = _seed_paid_order(status="confirmed")
    kg.update_entity(ORDER_ID, attributes={"payment_status": "pending"})

    result = cancel_order(kg, ORDER_ID)

    assert result["success"] is False


def test_mb3029_unknown_order_is_an_honest_failure():
    kg = KnowledgeGraph()

    result = cancel_order(kg, "does-not-exist")

    assert result["success"] is False
    assert "no such order" in result["error"]
