"""MB-3032 Out Of Stock — inventory reaches zero scenario.

Inventory reaches zero.

Like MB-3010/3012/3013/3015/3028, no new code was needed: the full
"zero stock" lifecycle was already correctly handled by existing,
already-tested code, chained together end to end here — open_products()
(a zero-quantity item stays browsable, an "out of stock" listing, not a
hidden/removed one), get_product_detail() (accurately reports
inventory=0), try_reserve() (honestly refuses a reservation against
zero stock), and OrderCreationCapability's MB-3031 auto-backorder wiring
(a checkout attempt against an out-of-stock item backorders instead of
failing outright).
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import get_product_detail
from src.monkey_brain.kernel.domains.grocery import (
    OrderCreationCapability,
    confirm_reservation,
    open_products,
    try_reserve,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

PRODUCT_ID = "p1"


def _seed(quantity: int = 0) -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity("store_1", EntityType.ORGANIZATION, "Corner Store", {"delivery_fee": 0})
    kg.add_entity(
        PRODUCT_ID,
        EntityType.ASSET,
        "Milk",
        {
            "price": 3.0,
            "quantity": quantity,
            "store_id": "store_1",
            "product": True,
        },
    )
    return kg


def _checkout(kg: KnowledgeGraph, actor_id: str) -> dict:
    cap = OrderCreationCapability()
    return cap.handle(
        {
            "context": {
                "knowledge_graph": kg,
                "actor_id": actor_id,
                "selected_product": [
                    {
                        "id": PRODUCT_ID,
                        "name": "Milk",
                        "price": 3.0,
                        "qty": 1,
                        "store_id": "store_1",
                        "store_name": "Corner Store",
                    }
                ],
            }
        }
    )


def test_mb3032_out_of_stock_product_stays_browsable():
    kg = _seed(quantity=0)

    catalog = open_products(kg)

    assert any(p.entity_id == PRODUCT_ID for p in catalog)


def test_mb3032_product_detail_reports_zero_inventory():
    kg = _seed(quantity=0)

    detail = get_product_detail(kg, PRODUCT_ID)

    assert detail.inventory == 0


def test_mb3032_reservation_against_zero_stock_is_honestly_refused():
    kg = _seed(quantity=0)

    ok, msg = try_reserve(kg, PRODUCT_ID, "alice", qty=1)

    assert ok is False
    assert "insufficient stock" in msg


def test_mb3032_checkout_against_zero_stock_backorders_instead_of_failing():
    kg = _seed(quantity=0)

    result = _checkout(kg, "alice")

    assert result["success"] is True
    assert len(result["backordered"]) == 1
    assert result["backordered"][0]["product_id"] == PRODUCT_ID


def test_mb3032_inventory_reaching_zero_via_a_real_purchase_then_backorders_the_next_buyer():
    kg = _seed(quantity=1)

    ok, _ = try_reserve(kg, PRODUCT_ID, "alice", qty=1)
    assert ok is True
    confirm_reservation(kg, PRODUCT_ID, "alice")
    assert kg.get_entity(PRODUCT_ID).attributes["quantity"] == 0

    result = _checkout(kg, "bob")

    assert result["success"] is True
    assert len(result["backordered"]) == 1
