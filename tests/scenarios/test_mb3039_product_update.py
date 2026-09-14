"""MB-3039 Product Update — merchant edits listing scenario.

Merchant edits listing.

Built kernel/domains/commerce.py::update_product() — a merchant edits
any field of their own listing (name, price, description, images,
category, ...). Refuses unless the merchant owns the store the product
actually belongs to (require_store_owner(), the same transitive
ownership rule list_product() (MB-3038) establishes at creation time) —
a merchant can never edit another merchant's listing. A partial update
(e.g. price alone) never touches fields the caller didn't mention.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    get_product_detail,
    list_product,
    onboard_merchant,
    update_product,
)
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph

MERCHANT_ID = "merchant_bob"


def _seed_product() -> tuple[KnowledgeGraph, str]:
    kg = KnowledgeGraph()
    store_id = onboard_merchant(kg, MERCHANT_ID, "Bob's Store")["store_id"]
    product_id = list_product(kg, store_id, MERCHANT_ID, "Oat Milk", price=4.5, quantity=10)["product_id"]
    return kg, product_id


def test_mb3039_owner_can_update_price():
    kg, product_id = _seed_product()

    result = update_product(kg, product_id, MERCHANT_ID, price=5.0)

    assert result["success"] is True
    assert kg.get_entity(product_id).attributes["price"] == 5.0
    assert get_product_detail(kg, product_id).price == 5.0


def test_mb3039_partial_update_leaves_other_fields_untouched():
    kg, product_id = _seed_product()

    update_product(kg, product_id, MERCHANT_ID, price=5.0)

    product = kg.get_entity(product_id)
    assert product.attributes["quantity"] == 10  # unchanged
    assert product.name == "Oat Milk"  # unchanged


def test_mb3039_can_update_the_product_name():
    kg, product_id = _seed_product()

    result = update_product(kg, product_id, MERCHANT_ID, name="Organic Oat Milk")

    assert result["success"] is True
    assert kg.get_entity(product_id).name == "Organic Oat Milk"


def test_mb3039_cannot_edit_another_merchants_listing():
    kg, product_id = _seed_product()

    result = update_product(kg, product_id, "merchant_eve", price=0.01)

    assert result["success"] is False
    assert "does not own" in result["error"]
    assert kg.get_entity(product_id).attributes["price"] == 4.5


def test_mb3039_cannot_update_to_a_negative_price():
    kg, product_id = _seed_product()

    result = update_product(kg, product_id, MERCHANT_ID, price=-1.0)

    assert result["success"] is False
    assert kg.get_entity(product_id).attributes["price"] == 4.5


def test_mb3039_unknown_product_is_an_honest_failure():
    kg = KnowledgeGraph()

    result = update_product(kg, "does-not-exist", MERCHANT_ID, price=1.0)

    assert result["success"] is False
    assert "no such product" in result["error"]


def test_mb3039_update_product_via_capability():
    kg, product_id = _seed_product()
    cap = CommerceCapability()

    assert cap.can_handle("update_product")
    result = cap.invoke("update_product", kg, product_id, MERCHANT_ID, price=6.0)

    assert result["success"] is True
