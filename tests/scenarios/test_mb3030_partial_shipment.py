"""MB-3030 Partial Shipment — multiple packages scenario.

Multiple packages.

pack_order() (MB-3018) already splits ONE shipment's items into
multiple physical boxes, and create_shipment()/track_order() (MB-3019/
MB-3020) already support multiple independent shipments per order. The
real gap: nothing decided WHEN an order should split into more than one
shipment in the first place. Per explicit design choice ("availability
split"): built kernel/domains/logistics.py::create_partial_shipments()
for this — whatever's actually available ships NOW as its own shipment;
any line item with a currently PENDING backorder (MB-3016's
place_backorder()/fulfill_backorders()) for this actor ships LATER,
once that backorder is actually fulfilled. A single out-of-stock item
no longer holds an entire order's shipment hostage.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.grocery import fulfill_backorders, place_backorder
from src.monkey_brain.kernel.domains.logistics import (
    LogisticsCapability,
    create_partial_shipments,
    track_order,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

PRODUCTS = [
    {"id": "p1", "name": "Apples", "qty": 3},
    {"id": "p2", "name": "Milk", "qty": 1},
]


def _seed(p1_qty: int = 10, p2_qty: int = 0) -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity("p1", EntityType.ASSET, "Apples", {"price": 2.0, "quantity": p1_qty})
    kg.add_entity("p2", EntityType.ASSET, "Milk", {"price": 3.0, "quantity": p2_qty})
    return kg


def test_mb3030_everything_available_ships_in_one_shipment():
    kg = _seed(p2_qty=5)

    result = create_partial_shipments(kg, "ORD-1", "alice", PRODUCTS)

    assert result["success"] is True
    assert len(result["shipments"]) == 1
    assert result["shipped_item_ids"] == ["p1", "p2"]
    assert result["pending_item_ids"] == []
    assert result["partial"] is False


def test_mb3030_backordered_item_splits_into_a_partial_shipment():
    kg = _seed(p2_qty=0)
    place_backorder(kg, "p2", "alice", qty=1)

    result = create_partial_shipments(kg, "ORD-2", "alice", PRODUCTS)

    assert result["success"] is True
    assert result["partial"] is True
    assert result["shipped_item_ids"] == ["p1"]
    assert result["pending_item_ids"] == ["p2"]
    assert len(result["shipments"]) == 1

    tracked = track_order(kg, "ORD-2")
    assert tracked["shipment_count"] == 1


def test_mb3030_another_actors_backorder_does_not_hold_up_this_shipment():
    kg = _seed(p2_qty=0)
    place_backorder(kg, "p2", "bob", qty=1)

    result = create_partial_shipments(kg, "ORD-3", "alice", PRODUCTS)

    assert result["partial"] is False
    assert result["shipped_item_ids"] == ["p1", "p2"]


def test_mb3030_everything_backordered_creates_no_shipment_at_all():
    kg = _seed(p1_qty=0, p2_qty=0)
    place_backorder(kg, "p1", "alice", qty=3)
    place_backorder(kg, "p2", "alice", qty=1)

    result = create_partial_shipments(kg, "ORD-4", "alice", PRODUCTS)

    assert result["success"] is True
    assert result["shipments"] == []
    assert result["pending_item_ids"] == ["p1", "p2"]
    assert result["partial"] is False


def test_mb3030_fulfilled_backorder_is_ready_to_ship():
    kg = _seed(p2_qty=0)
    place_backorder(kg, "p2", "alice", qty=1)
    kg.update_entity("p2", attributes={"quantity": 5})
    fulfill_backorders(kg, "p2")

    result = create_partial_shipments(kg, "ORD-5", "alice", PRODUCTS)

    assert result["partial"] is False
    assert result["shipped_item_ids"] == ["p1", "p2"]


def test_mb3030_create_partial_shipments_via_capability():
    kg = _seed(p2_qty=5)
    cap = LogisticsCapability()

    assert cap.can_handle("create_partial_shipments")
    result = cap.invoke("create_partial_shipments", kg, "ORD-6", "alice", PRODUCTS)
    assert result["success"] is True
