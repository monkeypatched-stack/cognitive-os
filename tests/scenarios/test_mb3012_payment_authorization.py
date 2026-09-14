"""MB-3012 Payment Authorization — credit card payment scenario.

Credit card.

Expected:
    - Payment approved.

Like MB-3010, this needed no new code: payment processing already exists
in kernel/domains/grocery.py as PaymentConfirmationCapability (checks
authorization/affordability before committing to anything) and
PaymentCapability (the actual charge). "Credit card" maps to this
codebase's real "credit" account_type (kernel/domains/finance.py) — a
credit account's available funds are credit_limit - balance ("balance" is
debt owed, not funds on hand), chosen automatically over cash/food-
assistance sources by choose_payment_source() when it can cover the
total. No existing test exercised either capability with a real credit
account before this one.
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


def _seed_world(credit_limit: float = 1000.0, existing_balance: float = 0.0) -> KnowledgeGraph:
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
            "balance": existing_balance,
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


def _place_order(kg: KnowledgeGraph) -> dict:
    order = OrderCreationCapability().handle(
        {
            "context": {
                "knowledge_graph": kg,
                "selected_product": _cart(),
                "actor_id": ACTOR_ID,
                "question": "deliver my order",
            }
        }
    )
    assert order["success"] is True, order
    return order


def test_mb3012_credit_card_payment_approved():
    kg = _seed_world()
    order = _place_order(kg)
    context = {
        "knowledge_graph": kg,
        "total": order["total"],
        "order": order,
        "actor_id": ACTOR_ID,
        "selected_product": _cart(),
    }

    confirmation = PaymentConfirmationCapability().handle({"context": context})
    assert confirmation["success"] is True
    assert confirmation["payment_source"] == "credit"

    payment = PaymentCapability().handle({"context": context})

    assert payment["success"] is True
    assert payment["status"] == "completed"
    assert payment["payment_source"] == "credit"
    assert payment["amount"] == order["total"]
    assert payment["processor"] == "Stripe"

    # Charged durably: the credit account's balance (debt owed) increased
    # by exactly the order total, and the order itself is marked paid.
    account = kg.get_entity(CREDIT_ACCOUNT_ID)
    assert account.attributes["balance"] == order["total"]
    paid_order = kg.get_entity(order["order_id"])
    assert paid_order.attributes["payment_status"] == "paid"
    assert paid_order.attributes["paid_amount"] == order["total"]


def test_mb3012_credit_card_chosen_over_no_other_source():
    """A single credit account, no cash/food-assistance alternative — the
    credit card must still be the account PaymentConfirmation picks and
    PaymentCapability actually charges (not silently falling through to
    "no wallet found")."""
    kg = _seed_world()
    order = _place_order(kg)
    context = {
        "knowledge_graph": kg,
        "total": order["total"],
        "order": order,
        "actor_id": ACTOR_ID,
        "selected_product": _cart(),
    }
    PaymentConfirmationCapability().handle({"context": context})
    assert context["chosen_payment_source"] == CREDIT_ACCOUNT_ID


def test_mb3012_credit_card_approved_with_existing_balance_within_limit():
    """Approval depends on available CREDIT (limit - balance), not on the
    balance being zero — a card that already carries some debt is still
    approved as long as the new charge fits within what's left."""
    kg = _seed_world(credit_limit=1000.0, existing_balance=200.0)
    order = _place_order(kg)
    context = {
        "knowledge_graph": kg,
        "total": order["total"],
        "order": order,
        "actor_id": ACTOR_ID,
        "selected_product": _cart(),
    }

    confirmation = PaymentConfirmationCapability().handle({"context": context})
    payment = PaymentCapability().handle({"context": context})

    assert confirmation["success"] is True
    assert payment["success"] is True
    account = kg.get_entity(CREDIT_ACCOUNT_ID)
    assert account.attributes["balance"] == round(200.0 + order["total"], 2)
