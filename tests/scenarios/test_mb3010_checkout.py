"""MB-3010 Checkout — end-to-end checkout scenario.

Verify:
    - address
    - taxes
    - shipping
    - totals

Unlike MB-3005/3006/3007/3009, none of this needed new code: checkout is
already a real, thoroughly implemented pipeline in kernel/domains/
grocery.py — DeliveryCapability (pickup/delivery address resolution,
rider assignment) and OrderCreationCapability (subtotal/tax/delivery-fee/
total, real inventory reservation via try_reserve). This scenario
exercises those two existing capabilities directly with a real store,
product, delivery address, and rider, rather than re-implementing
anything. No dedicated test file for either capability existed before
this one.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.grocery import (
    DeliveryCapability,
    OrderCreationCapability,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

STORE_ID = "store_1"
DELIVERY_FEE = 4.99
TAX_RATE = 0.08


def _seed_checkout_world() -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(
        STORE_ID,
        EntityType.ORGANIZATION,
        "Key Food",
        {
            "address": "200 W 23rd St, New York, NY",
            "delivery_fee": DELIVERY_FEE,
        },
    )
    kg.add_entity(
        "prod_milk",
        EntityType.ASSET,
        "Milk",
        {
            "price": 3.99,
            "quantity": 10,
            "store_id": STORE_ID,
        },
    )
    kg.add_entity(
        "prod_bread",
        EntityType.ASSET,
        "Bread",
        {
            "price": 2.99,
            "quantity": 10,
            "store_id": STORE_ID,
        },
    )
    kg.add_entity(
        "addr_home",
        EntityType.ADDRESS,
        "Home",
        {
            "street": "123 Main St",
            "city": "New York",
            "state": "NY",
            "zip_code": "10001",
            "is_primary": True,
            # DeliveryCapability scopes address resolution to the requesting
            # actor (grocery.py, "Scoped to the recipient" comment) -- without
            # a real actor_id here it silently matches zero addresses and
            # returns "no delivery address on file for actor" instead of
            # ever resolving this one.
            "actor_id": "alice",
        },
    )
    kg.add_entity(
        "rider_1",
        EntityType.PERSON,
        "Ravi",
        {
            "status": "available",
            "rating": 4.8,
            "vehicle": "bike",
            "estimated_minutes": 25,
        },
    )
    return kg


def _cart(*items: tuple[str, str, float, int]) -> list[dict]:
    return [
        {
            "id": pid,
            "name": name,
            "price": price,
            "qty": qty,
            "store_id": STORE_ID,
            "store_name": "Key Food",
        }
        for pid, name, price, qty in items
    ]


# ── address ───────────────────────────────────────────────────────────


def test_mb3010_checkout_resolves_pickup_and_delivery_addresses():
    kg = _seed_checkout_world()
    cart = _cart(("prod_milk", "Milk", 3.99, 2))

    result = DeliveryCapability().handle(
        {
            "context": {
                "knowledge_graph": kg,
                "selected_product": cart,
                "question": "deliver my order",
                "actor_id": "alice",
            }
        }
    )

    assert result["success"] is True
    assert result["pickup_addresses"] == ["Key Food, 200 W 23rd St, New York, NY"]
    assert result["delivery_address"] == "123 Main St, New York, NY, 10001"


def test_mb3010_checkout_fails_honestly_when_store_has_no_address():
    kg = _seed_checkout_world()
    kg.add_entity(
        "store_no_addr",
        EntityType.ORGANIZATION,
        "No Address Store",
        {"delivery_fee": 1.0},
    )
    kg.add_entity(
        "prod_eggs",
        EntityType.ASSET,
        "Eggs",
        {
            "price": 4.49,
            "quantity": 5,
            "store_id": "store_no_addr",
        },
    )
    cart = [
        {
            "id": "prod_eggs",
            "name": "Eggs",
            "price": 4.49,
            "qty": 1,
            "store_id": "store_no_addr",
            "store_name": "No Address Store",
        }
    ]

    result = DeliveryCapability().handle(
        {
            "context": {
                "knowledge_graph": kg,
                "selected_product": cart,
                "question": "deliver my order",
                "actor_id": "carol",
            }
        }
    )

    assert result["success"] is False
    assert "no address on file" in result["error"]


# ── taxes ─────────────────────────────────────────────────────────────


def test_mb3010_checkout_computes_tax_on_subtotal():
    kg = _seed_checkout_world()
    cart = _cart(("prod_milk", "Milk", 3.99, 2))

    result = OrderCreationCapability().handle(
        {
            "context": {
                "knowledge_graph": kg,
                "selected_product": cart,
                "actor_id": "alice",
                "question": "deliver my order",
            }
        }
    )

    assert result["success"] is True
    subtotal = round(3.99 * 2, 2)
    assert result["subtotal"] == subtotal
    assert result["tax_rate"] == TAX_RATE
    assert result["tax"] == round(subtotal * TAX_RATE, 2)


# ── shipping ──────────────────────────────────────────────────────────


def test_mb3010_checkout_charges_shipping_for_delivery():
    kg = _seed_checkout_world()
    cart = _cart(("prod_milk", "Milk", 3.99, 2))

    result = OrderCreationCapability().handle(
        {
            "context": {
                "knowledge_graph": kg,
                "selected_product": cart,
                "actor_id": "alice",
                "question": "deliver my order",
            }
        }
    )

    assert result["delivery_fee"] == DELIVERY_FEE


def test_mb3010_checkout_waives_shipping_for_pickup():
    kg = _seed_checkout_world()
    cart = _cart(("prod_milk", "Milk", 3.99, 2))

    result = OrderCreationCapability().handle(
        {
            "context": {
                "knowledge_graph": kg,
                "selected_product": cart,
                "actor_id": "bob",
                "question": "I will pick up my order",
            }
        }
    )

    assert result["success"] is True
    assert result["delivery_fee"] == 0.0


# ── totals ────────────────────────────────────────────────────────────


def test_mb3010_checkout_total_equals_subtotal_plus_tax_plus_shipping():
    kg = _seed_checkout_world()
    cart = _cart(("prod_milk", "Milk", 3.99, 2))

    result = OrderCreationCapability().handle(
        {
            "context": {
                "knowledge_graph": kg,
                "selected_product": cart,
                "actor_id": "alice",
                "question": "deliver my order",
            }
        }
    )

    assert result["total"] == round(result["subtotal"] + result["tax"] + result["delivery_fee"], 2)
    assert result["total"] == 13.61  # 7.98 + 0.64 + 4.99


def test_mb3010_checkout_totals_sum_across_multiple_line_items():
    kg = _seed_checkout_world()
    cart = _cart(("prod_milk", "Milk", 3.99, 2), ("prod_bread", "Bread", 2.99, 3))

    result = OrderCreationCapability().handle(
        {
            "context": {
                "knowledge_graph": kg,
                "selected_product": cart,
                "actor_id": "alice",
                "question": "deliver my order",
            }
        }
    )

    assert result["success"] is True
    assert len(result["items"]) == 2
    expected_subtotal = round(3.99 * 2 + 2.99 * 3, 2)
    assert result["subtotal"] == expected_subtotal
    expected_total = round(expected_subtotal + round(expected_subtotal * TAX_RATE, 2) + DELIVERY_FEE, 2)
    assert result["total"] == expected_total
