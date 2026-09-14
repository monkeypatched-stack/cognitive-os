"""MB-3026 Return Approval — approve return scenario.

Approve return.

MB-3025's return_order() files a request but never moves money or
inventory. Built kernel/domains/grocery.py::approve_return() as the
separate, deliberate step that actually does: refunds the wallet that
paid for the order and restocks each item — the same mechanics as
cancel_order()'s undo. Only ever acts on an order actually in
"return_requested" — there's nothing to approve for an order nobody
asked to return, and an already-approved/returned order can't be
approved twice. Wired to a new ApproveReturnCapability wrapper,
mirroring ReturnOrderCapability/CancelOrderCapability's own on-demand
convention.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.grocery import (
    ApproveReturnCapability,
    approve_return,
    return_order,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

ORDER_ID = "ORD-1"


def _seed_delivered_order() -> KnowledgeGraph:
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
            "status": "delivered",
            "paid_wallet_id": "wallet_1",
            "paid_amount": 9.0,
            "payment_status": "paid",
        },
    )
    return kg


def _requested_return(kg: KnowledgeGraph) -> None:
    result = return_order(kg, ORDER_ID, actor_id="alice", reason="wrong item")
    assert result["success"] is True


def test_mb3026_cannot_approve_a_return_that_was_never_requested():
    kg = _seed_delivered_order()

    result = approve_return(kg, ORDER_ID, approved_by="merchant")

    assert result["success"] is False
    assert "no pending return request" in result["error"]


def test_mb3026_approving_a_pending_request_refunds_and_restocks():
    kg = _seed_delivered_order()
    _requested_return(kg)

    result = approve_return(kg, ORDER_ID, approved_by="merchant")

    assert result["success"] is True
    assert result["refunded"] == 9.0
    assert kg.get_entity("wallet_1").attributes["balance"] == 29.0
    assert kg.get_entity("prod_1").attributes["quantity"] == 7
    order = kg.get_entity(ORDER_ID)
    assert order.attributes["status"] == "returned"
    assert order.attributes["approved_by"] == "merchant"


def test_mb3026_cannot_approve_the_same_return_twice():
    kg = _seed_delivered_order()
    _requested_return(kg)
    approve_return(kg, ORDER_ID, approved_by="merchant")

    result = approve_return(kg, ORDER_ID, approved_by="merchant")

    assert result["success"] is False


def test_mb3026_unpaid_order_cannot_be_approved():
    kg = _seed_delivered_order()
    _requested_return(kg)
    kg.update_entity(ORDER_ID, attributes={"payment_status": "pending"})

    result = approve_return(kg, ORDER_ID, approved_by="merchant")

    assert result["success"] is False


def test_mb3026_unknown_order_is_an_honest_failure():
    kg = KnowledgeGraph()

    result = approve_return(kg, "does-not-exist", approved_by="merchant")

    assert result["success"] is False
    assert "no such order" in result["error"]


def test_mb3026_approve_return_via_capability_wrapper():
    kg = _seed_delivered_order()
    _requested_return(kg)
    cap = ApproveReturnCapability()

    result = cap.handle(
        {
            "context": {
                "knowledge_graph": kg,
                "order_id": ORDER_ID,
                "actor_id": "merchant",
            }
        }
    )

    assert result["success"] is True
    assert kg.get_entity(ORDER_ID).attributes["status"] == "returned"
