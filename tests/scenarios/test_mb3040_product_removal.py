"""MB-3040 Product Removal — listing removed scenario.

Listing removed.

Built kernel/domains/commerce.py::remove_product() — a SOFT delete
(attributes["product"] set to False), the same pattern open_products()
already uses for a closed store (attributes["is_open"] = False):
excluded from the catalog going forward (open_products() requires
attributes["product"] to be truthy), without erasing the entity itself
— existing orders/reviews that reference this product_id keep
resolving against real data instead of a dangling reference. Refuses
unless the merchant owns the product's store, same rule as
update_product() (MB-3039).
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    list_product,
    onboard_merchant,
    remove_product,
)
from src.monkey_brain.kernel.domains.grocery import open_products
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph

MERCHANT_ID = "merchant_bob"


def _seed_product() -> tuple[KnowledgeGraph, str]:
    kg = KnowledgeGraph()
    store_id = onboard_merchant(kg, MERCHANT_ID, "Bob's Store")["store_id"]
    product_id = list_product(kg, store_id, MERCHANT_ID, "Oat Milk", price=4.5, quantity=10)["product_id"]
    return kg, product_id


def test_mb3040_owner_can_remove_their_listing():
    kg, product_id = _seed_product()

    result = remove_product(kg, product_id, MERCHANT_ID)

    assert result["success"] is True
    assert kg.get_entity(product_id).attributes["product"] is False


def test_mb3040_removed_listing_disappears_from_the_catalog_but_the_entity_survives():
    kg, product_id = _seed_product()

    remove_product(kg, product_id, MERCHANT_ID)

    catalog = open_products(kg)
    assert not any(p.entity_id == product_id for p in catalog)

    still_there = kg.get_entity(product_id)
    assert still_there is not None
    assert still_there.attributes["removed_by"] == MERCHANT_ID
    assert "removed_at" in still_there.attributes


def test_mb3040_cannot_remove_the_same_listing_twice():
    kg, product_id = _seed_product()
    remove_product(kg, product_id, MERCHANT_ID)

    result = remove_product(kg, product_id, MERCHANT_ID)

    assert result["success"] is False
    assert "already removed" in result["error"]


def test_mb3040_cannot_remove_another_merchants_listing():
    kg, product_id = _seed_product()

    result = remove_product(kg, product_id, "merchant_eve")

    assert result["success"] is False
    assert "does not own" in result["error"]
    assert kg.get_entity(product_id).attributes["product"] is True


def test_mb3040_unknown_product_is_an_honest_failure():
    kg = KnowledgeGraph()

    result = remove_product(kg, "does-not-exist", MERCHANT_ID)

    assert result["success"] is False
    assert "no such product" in result["error"]


def test_mb3040_remove_product_via_capability():
    kg, product_id = _seed_product()
    cap = CommerceCapability()

    assert cap.can_handle("remove_product")
    result = cap.invoke("remove_product", kg, product_id, MERCHANT_ID)

    assert result["success"] is True
