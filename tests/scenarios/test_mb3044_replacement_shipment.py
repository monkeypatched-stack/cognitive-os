"""MB-3044 Replacement Shipment — replacement issued scenario.

Replacement issued.

Built kernel/domains/logistics.py::issue_replacement_shipment() — the
recovery path once mark_shipment_lost() (MB-3043) actually confirms a
shipment is lost, not before (issuing a replacement for a shipment that
was never lost would be a duplicate order, not a recovery). The new
shipment starts its own fresh created -> in_transit -> delivered
lifecycle (create_shipment()), carrying over the same packages/rider/
carrier, linked back to the original via replaces_shipment_id/
replaced_by_shipment_id — a traceable substitution, not a silent
do-over that erases what actually happened to the first one.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.logistics import (
    LogisticsCapability,
    create_shipment,
    issue_replacement_shipment,
    mark_shipment_in_transit,
    mark_shipment_lost,
    pack_order,
    track_order,
)
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph


def _packages() -> list:
    return pack_order([{"id": "p1", "name": "Apples", "qty": 3}])["packages"]


def _lost_shipment(kg: KnowledgeGraph, order_id: str) -> str:
    shipment_id = create_shipment(kg, order_id, _packages(), rider_id="rider_1", carrier="FastShip")["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)
    mark_shipment_lost(kg, shipment_id)
    return shipment_id


def test_mb3044_cannot_replace_a_shipment_that_was_never_lost():
    kg = KnowledgeGraph()
    shipment_id = create_shipment(kg, "ORD-1", _packages())["shipment_id"]

    result = issue_replacement_shipment(kg, shipment_id)

    assert result["success"] is False
    assert "not 'lost'" in result["error"]


def test_mb3044_replacement_is_a_real_new_shipment_linked_to_the_lost_one():
    kg = KnowledgeGraph()
    lost_id = _lost_shipment(kg, "ORD-1")

    result = issue_replacement_shipment(kg, lost_id)

    assert result["success"] is True
    assert result["status"] == "created"
    assert result["replaces_shipment_id"] == lost_id
    assert kg.get_entity(lost_id).attributes["replaced_by_shipment_id"] == result["shipment_id"]


def test_mb3044_replacement_carries_over_order_rider_and_carrier():
    kg = KnowledgeGraph()
    lost_id = _lost_shipment(kg, "ORD-1")

    result = issue_replacement_shipment(kg, lost_id)

    replacement = kg.get_entity(result["shipment_id"])
    assert replacement.attributes["order_id"] == "ORD-1"
    assert replacement.attributes["rider_id"] == "rider_1"
    assert replacement.attributes["carrier"] == "FastShip"


def test_mb3044_order_tracking_shows_both_the_lost_and_replacement_shipments():
    kg = KnowledgeGraph()
    lost_id = _lost_shipment(kg, "ORD-1")
    issue_replacement_shipment(kg, lost_id)

    tracked = track_order(kg, "ORD-1")

    assert tracked["shipment_count"] == 2


def test_mb3044_unknown_shipment_is_an_honest_failure():
    kg = KnowledgeGraph()

    result = issue_replacement_shipment(kg, "does-not-exist")

    assert result["success"] is False
    assert "not found" in result["error"]


def test_mb3044_issue_replacement_shipment_via_capability():
    kg = KnowledgeGraph()
    lost_id = _lost_shipment(kg, "ORD-1")
    cap = LogisticsCapability()

    assert cap.can_handle("issue_replacement_shipment")
    result = cap.invoke("issue_replacement_shipment", kg, lost_id)

    assert result["success"] is True
