"""MB-3023 Customer Review — leave review scenario.

Leave review.

get_product_detail() (MB-3005) only ever displayed
attributes["reviews"] — nothing could write to it. Built
kernel/domains/commerce.py::leave_review() for the write side. Per
explicit design choice ("verified purchase required"): a review
requires a real, completed order (the same order history
customers_also_bought() (MB-3006) already mines, gated on the same
"completed" terminal status confirm_receipt() (MB-3022) sets) placed by
the reviewer that actually contains the product — an actor who never
bought (or never confirmed receiving) the product can't review it.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    get_product_detail,
    leave_review,
)
from src.monkey_brain.kernel.domains.logistics import (
    confirm_receipt,
    create_shipment,
    mark_shipment_delivered,
    mark_shipment_in_transit,
    pack_order,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

PRODUCT_ID = "prod_1"


def _seed_product() -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(PRODUCT_ID, EntityType.ASSET, "Oat Milk", {"price": 4.5, "quantity": 10})
    return kg


def _complete_order(kg: KnowledgeGraph, order_id: str, buyer_id: str) -> None:
    kg.add_entity(
        order_id,
        EntityType.EVENT,
        "Grocery Order",
        {
            "buyer_id": buyer_id,
            "items": [{"product_id": PRODUCT_ID}],
            "status": "confirmed",
        },
    )
    packages = pack_order([{"id": PRODUCT_ID, "name": "Oat Milk", "qty": 1}])["packages"]
    shipment_id = create_shipment(kg, order_id, packages)["shipment_id"]
    mark_shipment_in_transit(kg, shipment_id)
    mark_shipment_delivered(kg, shipment_id)
    confirm_receipt(kg, order_id, actor_id=buyer_id)


def test_mb3023_review_without_a_purchase_is_refused():
    kg = _seed_product()

    result = leave_review(kg, PRODUCT_ID, "alice", 5, "Great!")

    assert result["success"] is False
    assert "verified purchase" in result["error"]


def test_mb3023_rating_out_of_range_is_refused():
    kg = _seed_product()

    result = leave_review(kg, PRODUCT_ID, "alice", 7, "too high")

    assert result["success"] is False
    assert "rating must be between" in result["error"]


def test_mb3023_unknown_product_is_an_honest_failure():
    kg = _seed_product()

    result = leave_review(kg, "does-not-exist", "alice", 5, "x")

    assert result["success"] is False
    assert "no such product" in result["error"]


def test_mb3023_verified_purchase_can_leave_a_review():
    kg = _seed_product()
    _complete_order(kg, "ORD-1", "alice")

    result = leave_review(kg, PRODUCT_ID, "alice", 5, "Great oat milk!")

    assert result["success"] is True
    assert result["review_count"] == 1

    detail = get_product_detail(kg, PRODUCT_ID)
    assert len(detail.reviews) == 1
    assert detail.reviews[0].reviewer == "alice"
    assert detail.average_rating == 5.0


def test_mb3023_someone_who_never_bought_it_still_cannot_review():
    kg = _seed_product()
    _complete_order(kg, "ORD-1", "alice")
    leave_review(kg, PRODUCT_ID, "alice", 5, "Great!")

    result = leave_review(kg, PRODUCT_ID, "bob", 4, "Looks nice")

    assert result["success"] is False


def test_mb3023_multiple_reviews_average_correctly():
    kg = _seed_product()
    _complete_order(kg, "ORD-1", "alice")
    _complete_order(kg, "ORD-2", "carol")

    leave_review(kg, PRODUCT_ID, "alice", 5, "Great!")
    leave_review(kg, PRODUCT_ID, "carol", 3, "Its ok")

    detail = get_product_detail(kg, PRODUCT_ID)
    assert len(detail.reviews) == 2
    assert detail.average_rating == 4.0


def test_mb3023_leave_review_via_capability():
    kg = _seed_product()
    _complete_order(kg, "ORD-1", "alice")
    cap = CommerceCapability()

    assert cap.can_handle("leave_review")
    assert cap.invoke("leave_review", kg, PRODUCT_ID, "alice", 5, "Great!")["success"] is True
