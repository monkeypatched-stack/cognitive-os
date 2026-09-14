"""MB-3043 Lost Package — carrier loses shipment scenario.

Carrier loses shipment.

Built kernel/domains/logistics.py::mark_shipment_lost() — reuses
_advance_shipment_status()'s same guarded transition machinery as
mark_shipment_in_transit()/mark_shipment_delivered() (MB-3019). Only
ever a valid transition from "in_transit" — a package can't be lost
before it's even shipped, and once "delivered" it's not lost, it's a
different problem (a delivery dispute). issue_replacement_shipment()
(MB-3044) is the recovery path once a shipment reaches "lost".
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.logistics import (
    LogisticsCapability,
    create_shipment,
    mark_shipment_delivered,
    mark_shipment_in_transit,
    mark_shipment_lost,
    pack_order,
)
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph


def _packages() -> list:
    return pack_order([{"id": "p1", "name": "Apples", "qty": 3}])["packages"]


def test_mb3043_cannot_mark_lost_before_in_transit():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-1", _packages())["shipment_id"]

    result = mark_shipment_lost(kg, shipment_id)

    assert result["success"] is False


def test_mb3043_carrier_marks_an_in_transit_shipment_lost():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-1", _packages())["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)

    result = mark_shipment_lost(kg, shipment_id, reported_by="carrier_agent")

    assert result["success"] is True
    assert result["status"] == "lost"
    assert kg.get_entity(shipment_id).attributes["lost_reported_by"] == "carrier_agent"


def test_mb3043_cannot_mark_a_lost_shipment_delivered():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-1", _packages())["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)
    mark_shipment_lost(kg, shipment_id)

    result = mark_shipment_delivered(kg, shipment_id)

    assert result["success"] is False


def test_mb3043_mark_shipment_lost_via_capability():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-1", _packages())["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)
    cap = LogisticsCapability()

    assert cap.can_handle("mark_shipment_lost")
    result = cap.invoke("mark_shipment_lost", kg, shipment_id)

    assert result["success"] is True
