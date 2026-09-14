"""MB-3019 Shipping — create shipment scenario.

Create shipment.

No shipment/tracking concept existed anywhere in kernel/domains/*.py.
assign_picker() (MB-3017) and pack_order() (MB-3018) are pure
computations that persist nothing; DeliveryCapability (grocery.py)
schedules a rider and returns a delivery_id, but that id is never
persisted either — nothing in the KG could be looked up later by
tracking number or have its status queried or advanced.

Full lifecycle scope (per explicit design choice): built
kernel/domains/logistics.py::create_shipment() to persist a real
Shipment entity (tracking number, order_id, packages from pack_order(),
rider/carrier, status="created"), plus mark_shipment_in_transit()/
mark_shipment_delivered() to advance it through a strict, linear
lifecycle (created -> in_transit -> delivered) — no skipping ahead, no
moving backward, no re-applying the same transition twice — and
get_shipment() to look up current status/history.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.logistics import (
    LogisticsCapability,
    create_shipment,
    get_shipment,
    mark_shipment_delivered,
    mark_shipment_in_transit,
    pack_order,
)
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph


def _shipped_packages() -> list:
    return pack_order([{"id": "p1", "name": "Apples", "qty": 3}])["packages"]


def test_mb3019_create_shipment_persists_a_trackable_record():
    kg = KnowledgeGraph()

    result = create_shipment(kg, "ORD-123", _shipped_packages(), rider_id="rider_1", carrier="InternalFleet")

    assert result["success"] is True
    assert result["status"] == "created"
    assert result["tracking_number"].startswith("TRK-")

    got = get_shipment(kg, result["shipment_id"])
    assert got["success"] is True
    assert got["status"] == "created"
    assert got["order_id"] == "ORD-123"
    assert len(got["history"]) == 1


def test_mb3019_shipment_advances_through_the_full_lifecycle():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-124", _shipped_packages())["shipment_id"]

    in_transit = mark_shipment_in_transit(kg, shipment_id)
    assert in_transit["success"] is True
    assert in_transit["status"] == "in_transit"

    delivered = mark_shipment_delivered(kg, shipment_id)
    assert delivered["success"] is True
    assert delivered["status"] == "delivered"

    final = get_shipment(kg, shipment_id)
    assert [h["status"] for h in final["history"]] == [
        "created",
        "in_transit",
        "delivered",
    ]


def test_mb3019_cannot_mark_delivered_before_in_transit():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-125", _shipped_packages())["shipment_id"]

    result = mark_shipment_delivered(kg, shipment_id)

    assert result["success"] is False
    assert "created" in result["error"]
    assert get_shipment(kg, shipment_id)["status"] == "created"


def test_mb3019_cannot_re_apply_the_same_transition_twice():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-126", _shipped_packages())["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)

    result = mark_shipment_in_transit(kg, shipment_id)

    assert result["success"] is False


def test_mb3019_cannot_move_backward_from_delivered():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-127", _shipped_packages())["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)
    mark_shipment_delivered(kg, shipment_id)

    result = mark_shipment_in_transit(kg, shipment_id)

    assert result["success"] is False
    assert get_shipment(kg, shipment_id)["status"] == "delivered"


def test_mb3019_unknown_shipment_is_an_honest_failure():
    kg = KnowledgeGraph()

    assert get_shipment(kg, "does-not-exist")["success"] is False
    assert mark_shipment_in_transit(kg, "does-not-exist")["success"] is False
    assert mark_shipment_delivered(kg, "does-not-exist")["success"] is False


def test_mb3019_shipment_lifecycle_via_capability_bus():
    kg = KnowledgeGraph()
    cap = LogisticsCapability()

    assert cap.can_handle("create_shipment")
    assert cap.can_handle("mark_shipment_in_transit")
    assert cap.can_handle("mark_shipment_delivered")
    assert cap.can_handle("get_shipment")

    created = cap.invoke("create_shipment", kg, "ORD-128", _shipped_packages())
    assert created["success"] is True

    cap.invoke("mark_shipment_in_transit", kg, created["shipment_id"])
    delivered = cap.invoke("mark_shipment_delivered", kg, created["shipment_id"])
    assert delivered["status"] == "delivered"
