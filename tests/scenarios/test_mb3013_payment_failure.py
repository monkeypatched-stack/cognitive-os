"""MB-3013 Payment Failure — declined card retry scenario.

Declined card.

Expected:
    - Retry.

Like MB-3010/MB-3012, no new code was needed: kernel/domains/grocery.py's
OrderCreationCapability already documents a real "interrupt/resume
safety" mechanism (context["resume_order_id"]) for exactly this —
retrying a checkout after a failed attempt reuses the SAME order_id
(no duplicate order, no double reservation) rather than minting a new
one, and if the retry finds the order already reached a genuine
successful charge in the meantime, it returns that fact as a safe no-op
instead of charging twice. This scenario is: a card gets declined
(insufficient credit), the customer fixes the problem (raises their
limit / a real customer would swap cards) and retries — the retry must
succeed cleanly, without ever double-charging or double-decrementing
stock, and a genuinely late/duplicate retry after success must also be
a safe no-op.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.grocery import (
    OrderCreationCapability,
    PaymentCapability,
    PaymentConfirmationCapability,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

ACTOR_ID = "alice"
STORE_ID = "store_1"
CREDIT_ACCOUNT_ID = "acct_alice_credit"


def _seed_world(credit_limit: float) -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(
        STORE_ID,
        EntityType.ORGANIZATION,
        "Key Food",
        {
            "address": "200 W 23rd St, New York, NY",
            "delivery_fee": 4.99,
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
        CREDIT_ACCOUNT_ID,
        EntityType.ACCOUNT,
        "Alice Credit Card",
        {
            "account_type": "credit",
            "credit_limit": credit_limit,
            "balance": 0.0,
            "owner": ACTOR_ID,
        },
    )
    kg.add_entity(
        "proc_stripe",
        EntityType.ORGANIZATION,
        "Stripe",
        {
            "type": "payment_processor",
            "priority": 0,
        },
    )
    return kg


def _cart() -> list[dict]:
    return [
        {
            "id": "prod_milk",
            "name": "Milk",
            "price": 3.99,
            "qty": 2,
            "store_id": STORE_ID,
            "store_name": "Key Food",
        }
    ]


def _place_order(kg: KnowledgeGraph, resume_order_id: str | None = None) -> dict:
    context = {
        "knowledge_graph": kg,
        "selected_product": _cart(),
        "actor_id": ACTOR_ID,
        "question": "deliver my order",
    }
    if resume_order_id:
        context["resume_order_id"] = resume_order_id
    return OrderCreationCapability().handle({"context": context})


def _attempt_payment(kg: KnowledgeGraph, order: dict) -> dict:
    context = {
        "knowledge_graph": kg,
        "total": order["total"],
        "order": order,
        "actor_id": ACTOR_ID,
        "selected_product": _cart(),
    }
    PaymentConfirmationCapability().handle({"context": context})
    return PaymentCapability().handle({"context": context})


def test_mb3013_declined_card_is_rejected_and_leaves_no_side_effects():
    kg = _seed_world(credit_limit=5.0)  # too little to cover a ~$13.61 order
    order = _place_order(kg)

    result = _attempt_payment(kg, order)

    assert result["success"] is False
    assert "insufficient credit" in result["error"]
    # A declined payment must not touch stock or mark the order paid — the
    # hold from OrderCreation's try_reserve was never confirmed.
    assert kg.get_entity("prod_milk").attributes["quantity"] == 10
    assert kg.get_entity(order["order_id"]).attributes.get("payment_status") != "paid"


def test_mb3013_retry_after_declined_card_succeeds():
    kg = _seed_world(credit_limit=5.0)
    order = _place_order(kg)
    declined = _attempt_payment(kg, order)
    assert declined["success"] is False

    # The customer's card issuer raises their limit (or, in a real
    # checkout, they'd swap to a different card) and retries.
    kg.update_entity(CREDIT_ACCOUNT_ID, attributes={"credit_limit": 1000.0})

    retry_order = _place_order(kg, resume_order_id=order["order_id"])
    assert retry_order["success"] is True
    assert retry_order["order_id"] == order["order_id"], "retry reuses the same order, not a duplicate"

    retry_payment = _attempt_payment(kg, retry_order)
    assert retry_payment["success"] is True

    paid_order = kg.get_entity(order["order_id"])
    assert paid_order.attributes["payment_status"] == "paid"
    assert paid_order.attributes["paid_amount"] == retry_order["total"]
    # Stock decremented exactly once, by the successful retry — the
    # declined first attempt never confirmed its hold.
    assert kg.get_entity("prod_milk").attributes["quantity"] == 8


def test_mb3013_late_duplicate_retry_after_success_is_a_safe_no_op():
    kg = _seed_world(credit_limit=1000.0)
    order = _place_order(kg)
    payment = _attempt_payment(kg, order)
    assert payment["success"] is True

    # A late/duplicate retry (e.g. a lost response resubmitted) must not
    # charge or decrement stock a second time.
    duplicate_retry = _place_order(kg, resume_order_id=order["order_id"])

    assert duplicate_retry["success"] is True
    assert duplicate_retry.get("resumed") is True
    assert kg.get_entity("prod_milk").attributes["quantity"] == 8
