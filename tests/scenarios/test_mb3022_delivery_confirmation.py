"""MB-3022 Delivery Confirmation — customer confirms receipt scenario.

Customer confirms receipt.

Per explicit design choice ("Closes the order lifecycle"): system
delivery alone (MB-3021's mark_shipment_delivered, which sets an
order's status to "delivered" once every shipment covering it is) does
not finish the transaction — the customer's own sign-off does. Built
kernel/domains/logistics.py::confirm_receipt() for this: requires an
order to already be "delivered" before advancing it to a new terminal
"completed" status. A one-way, one-time transition — refuses on an
order that's still in progress, was cancelled, or was already
confirmed — same discipline as the shipment lifecycle's own guards
(MB-3019).
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.logistics import (
    LogisticsCapability,
    confirm_receipt,
    create_shipment,
    mark_shipment_delivered,
    mark_shipment_in_transit,
    pack_order,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph


def _packages() -> list:
    return pack_order([{"id": "p1", "name": "Apples", "qty": 3}])["packages"]


def _delivered_order(kg: KnowledgeGraph, order_id: str) -> None:
    kg.add_entity(order_id, EntityType.EVENT, "Grocery Order", {"status": "confirmed"})
    shipment_id = create_shipment(kg, order_id, _packages())["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)
    mark_shipment_delivered(kg, shipment_id)


def test_mb3022_cannot_confirm_receipt_before_delivery():
    kg = KnowledgeGraph()
    kg.add_entity("ORD-EARLY", EntityType.EVENT, "Grocery Order", {"status": "confirmed"})

    result = confirm_receipt(kg, "ORD-EARLY", actor_id="alice")

    assert result["success"] is False
    assert "delivered" in result["error"]
    assert kg.get_entity("ORD-EARLY").attributes["status"] == "confirmed"


def test_mb3022_confirm_receipt_completes_the_order():
    kg = KnowledgeGraph()
    _delivered_order(kg, "ORD-A")

    result = confirm_receipt(kg, "ORD-A", actor_id="alice")

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["confirmed_by"] == "alice"
    order = kg.get_entity("ORD-A")
    assert order.attributes["status"] == "completed"
    assert "completed_at" in order.attributes
    assert order.attributes["confirmed_by"] == "alice"


def test_mb3022_cannot_confirm_receipt_twice():
    kg = KnowledgeGraph()
    _delivered_order(kg, "ORD-B")
    confirm_receipt(kg, "ORD-B", actor_id="alice")

    result = confirm_receipt(kg, "ORD-B", actor_id="alice")

    assert result["success"] is False
    assert "completed" in result["error"]


def test_mb3022_cannot_confirm_receipt_on_a_cancelled_order():
    kg = KnowledgeGraph()
    kg.add_entity("ORD-CANCELLED", EntityType.EVENT, "Grocery Order", {"status": "cancelled"})

    result = confirm_receipt(kg, "ORD-CANCELLED")

    assert result["success"] is False


def test_mb3022_unknown_order_is_an_honest_failure():
    kg = KnowledgeGraph()

    result = confirm_receipt(kg, "does-not-exist")

    assert result["success"] is False
    assert "no such order" in result["error"]


def test_mb3022_confirm_receipt_via_capability():
    kg = KnowledgeGraph()
    _delivered_order(kg, "ORD-C")
    cap = LogisticsCapability()

    assert cap.can_handle("confirm_receipt")
    assert cap.invoke("confirm_receipt", kg, "ORD-C", actor_id="bob")["success"] is True
    assert kg.get_entity("ORD-C").attributes["confirmed_by"] == "bob"
