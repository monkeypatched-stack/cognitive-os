"""MB-3045 Carrier Delay — weather delay scenario.

Weather delay.

Built kernel/domains/logistics.py::report_shipment_delay() — records a
real delay on a shipment already in transit (e.g. weather) without
changing its status (still genuinely "in_transit", just running late),
appending a delay record and updating the estimated arrival when a new
one is given. Only valid on a shipment that's actually "in_transit" —
a delay reported before a shipment has even shipped, or after it's
already delivered/lost (MB-3043), doesn't mean anything.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.logistics import (
    LogisticsCapability,
    create_shipment,
    mark_shipment_delivered,
    mark_shipment_in_transit,
    pack_order,
    report_shipment_delay,
)
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph


def _packages() -> list:
    return pack_order([{"id": "p1", "name": "Apples", "qty": 3}])["packages"]


def test_mb3045_cannot_report_a_delay_before_in_transit():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-1", _packages())["shipment_id"]

    result = report_shipment_delay(kg, shipment_id, reason="weather")

    assert result["success"] is False


def test_mb3045_weather_delay_updates_eta_without_changing_status():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-1", _packages())["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)

    result = report_shipment_delay(kg, shipment_id, reason="severe weather", new_eta=1234567890.0)

    assert result["success"] is True
    assert result["delay_count"] == 1
    shipment = kg.get_entity(shipment_id)
    assert shipment.attributes["estimated_arrival"] == 1234567890.0
    assert shipment.attributes["status"] == "in_transit"


def test_mb3045_multiple_delays_accumulate():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-1", _packages())["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)
    report_shipment_delay(kg, shipment_id, reason="first weather event")

    result = report_shipment_delay(kg, shipment_id, reason="second weather event")

    assert result["delay_count"] == 2


def test_mb3045_cannot_report_a_delay_after_delivered():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-1", _packages())["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)
    mark_shipment_delivered(kg, shipment_id)

    result = report_shipment_delay(kg, shipment_id, reason="too late")

    assert result["success"] is False


def test_mb3045_report_shipment_delay_via_capability():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-1", _packages())["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)
    cap = LogisticsCapability()

    assert cap.can_handle("report_shipment_delay")
    result = cap.invoke("report_shipment_delay", kg, shipment_id, "weather")

    assert result["success"] is True
