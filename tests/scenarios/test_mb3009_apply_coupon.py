"""MB-3009 Apply Coupon — coupon acceptance/rejection scenario.

Verify:
    - Coupon accepted.
    - Coupon rejected.

Unlike MB-3005/3006/3007, coupon VALIDATION already existed and was
thoroughly implemented: kernel/domains/grocery.py::validate_coupon()
(forged/nonexistent -> fraud_suspected, wrong-store -> fraud_suspected,
expired -> honest rejection, otherwise valid with discount_amount/
discount_percent). What didn't exist was applying that validation to a
customer's actual cart (MB-3007) and getting back a real new total.
kernel/domains/commerce.py::apply_coupon_to_cart() delegates the
accept/reject decision entirely to validate_coupon() and only adds what
that decision means for one cart's subtotal.
"""

from __future__ import annotations

import time

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    CommerceCapabilityBus,
    add_to_cart,
    apply_coupon_to_cart,
    get_cart,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

STORE_ID = "store_1"
MILK = "prod_milk"


def _seed_catalog_and_coupons() -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(MILK, EntityType.ASSET, "Milk", {"price": 10.00, "store_id": STORE_ID})

    kg.add_entity(
        "coupon_save3",
        EntityType.OTHER,
        "SAVE3 coupon",
        {
            "coupon": True,
            "code": "SAVE3",
            "store_id": STORE_ID,
            "discount_amount": 3.0,
        },
    )
    kg.add_entity(
        "coupon_10pct",
        EntityType.OTHER,
        "10PCT coupon",
        {
            "coupon": True,
            "code": "10PCT",
            "discount_percent": 10,
        },
    )
    kg.add_entity(
        "coupon_expired",
        EntityType.OTHER,
        "EXPIRED coupon",
        {
            "coupon": True,
            "code": "EXPIRED",
            "discount_amount": 5.0,
            "valid_until": time.time() - 3600,
        },
    )
    kg.add_entity(
        "coupon_otherstore",
        EntityType.OTHER,
        "OTHERSTORE coupon",
        {
            "coupon": True,
            "code": "OTHERSTORE",
            "store_id": "store_2",
            "discount_amount": 1.0,
        },
    )
    return kg


# ── Coupon accepted ──────────────────────────────────────────────────────


def test_mb3009_valid_flat_discount_coupon_accepted():
    kg = _seed_catalog_and_coupons()
    add_to_cart(kg, "alice", MILK, quantity=1)

    result = apply_coupon_to_cart(kg, "alice", "SAVE3", store_id=STORE_ID)

    assert result.accepted is True
    assert result.discount_amount == 3.0
    assert result.new_total == 7.0

    # Applied durably — a later read of the cart reflects it.
    cart = get_cart(kg, "alice")
    assert cart.coupon_code == "SAVE3"
    assert cart.discount == 3.0
    assert cart.total == 7.0


def test_mb3009_valid_percent_discount_coupon_accepted():
    kg = _seed_catalog_and_coupons()
    add_to_cart(kg, "erin", MILK, quantity=1)

    result = apply_coupon_to_cart(kg, "erin", "10PCT", store_id=STORE_ID)

    assert result.accepted is True
    assert result.discount_amount == 1.0  # 10% of a $10.00 subtotal
    assert result.new_total == 9.0


def test_mb3009_coupon_valid_for_any_store_when_unscoped():
    kg = _seed_catalog_and_coupons()
    add_to_cart(kg, "frank", MILK, quantity=1)

    result = apply_coupon_to_cart(kg, "frank", "10PCT", store_id="some_other_store")
    assert result.accepted is True


# ── Coupon rejected ──────────────────────────────────────────────────────


def test_mb3009_nonexistent_coupon_rejected_as_forged():
    kg = _seed_catalog_and_coupons()
    add_to_cart(kg, "bob", MILK, quantity=1)
    before = get_cart(kg, "bob")

    result = apply_coupon_to_cart(kg, "bob", "NOTREAL", store_id=STORE_ID)

    assert result.accepted is False
    assert result.fraud_suspected is True
    # Cart is left completely unchanged on rejection.
    assert get_cart(kg, "bob") == before


def test_mb3009_expired_coupon_rejected_not_as_fraud():
    kg = _seed_catalog_and_coupons()
    add_to_cart(kg, "carol", MILK, quantity=1)

    result = apply_coupon_to_cart(kg, "carol", "EXPIRED", store_id=STORE_ID)

    assert result.accepted is False
    assert "expired" in result.reason
    # It was a real, honestly-issued coupon — just lapsed — not a forgery.
    assert result.fraud_suspected is False
    assert get_cart(kg, "carol").coupon_code == ""


def test_mb3009_coupon_for_different_store_rejected_as_fraud():
    kg = _seed_catalog_and_coupons()
    add_to_cart(kg, "dave", MILK, quantity=1)

    result = apply_coupon_to_cart(kg, "dave", "OTHERSTORE", store_id=STORE_ID)

    assert result.accepted is False
    assert result.fraud_suspected is True
    assert get_cart(kg, "dave").coupon_code == ""


def test_mb3009_apply_coupon_via_capability_bus():
    kg = _seed_catalog_and_coupons()
    add_to_cart(kg, "alice", MILK, quantity=1)
    bus = CommerceCapabilityBus([CommerceCapability()])

    assert bus.discover_operation("apply_coupon_to_cart") is not None

    accepted = bus.invoke("apply_coupon_to_cart", kg, "alice", "SAVE3", store_id=STORE_ID)
    assert accepted.accepted is True

    rejected = bus.invoke("apply_coupon_to_cart", kg, "alice", "BOGUS", store_id=STORE_ID)
    assert rejected.accepted is False
