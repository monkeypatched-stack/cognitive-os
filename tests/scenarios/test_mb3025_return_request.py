"""MB-3025 Return Request — customer requests return scenario.

Customer requests return.

"return_order" was already declared as a CommerceCapability operation
(commerce.py's _DEFAULT_OPERATIONS) — discoverable, but invoking it hit
_legacy_handler's NotImplementedError fallback: nothing actually
implemented it. Built kernel/domains/grocery.py::return_order() for
this.

Retrofitted (per explicit design choice, once MB-3026 made clear
returns are two-phase): return_order() files a REQUEST — it does not
refund or restock — moving the order to "return_requested". The actual
refund/restock only happens on approve_return() (MB-3026), a separate,
deliberate merchant-side step. return_order() only accepts an order
that actually reached "delivered" or "completed" (MB-3021/MB-3022's own
terminal statuses); an order that was never delivered isn't returnable,
it's cancellable — this refuses it by name rather than silently
cancelling under the wrong label. Wired into
CommerceCapability._legacy_handler (closing the original gap) and a new
ReturnOrderCapability wrapper, mirroring CancelOrderCapability's own
on-demand convention.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    CommerceCapabilityBus,
)
from src.monkey_brain.kernel.domains.grocery import ReturnOrderCapability, return_order
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


def test_mb3025_cannot_request_a_return_for_an_order_that_was_never_delivered():
    kg = _seed_paid_order(status="confirmed")

    result = return_order(kg, ORDER_ID, actor_id="alice")

    assert result["success"] is False
    assert "never delivered" in result["error"]
    assert kg.get_entity(ORDER_ID).attributes["status"] == "confirmed"


def test_mb3025_delivered_order_can_request_a_return_without_refunding():
    kg = _seed_paid_order(status="delivered")

    result = return_order(kg, ORDER_ID, actor_id="alice", reason="wrong item")

    assert result["success"] is True
    assert result["status"] == "return_requested"
    # A request alone must never move money or restock — that's
    # approve_return()'s (MB-3026) job.
    assert kg.get_entity("wallet_1").attributes["balance"] == 20.0
    assert kg.get_entity("prod_1").attributes["quantity"] == 5
    order = kg.get_entity(ORDER_ID)
    assert order.attributes["status"] == "return_requested"
    assert order.attributes["return_reason"] == "wrong item"
    assert order.attributes["return_requested_by"] == "alice"


def test_mb3025_completed_order_can_also_request_a_return():
    kg = _seed_paid_order(status="completed")

    result = return_order(kg, ORDER_ID)

    assert result["success"] is True
    assert result["status"] == "return_requested"


def test_mb3025_cannot_request_a_return_twice():
    kg = _seed_paid_order(status="delivered")
    return_order(kg, ORDER_ID)

    result = return_order(kg, ORDER_ID)

    assert result["success"] is False


def test_mb3025_unpaid_order_cannot_request_a_return():
    kg = _seed_paid_order(status="delivered")
    kg.update_entity(ORDER_ID, attributes={"payment_status": "pending"})

    result = return_order(kg, ORDER_ID)

    assert result["success"] is False


def test_mb3025_unknown_order_is_an_honest_failure():
    kg = KnowledgeGraph()

    result = return_order(kg, "does-not-exist")

    assert result["success"] is False
    assert "no such order" in result["error"]


def test_mb3025_return_request_via_capability_wrapper():
    kg = _seed_paid_order(status="delivered")
    cap = ReturnOrderCapability()

    result = cap.handle(
        {
            "context": {
                "knowledge_graph": kg,
                "order_id": ORDER_ID,
                "actor_id": "alice",
                "return_reason": "too small",
            }
        }
    )

    assert result["success"] is True
    assert result["status"] == "return_requested"


def test_mb3025_return_order_via_commerce_capability_bus():
    kg = _seed_paid_order(status="delivered")
    bus = CommerceCapabilityBus([CommerceCapability()])

    assert bus.discover_operation("return_order") is not None
    result = bus.invoke("return_order", kg, ORDER_ID, actor_id="alice")

    assert result["success"] is True
    assert result["status"] == "return_requested"
