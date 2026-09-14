"""MB-3033 Price Change — merchant changes price scenario.

Merchant changes price.

Like MB-3010/3012/3013/3015/3028/3032, no new code was needed:
update_product() (MB-3039) already handles price as one of its generic
fields, with the same ownership check every other listing write uses.
This file verifies the specific, high-stakes guarantee a price change
must uphold: it updates the LIVE catalog immediately, but never
retroactively rewrites a historical order's captured price (grocery.py's
OrderCreationCapability captures price "AS BOUGHT" into order line
items, a real, persisted fact — GS-1900) or an already-added cart line
(commerce.py's Cart/CartLine snapshot price at add_to_cart() time). A
customer who added an item to their cart, or already bought it, at the
old price is never silently charged the new one.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    add_to_cart,
    get_cart,
    get_product_detail,
    list_product,
    onboard_merchant,
    update_product,
)
from src.monkey_brain.kernel.domains.grocery import OrderCreationCapability
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph

MERCHANT_ID = "merchant_bob"
OLD_PRICE = 4.5
NEW_PRICE = 6.0


def _seed_product() -> tuple[KnowledgeGraph, str, str]:
    kg = KnowledgeGraph()
    store_id = onboard_merchant(kg, MERCHANT_ID, "Bob's Store")["store_id"]
    product_id = list_product(kg, store_id, MERCHANT_ID, "Oat Milk", price=OLD_PRICE, quantity=10)["product_id"]
    return kg, store_id, product_id


def test_mb3033_price_change_updates_the_live_catalog():
    kg, _store_id, product_id = _seed_product()

    result = update_product(kg, product_id, MERCHANT_ID, price=NEW_PRICE)

    assert result["success"] is True
    assert get_product_detail(kg, product_id).price == NEW_PRICE


def test_mb3033_price_change_does_not_rewrite_a_historical_order():
    kg, store_id, product_id = _seed_product()
    cap = OrderCreationCapability()
    order_result = cap.handle(
        {
            "context": {
                "knowledge_graph": kg,
                "actor_id": "alice",
                "selected_product": [
                    {
                        "id": product_id,
                        "name": "Oat Milk",
                        "price": OLD_PRICE,
                        "qty": 1,
                        "store_id": store_id,
                        "store_name": "Bob's Store",
                    }
                ],
            }
        }
    )
    order_id = order_result["order_id"]

    update_product(kg, product_id, MERCHANT_ID, price=NEW_PRICE)

    order = kg.get_entity(order_id)
    assert order.attributes["items"][0]["price"] == OLD_PRICE


def test_mb3033_price_change_does_not_rewrite_an_existing_cart_line():
    kg, _store_id, product_id = _seed_product()
    add_to_cart(kg, "alice", product_id, quantity=2)

    update_product(kg, product_id, MERCHANT_ID, price=NEW_PRICE)

    cart = get_cart(kg, "alice")
    assert cart.lines[0].price == OLD_PRICE


def test_mb3033_a_new_cart_after_the_change_uses_the_new_price():
    kg, _store_id, product_id = _seed_product()

    update_product(kg, product_id, MERCHANT_ID, price=NEW_PRICE)
    add_to_cart(kg, "bob", product_id, quantity=1)

    cart = get_cart(kg, "bob")
    assert cart.lines[0].price == NEW_PRICE
