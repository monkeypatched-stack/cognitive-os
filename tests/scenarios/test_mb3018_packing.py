"""MB-3018 Packing — pack order scenario.

Pack order.

No packing/boxing concept existed anywhere in kernel/domains/*.py (only
unrelated hits about multi-pack product sizes). This is a low-ambiguity,
direct extension of the same warehouse-fulfillment pipeline just built
for MB-3017 (Pick -> Pack -> Deliver): built
kernel/domains/logistics.py::pack_order(), reusing the cold-chain/
capacity model already established by select_delivery_riders() on the
delivery side. Cold-chain items are NEVER packed alongside standard
items in the same box (same hard-requirement tier as the cold-chain
rider requirement on the delivery side); items are first-fit packed
into as few boxes as it takes, capped at box_capacity units each.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.logistics import LogisticsCapability, pack_order


def test_mb3018_order_that_fits_in_one_box():
    products = [
        {"id": "p1", "name": "Apples", "qty": 3},
        {"id": "p2", "name": "Bread", "qty": 2},
    ]

    result = pack_order(products)

    assert result["success"] is True
    assert result["package_count"] == 1
    assert result["packages"][0]["box_type"] == "standard"
    assert result["packages"][0]["qty"] == 5


def test_mb3018_cold_chain_items_never_mixed_into_a_standard_box():
    products = [
        {"id": "p1", "name": "Milk", "qty": 2, "cold_chain": True},
        {"id": "p2", "name": "Bread", "qty": 2},
    ]

    result = pack_order(products)

    assert result["package_count"] == 2
    assert result["cold_chain_packages"] == 1
    box_types = sorted(p["box_type"] for p in result["packages"])
    assert box_types == ["insulated", "standard"]
    insulated = next(p for p in result["packages"] if p["box_type"] == "insulated")
    assert all(item["product_id"] == "p1" for item in insulated["items"])


def test_mb3018_order_exceeding_box_capacity_splits_across_multiple_boxes():
    products = [{"id": "p1", "name": "Cans", "qty": 30}]

    result = pack_order(products, box_capacity=12)

    assert result["package_count"] == 3
    assert [p["qty"] for p in result["packages"]] == [12, 12, 6]
    assert sum(p["qty"] for p in result["packages"]) == 30


def test_mb3018_multiple_cold_chain_items_split_across_capacity_boundary():
    products = [
        {"id": "c1", "name": "Milk", "qty": 8, "cold_chain": True},
        {"id": "c2", "name": "Yogurt", "qty": 8, "cold_chain": True},
        {"id": "s1", "name": "Chips", "qty": 4},
    ]

    result = pack_order(products, box_capacity=12)

    # 16 cold-chain units at 12/box -> 2 insulated boxes; standard items
    # never share a box with the cold-chain overflow.
    assert result["cold_chain_packages"] == 2
    standard_boxes = [p for p in result["packages"] if p["box_type"] == "standard"]
    assert len(standard_boxes) == 1
    assert standard_boxes[0]["qty"] == 4


def test_mb3018_empty_order_produces_zero_packages_no_error():
    result = pack_order([])

    assert result["success"] is True
    assert result["package_count"] == 0
    assert result["packages"] == []


def test_mb3018_pack_order_via_capability():
    products = [{"id": "p1", "name": "Apples", "qty": 3}]
    cap = LogisticsCapability()

    assert cap.can_handle("pack_order")
    assert cap.invoke("pack_order", products) == pack_order(products)
