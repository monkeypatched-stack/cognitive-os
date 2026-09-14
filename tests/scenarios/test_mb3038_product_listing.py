"""MB-3038 Product Listing — merchant lists product scenario.

Merchant lists product.

Built kernel/domains/commerce.py::list_product() — a merchant lists a
new product into their own store (MB-3037's onboard_merchant()).
Refuses outright unless the merchant actually owns the store
(require_store_owner()) — a merchant can never list into a store they
don't own. Sets attributes["product"] = True unconditionally, the exact
marker open_products() requires for an ASSET to be a real, purchasable
catalog item — a listed product is immediately browsable, not a second,
disconnected catalog.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    list_product,
    onboard_merchant,
)
from src.monkey_brain.kernel.domains.grocery import open_products
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

MERCHANT_ID = "merchant_bob"


def _seed_store() -> tuple[KnowledgeGraph, str]:
    kg = KnowledgeGraph()
    store_id = onboard_merchant(kg, MERCHANT_ID, "Bob's Store")["store_id"]
    return kg, store_id


def test_mb3038_merchant_lists_a_product_in_their_own_store():
    kg, store_id = _seed_store()

    result = list_product(kg, store_id, MERCHANT_ID, "Oat Milk", price=4.5, quantity=10, cold_chain=True)

    assert result["success"] is True
    product = kg.get_entity(result["product_id"])
    assert product.entity_type == EntityType.ASSET
    assert product.attributes["product"] is True
    assert product.attributes["store_id"] == store_id
    assert product.attributes["price"] == 4.5
    assert product.attributes["quantity"] == 10
    assert product.attributes["cold_chain"] is True


def test_mb3038_listed_product_is_immediately_browsable():
    kg, store_id = _seed_store()
    result = list_product(kg, store_id, MERCHANT_ID, "Oat Milk", price=4.5)

    catalog = open_products(kg)

    assert any(p.entity_id == result["product_id"] for p in catalog)


def test_mb3038_cannot_list_into_a_store_you_do_not_own():
    kg, store_id = _seed_store()

    result = list_product(kg, store_id, "merchant_eve", "Fake Product", price=1.0)

    assert result["success"] is False
    assert "does not own" in result["error"]


def test_mb3038_cannot_list_a_product_with_a_negative_price():
    kg, store_id = _seed_store()

    result = list_product(kg, store_id, MERCHANT_ID, "Broken Price", price=-1.0)

    assert result["success"] is False


def test_mb3038_cannot_list_into_a_nonexistent_store():
    kg = KnowledgeGraph()

    result = list_product(kg, "does-not-exist", MERCHANT_ID, "Ghost Product", price=1.0)

    assert result["success"] is False
    assert "no such store" in result["error"]


def test_mb3038_list_product_via_capability():
    kg, store_id = _seed_store()
    cap = CommerceCapability()

    assert cap.can_handle("list_product")
    result = cap.invoke("list_product", kg, store_id, MERCHANT_ID, "Bread", 2.0)

    assert result["success"] is True
