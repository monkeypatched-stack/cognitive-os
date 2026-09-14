"""MB-3034 Promotion — limited-time sale scenario.

Limited-time sale.

No time-bounded, automatic (no-code) discount existed — MB-3009's
coupons are opt-in and code-gated, the opposite of a promotion. Per
explicit design choice ("full: display AND checkout"): built
kernel/domains/commerce.py::create_promotion() (a merchant's own
time-limited sale on their own product, ownership-checked like every
other merchant write) and get_effective_price() — the single source of
truth for a product's actual current price, wired into BOTH
get_product_detail() (what's shown) and add_to_cart() (what a new cart
line actually charges), so a sale genuinely saves money at checkout,
not just a displayed badge.
"""

from __future__ import annotations

import time

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    add_to_cart,
    create_promotion,
    get_cart,
    get_effective_price,
    get_product_detail,
    list_product,
    onboard_merchant,
)
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph

MERCHANT_ID = "merchant_bob"
REGULAR_PRICE = 10.0
SALE_PRICE = 7.0


def _seed_product(price: float = REGULAR_PRICE) -> tuple[KnowledgeGraph, str]:
    kg = KnowledgeGraph()
    store_id = onboard_merchant(kg, MERCHANT_ID, "Bob's Store")["store_id"]
    product_id = list_product(kg, store_id, MERCHANT_ID, "Oat Milk", price=price, quantity=10)["product_id"]
    return kg, product_id


def test_mb3034_no_promotion_means_regular_price():
    kg, product_id = _seed_product()

    assert get_effective_price(kg, product_id) == REGULAR_PRICE
    detail = get_product_detail(kg, product_id)
    assert detail.price == REGULAR_PRICE
    assert detail.on_sale is False
    assert detail.regular_price is None


def test_mb3034_active_promotion_lowers_the_effective_and_displayed_price():
    kg, product_id = _seed_product()

    result = create_promotion(kg, product_id, MERCHANT_ID, sale_price=SALE_PRICE)

    assert result["success"] is True
    assert get_effective_price(kg, product_id) == SALE_PRICE
    detail = get_product_detail(kg, product_id)
    assert detail.price == SALE_PRICE
    assert detail.on_sale is True
    assert detail.regular_price == REGULAR_PRICE


def test_mb3034_promotion_applies_automatically_at_checkout_no_code_needed():
    kg, product_id = _seed_product()
    create_promotion(kg, product_id, MERCHANT_ID, sale_price=SALE_PRICE)

    add_to_cart(kg, "alice", product_id, quantity=2)

    cart = get_cart(kg, "alice")
    assert cart.lines[0].price == SALE_PRICE
    assert cart.total == SALE_PRICE * 2


def test_mb3034_future_dated_promotion_is_not_yet_active():
    kg, product_id = _seed_product()
    future_start = time.time() + 3600

    create_promotion(kg, product_id, MERCHANT_ID, sale_price=SALE_PRICE, starts_at=future_start)

    assert get_effective_price(kg, product_id) == REGULAR_PRICE


def test_mb3034_expired_promotion_no_longer_applies():
    kg, product_id = _seed_product()
    now = time.time()

    create_promotion(
        kg,
        product_id,
        MERCHANT_ID,
        sale_price=SALE_PRICE,
        starts_at=now - 7200,
        ends_at=now - 3600,
    )

    assert get_effective_price(kg, product_id) == REGULAR_PRICE


def test_mb3034_cannot_run_a_promotion_on_another_merchants_product():
    kg, product_id = _seed_product()

    result = create_promotion(kg, product_id, "merchant_eve", sale_price=1.0)

    assert result["success"] is False
    assert "does not own" in result["error"]


def test_mb3034_sale_price_must_be_a_genuine_discount():
    kg, product_id = _seed_product()

    not_cheaper = create_promotion(kg, product_id, MERCHANT_ID, sale_price=REGULAR_PRICE)
    negative = create_promotion(kg, product_id, MERCHANT_ID, sale_price=-1.0)

    assert not_cheaper["success"] is False
    assert negative["success"] is False


def test_mb3034_ends_at_must_be_after_starts_at():
    kg, product_id = _seed_product()

    result = create_promotion(kg, product_id, MERCHANT_ID, sale_price=1.0, starts_at=100, ends_at=50)

    assert result["success"] is False


def test_mb3034_create_promotion_via_capability():
    kg, product_id = _seed_product()
    cap = CommerceCapability()

    assert cap.can_handle("create_promotion")
    assert cap.can_handle("get_effective_price")
    result = cap.invoke("create_promotion", kg, product_id, MERCHANT_ID, SALE_PRICE)

    assert result["success"] is True
