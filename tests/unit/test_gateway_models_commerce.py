"""Gate 4 — unit tests for the Gate 2 (Production API) Pydantic models in
gateway_models.py: Commerce/Orders/Fulfillment/Events/Presence/Verify.

These are the 28 models that replaced raw dict bodies with real validation
(ADR from Gate 2) — this file locks down the specific constraints that
were the whole point of that pass (required fields, numeric bounds,
non-empty lists) so a future edit can't silently loosen them back to
"anything goes" without a test noticing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.monkey_brain.api.gateway_models import (
    CartItemAddRequest,
    EventCreateRequest,
    InventoryBackorderRequest,
    InventoryReserveRequest,
    MerchantCreateRequest,
    OrderCreateRequest,
    OrderRefundRequest,
    ProductCreateRequest,
    PromotionCreateRequest,
    ReviewCreateRequest,
    ShipmentCreateRequest,
    ShipmentDelayRequest,
    VerifyReportResponse,
    WalletCreateRequest,
)


def test_merchant_requires_merchant_id_and_store_name():
    MerchantCreateRequest(merchant_id="m1", store_name="Bob's Store")  # doesn't raise
    with pytest.raises(ValidationError):
        MerchantCreateRequest(store_name="Bob's Store")
    with pytest.raises(ValidationError):
        MerchantCreateRequest(merchant_id="m1")
    with pytest.raises(ValidationError):
        MerchantCreateRequest(merchant_id="", store_name="Bob's Store")


def test_merchant_allows_extra_attributes():
    """merchant.py's onboard_merchant() forwards arbitrary extra kwargs as
    store attributes — the model must not silently drop them."""
    body = MerchantCreateRequest(merchant_id="m1", store_name="Bob's", rating=4.9, custom_flag=True)
    assert body.model_dump()["rating"] == 4.9
    assert body.model_dump()["custom_flag"] is True


def test_wallet_requires_owner_only():
    WalletCreateRequest(owner="actor-1")  # doesn't raise, everything else defaults
    with pytest.raises(ValidationError):
        WalletCreateRequest()


def test_product_price_must_be_non_negative():
    ProductCreateRequest(store_id="s1", merchant_id="m1", name="Mouse", price=0)  # 0 is valid
    ProductCreateRequest(store_id="s1", merchant_id="m1", name="Mouse", price=59.99)
    with pytest.raises(ValidationError):
        ProductCreateRequest(store_id="s1", merchant_id="m1", name="Mouse", price=-1)


def test_product_quantity_must_be_non_negative():
    with pytest.raises(ValidationError):
        ProductCreateRequest(store_id="s1", merchant_id="m1", name="Mouse", price=1.0, quantity=-1)


def test_product_requires_all_four_core_fields():
    with pytest.raises(ValidationError):
        ProductCreateRequest(merchant_id="m1", name="Mouse", price=1.0)  # missing store_id
    with pytest.raises(ValidationError):
        ProductCreateRequest(store_id="s1", name="Mouse", price=1.0)  # missing merchant_id
    with pytest.raises(ValidationError):
        ProductCreateRequest(store_id="s1", merchant_id="m1", price=1.0)  # missing name


def test_promotion_sale_price_must_be_non_negative():
    PromotionCreateRequest(merchant_id="m1", sale_price=0)
    with pytest.raises(ValidationError):
        PromotionCreateRequest(merchant_id="m1", sale_price=-5)


def test_review_rating_bounded_zero_to_five():
    ReviewCreateRequest(actor_id="a1", rating=0)
    ReviewCreateRequest(actor_id="a1", rating=5)
    with pytest.raises(ValidationError):
        ReviewCreateRequest(actor_id="a1", rating=5.1)
    with pytest.raises(ValidationError):
        ReviewCreateRequest(actor_id="a1", rating=-0.1)
    with pytest.raises(ValidationError):
        ReviewCreateRequest(actor_id="a1", rating=99)


def test_cart_item_quantity_must_be_at_least_one():
    CartItemAddRequest(product_id="p1", quantity=1)
    with pytest.raises(ValidationError):
        CartItemAddRequest(product_id="p1", quantity=0)
    with pytest.raises(ValidationError):
        CartItemAddRequest(product_id="p1", quantity=-1)


def test_inventory_reserve_requires_product_and_actor_and_positive_qty():
    InventoryReserveRequest(product_id="p1", actor_id="a1")  # qty defaults to 1
    with pytest.raises(ValidationError):
        InventoryReserveRequest(product_id="p1", actor_id="a1", qty=0)
    with pytest.raises(ValidationError):
        InventoryReserveRequest(actor_id="a1")  # missing product_id


def test_inventory_reserve_hold_seconds_must_be_positive():
    InventoryReserveRequest(product_id="p1", actor_id="a1", hold_seconds=0.1)
    with pytest.raises(ValidationError):
        InventoryReserveRequest(product_id="p1", actor_id="a1", hold_seconds=0)
    with pytest.raises(ValidationError):
        InventoryReserveRequest(product_id="p1", actor_id="a1", hold_seconds=-1)


def test_inventory_backorder_qty_must_be_at_least_one():
    with pytest.raises(ValidationError):
        InventoryBackorderRequest(product_id="p1", actor_id="a1", qty=0)


def test_order_requires_actor_id_and_non_empty_items():
    OrderCreateRequest(actor_id="a1", items=[{"id": "p1", "qty": 1}])
    with pytest.raises(ValidationError):
        OrderCreateRequest(actor_id="a1", items=[])
    with pytest.raises(ValidationError):
        OrderCreateRequest(items=[{"id": "p1"}])  # missing actor_id


def test_order_refund_amount_must_be_non_negative_when_given():
    OrderRefundRequest()  # amount is optional
    OrderRefundRequest(amount=0)
    with pytest.raises(ValidationError):
        OrderRefundRequest(amount=-1)


def test_shipment_requires_order_id_and_non_empty_packages():
    ShipmentCreateRequest(order_id="o1", packages=[{"box_type": "standard"}])
    with pytest.raises(ValidationError):
        ShipmentCreateRequest(order_id="o1", packages=[])
    with pytest.raises(ValidationError):
        ShipmentCreateRequest(packages=[{"box_type": "standard"}])  # missing order_id


def test_shipment_delay_requires_non_empty_reason():
    ShipmentDelayRequest(reason="severe weather")
    with pytest.raises(ValidationError):
        ShipmentDelayRequest(reason="")
    with pytest.raises(ValidationError):
        ShipmentDelayRequest()


def test_event_requires_non_empty_type():
    EventCreateRequest(type="fire")
    with pytest.raises(ValidationError):
        EventCreateRequest(type="")
    with pytest.raises(ValidationError):
        EventCreateRequest()


def test_event_allows_extra_fields_for_context_stream_payload():
    body = EventCreateRequest(type="flash_sale", discount_pct=20)
    assert body.model_dump()["discount_pct"] == 20


def test_verify_report_response_has_categories_field():
    """Gate 3's ten-category report shape (ADR-010) — locks down that the
    'categories' key this session added stays part of the contract."""
    report = VerifyReportResponse(
        ok=False,
        violation_count=1,
        violations=[{"category": "presence_consistency", "type": "actor_without_presence"}],
        categories={"presence_consistency": 1},
    )
    assert report.categories == {"presence_consistency": 1}
    assert report.violation_count == len(report.violations)
