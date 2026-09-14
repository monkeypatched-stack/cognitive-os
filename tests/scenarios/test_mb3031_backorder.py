"""MB-3031 Backorder — inventory unavailable scenario.

Inventory unavailable.

place_backorder()/fulfill_backorders() (MB-3016) already existed, but
nothing in the real checkout path ever called them: OrderCreationCapability
failed the ENTIRE order the moment any single item couldn't reserve
(insufficient stock), with no automatic backorder. Per explicit design
choice ("wire into checkout"): OrderCreationCapability now places a real
backorder for whatever can't reserve, instead of failing the whole
order — the order completes for what's actually available, with the
rest queued.

Backorders are keyed to buyer_id (the real actor), not order_id — a
deliberate choice distinct from try_reserve's own order_id-as-holder
convention within this function (that's scoped to one order's temporary
hold). A backorder is a claim the actor holds regardless of which order
it came from, and MB-3030's create_partial_shipments() already looks
backorders up by buyer_id — this file verifies that full pipeline
connects end to end: checkout backorders an unavailable item, a
downstream create_partial_shipments() call correctly treats it as
pending, and fulfill_backorders() delivers it once restocked.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.grocery import (
    OrderCreationCapability,
    fulfill_backorders,
)
from src.monkey_brain.kernel.domains.logistics import create_partial_shipments
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

BUYER_ID = "alice"


def _seed(p1_qty: int = 10, p2_qty: int = 0) -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(
        "p1",
        EntityType.ASSET,
        "Apples",
        {"price": 2.0, "quantity": p1_qty, "store_id": "store_1"},
    )
    kg.add_entity(
        "p2",
        EntityType.ASSET,
        "Milk",
        {"price": 3.0, "quantity": p2_qty, "store_id": "store_1"},
    )
    kg.add_entity("store_1", EntityType.ORGANIZATION, "Corner Store", {"delivery_fee": 0})
    return kg


def _place_order(kg: KnowledgeGraph, products: list[dict]) -> dict:
    cap = OrderCreationCapability()
    return cap.handle(
        {
            "context": {
                "knowledge_graph": kg,
                "actor_id": BUYER_ID,
                "selected_product": products,
            }
        }
    )


def test_mb3031_unavailable_item_backorders_instead_of_failing_the_order():
    kg = _seed(p2_qty=0)
    products = [
        {
            "id": "p1",
            "name": "Apples",
            "price": 2.0,
            "qty": 3,
            "store_id": "store_1",
            "store_name": "Corner Store",
        },
        {
            "id": "p2",
            "name": "Milk",
            "price": 3.0,
            "qty": 1,
            "store_id": "store_1",
            "store_name": "Corner Store",
        },
    ]

    result = _place_order(kg, products)

    assert result["success"] is True
    assert len(result["backordered"]) == 1
    assert result["backordered"][0]["product_id"] == "p2"

    # p1 was genuinely reserved.
    reservations = kg.get_entity("p1").attributes.get("reservations", [])
    assert sum(r["qty"] for r in reservations) == 3


def test_mb3031_backorder_is_keyed_to_the_buyer_not_the_order():
    kg = _seed(p2_qty=0)
    products = [
        {
            "id": "p2",
            "name": "Milk",
            "price": 3.0,
            "qty": 1,
            "store_id": "store_1",
            "store_name": "Corner Store",
        }
    ]

    _place_order(kg, products)

    backorders = [e for e in kg.entities if e.attributes.get("backorder") and e.attributes.get("product_id") == "p2"]
    assert len(backorders) == 1
    assert backorders[0].attributes["actor_id"] == BUYER_ID


def test_mb3031_all_available_order_has_nothing_backordered():
    kg = _seed(p2_qty=5)
    products = [
        {
            "id": "p1",
            "name": "Apples",
            "price": 2.0,
            "qty": 3,
            "store_id": "store_1",
            "store_name": "Corner Store",
        },
        {
            "id": "p2",
            "name": "Milk",
            "price": 3.0,
            "qty": 1,
            "store_id": "store_1",
            "store_name": "Corner Store",
        },
    ]

    result = _place_order(kg, products)

    assert result["success"] is True
    assert result["backordered"] == []


def test_mb3031_connects_end_to_end_with_partial_shipments_and_fulfillment():
    kg = _seed(p2_qty=0)
    products = [
        {
            "id": "p1",
            "name": "Apples",
            "price": 2.0,
            "qty": 3,
            "store_id": "store_1",
            "store_name": "Corner Store",
        },
        {
            "id": "p2",
            "name": "Milk",
            "price": 3.0,
            "qty": 1,
            "store_id": "store_1",
            "store_name": "Corner Store",
        },
    ]
    result = _place_order(kg, products)
    order_id = result["order_id"]

    shipments = create_partial_shipments(
        kg,
        order_id,
        BUYER_ID,
        [
            {"id": "p1", "name": "Apples", "qty": 3},
            {"id": "p2", "name": "Milk", "qty": 1},
        ],
    )
    assert shipments["partial"] is True
    assert shipments["shipped_item_ids"] == ["p1"]
    assert shipments["pending_item_ids"] == ["p2"]

    kg.update_entity("p2", attributes={"quantity": 5})
    fulfilled = fulfill_backorders(kg, "p2")

    assert len(fulfilled) == 1
    assert fulfilled[0]["actor_id"] == BUYER_ID
