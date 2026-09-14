"""MB-3027 Refund — refund payment scenario.

Refund payment.

"refund_order" was already declared as a CommerceCapability operation
(commerce.py's _DEFAULT_OPERATIONS, same gap pattern as "return_order"
was before MB-3025) — discoverable, but invoking it hit
_legacy_handler's NotImplementedError fallback.

Per explicit design choice ("partial/goodwill refund"): built
kernel/domains/grocery.py::refund_order() as a standalone, partial-
capable refund distinct from cancel_order()/approve_return() — it
credits the wallet WITHOUT restocking inventory or changing the order's
status, for cases where the customer keeps the goods (a damaged-item
credit, a price adjustment, a goodwill gesture).

Correctness fix that came with it: refund_order() tracks
attributes["total_refunded"] cumulatively on the order, and
cancel_order()/approve_return() were updated to read it before
refunding — both now only ever refund what's actually still
outstanding (paid_amount - total_refunded already issued), not the
original paid_amount blindly. Without this, a partial refund followed
by a full cancellation/return would double-refund the same money.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    CommerceCapabilityBus,
)
from src.monkey_brain.kernel.domains.grocery import (
    RefundOrderCapability,
    approve_return,
    cancel_order,
    refund_order,
    return_order,
)
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


def test_mb3027_partial_refund_credits_wallet_without_restocking_or_status_change():
    kg = _seed_paid_order()

    result = refund_order(kg, ORDER_ID, amount=3.0, reason="damaged item, kept goods", refunded_by="agent")

    assert result["success"] is True
    assert result["refunded"] == 3.0
    assert kg.get_entity("wallet_1").attributes["balance"] == 23.0
    assert kg.get_entity("prod_1").attributes["quantity"] == 5
    order = kg.get_entity(ORDER_ID)
    assert order.attributes["status"] == "confirmed"
    assert order.attributes["total_refunded"] == 3.0


def test_mb3027_refund_exceeding_remaining_balance_is_refused():
    kg = _seed_paid_order()
    refund_order(kg, ORDER_ID, amount=3.0)

    result = refund_order(kg, ORDER_ID, amount=100.0)

    assert result["success"] is False
    assert "exceeds" in result["error"]


def test_mb3027_default_amount_refunds_only_the_remaining_balance():
    kg = _seed_paid_order()
    refund_order(kg, ORDER_ID, amount=3.0)

    result = refund_order(kg, ORDER_ID)

    assert result["success"] is True
    assert result["refunded"] == 6.0
    assert kg.get_entity("wallet_1").attributes["balance"] == 29.0


def test_mb3027_fully_refunded_order_cannot_be_refunded_again():
    kg = _seed_paid_order()
    refund_order(kg, ORDER_ID)

    result = refund_order(kg, ORDER_ID)

    assert result["success"] is False
    assert "already been fully refunded" in result["error"]


def test_mb3027_partial_refund_then_cancel_does_not_double_refund():
    kg = _seed_paid_order()
    refund_order(kg, ORDER_ID, amount=3.0, reason="partial credit")

    result = cancel_order(kg, ORDER_ID, actor_id="alice")

    assert result["refunded"] == 6.0
    assert kg.get_entity("wallet_1").attributes["balance"] == 29.0
    assert kg.get_entity(ORDER_ID).attributes["total_refunded"] == 9.0


def test_mb3027_partial_refund_then_approve_return_does_not_double_refund():
    kg = _seed_paid_order(status="delivered")
    refund_order(kg, ORDER_ID, amount=4.0, reason="partial credit")
    return_order(kg, ORDER_ID, actor_id="alice")

    result = approve_return(kg, ORDER_ID, approved_by="merchant")

    assert result["refunded"] == 5.0
    assert kg.get_entity("wallet_1").attributes["balance"] == 29.0


def test_mb3027_unpaid_order_cannot_be_refunded():
    kg = _seed_paid_order()
    kg.update_entity(ORDER_ID, attributes={"payment_status": "pending"})

    result = refund_order(kg, ORDER_ID)

    assert result["success"] is False


def test_mb3027_unknown_order_is_an_honest_failure():
    kg = KnowledgeGraph()

    result = refund_order(kg, "does-not-exist")

    assert result["success"] is False
    assert "no such order" in result["error"]


def test_mb3027_refund_via_capability_wrapper():
    kg = _seed_paid_order()
    cap = RefundOrderCapability()

    result = cap.handle(
        {
            "context": {
                "knowledge_graph": kg,
                "order_id": ORDER_ID,
                "refund_amount": 2.0,
                "actor_id": "agent",
            }
        }
    )

    assert result["success"] is True
    assert result["refunded"] == 2.0


def test_mb3027_refund_order_via_commerce_capability_bus():
    kg = _seed_paid_order()
    bus = CommerceCapabilityBus([CommerceCapability()])

    assert bus.discover_operation("refund_order") is not None
    result = bus.invoke("refund_order", kg, ORDER_ID, amount=1.0)

    assert result["success"] is True
    assert result["refunded"] == 1.0
