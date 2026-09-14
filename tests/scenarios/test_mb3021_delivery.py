"""MB-3021 Delivery — order delivered scenario.

Order delivered.

An order's persisted status (OrderCreationCapability, grocery.py) only
ever moved from "confirmed" to "cancelled" (cancel_order) — nothing
ever set it to "delivered", even once every shipment covering it
actually was (MB-3019/MB-3020). Extended
kernel/domains/logistics.py::mark_shipment_delivered() to close that
loop: reuses track_order()'s existing "least advanced status wins"
rule to check whether THIS shipment was the last outstanding one for
its order, and if so marks the order entity delivered too — a
multi-shipment order is never marked delivered until every shipment
that makes it up actually is. Silently leaves the order alone if it
isn't persisted in this KG, same as create_shipment() never requiring
one to be.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.logistics import (
    create_shipment,
    mark_shipment_delivered,
    mark_shipment_in_transit,
    pack_order,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph


def _packages() -> list:
    return pack_order([{"id": "p1", "name": "Apples", "qty": 3}])["packages"]


def test_mb3021_single_shipment_order_marked_delivered():
    kg = KnowledgeGraph()
    kg.add_entity("ORD-A", EntityType.EVENT, "Grocery Order", {"status": "confirmed"})
    shipment_id = create_shipment(kg, "ORD-A", _packages())["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)

    result = mark_shipment_delivered(kg, shipment_id)

    assert result["success"] is True
    assert result["order_delivered"] is True
    order = kg.get_entity("ORD-A")
    assert order.attributes["status"] == "delivered"
    assert "delivered_at" in order.attributes


def test_mb3021_multi_shipment_order_waits_for_every_shipment():
    kg = KnowledgeGraph()
    kg.add_entity("ORD-B", EntityType.EVENT, "Grocery Order", {"status": "confirmed"})
    first = create_shipment(kg, "ORD-B", _packages())["shipment_id"]
    second = create_shipment(kg, "ORD-B", _packages())["shipment_id"]
    mark_shipment_in_transit(kg, first)

    first_result = mark_shipment_delivered(kg, first)

    assert first_result["order_delivered"] is False
    assert kg.get_entity("ORD-B").attributes["status"] == "confirmed"

    mark_shipment_in_transit(kg, second)
    second_result = mark_shipment_delivered(kg, second)

    assert second_result["order_delivered"] is True
    assert kg.get_entity("ORD-B").attributes["status"] == "delivered"


def test_mb3021_shipment_for_order_with_no_persisted_entity_does_not_crash():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-GHOST", _packages())["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)

    result = mark_shipment_delivered(kg, shipment_id)

    assert result["success"] is True
    assert result["order_delivered"] is False
