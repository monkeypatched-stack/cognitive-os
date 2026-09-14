"""MB-3014 Fraud Detection — high-risk transaction scenario.

High-risk transaction.

Expected:
    - Fraud workflow.

Unlike MB-3010/3012/3013, nothing real backed this before: the only
existing "fraud" module (kernel/plan/intents/predicates/fraud_detection.py)
is a disconnected, MongoDB-backed chatbot stub that assigns every flagged
transaction the exact same hardcoded risk_score (0.85) regardless of what
the transaction actually is — it can't distinguish high-risk from
low-risk, and has no connection to the real checkout/payment pipeline
(PaymentConfirmationCapability/PaymentCapability had zero risk-scoring
logic).

Built kernel/domains/finance.py::assess_transaction_risk() instead: real
risk signals computed from an actor's ACTUAL persisted order history
(the same EntityType.EVENT order entities monthly_spending()/
check_monthly_cap() already read) —
  - velocity: 10+ of this actor's own orders within the last hour (raised
    from 3 in a later pass — that threshold tripped on ordinary live/demo
    usage, see finance.py::_FRAUD_VELOCITY_THRESHOLD's own docstring)
  - amount anomaly: order total > 5x this actor's historical average
    (only evaluated when real history exists)
— wired into BOTH PaymentConfirmationCapability and PaymentCapability
(the same "verify directly, don't trust a prior check" principle this
file already applies to RBAC/separation-of-duties), checked BEFORE any
balance/account lookup so a high-risk request never reaches the
balance-lookup code path that would otherwise leak real account details
to what might be a stolen-card test. The fraud workflow: a high-risk
transaction is HELD for review (fraud_review=True, with the real
risk_assessment attached) — never silently approved, and reported
distinctly from an ordinary decline.
"""

from __future__ import annotations

import time

from src.monkey_brain.kernel.domains.finance import assess_transaction_risk
from src.monkey_brain.kernel.domains.grocery import (
    OrderCreationCapability,
    PaymentCapability,
    PaymentConfirmationCapability,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

ACTOR_ID = "alice"
STORE_ID = "store_1"
CREDIT_ACCOUNT_ID = "acct_alice_credit"


def _seed_world() -> KnowledgeGraph:
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
            "quantity": 100,
            "store_id": STORE_ID,
        },
    )
    kg.add_entity(
        CREDIT_ACCOUNT_ID,
        EntityType.ACCOUNT,
        "Alice Credit Card",
        {
            "account_type": "credit",
            "credit_limit": 100_000.0,
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


def _seed_order_history(kg: KnowledgeGraph, actor_id: str, count: int, total: float, when: float) -> None:
    for i in range(count):
        kg.add_entity(
            f"ord_hist_{actor_id}_{i}",
            EntityType.EVENT,
            "Grocery Order",
            {
                "order_id": f"ord_hist_{actor_id}_{i}",
                "buyer_id": actor_id,
                "total": total,
                "created_at": when,
            },
        )


# ── assess_transaction_risk() itself ─────────────────────────────────────


def test_mb3014_first_ever_order_is_not_flagged():
    kg = _seed_world()
    result = assess_transaction_risk(kg, ACTOR_ID, 500.0)
    assert result["high_risk"] is False
    assert result["risk_score"] == 0.0


def test_mb3014_anomalous_amount_flagged_as_high_risk():
    kg = _seed_world()
    now = time.time()
    _seed_order_history(kg, ACTOR_ID, count=3, total=20.0, when=now - 86400)

    normal = assess_transaction_risk(kg, ACTOR_ID, 25.0, now=now)
    assert normal["high_risk"] is False

    anomalous = assess_transaction_risk(kg, ACTOR_ID, 200.0, now=now)  # 10x historical average
    assert anomalous["high_risk"] is True
    assert "historical average" in anomalous["reasons"][0]


def test_mb3014_high_velocity_flagged_as_high_risk():
    kg = _seed_world()
    now = time.time()
    # _FRAUD_VELOCITY_THRESHOLD (finance.py) raised from 3 to 10 in a
    # later pass -- see module docstring.
    _seed_order_history(kg, ACTOR_ID, count=10, total=20.0, when=now - 60)

    result = assess_transaction_risk(kg, ACTOR_ID, 20.0, now=now)

    assert result["high_risk"] is True
    assert "orders from this actor in the last" in result["reasons"][0]


def test_mb3014_unrelated_actor_history_does_not_affect_assessment():
    kg = _seed_world()
    now = time.time()
    _seed_order_history(kg, "someone_else", count=5, total=1000.0, when=now - 60)

    result = assess_transaction_risk(kg, ACTOR_ID, 20.0, now=now)
    assert result["high_risk"] is False


# ── the fraud workflow, wired into real checkout ─────────────────────────


def test_mb3014_high_risk_transaction_held_at_confirmation_and_payment():
    kg = _seed_world()
    now = time.time()
    # 10 orders in the last hour -- this actor's 11th (the one about to
    # be placed) will trip the velocity signal. Threshold raised from 3
    # to 10 in a later pass -- see module docstring.
    _seed_order_history(kg, ACTOR_ID, count=10, total=20.0, when=now - 60)

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
    assert order["success"] is True
    context = {
        "knowledge_graph": kg,
        "total": order["total"],
        "order": order,
        "actor_id": ACTOR_ID,
        "selected_product": _cart(),
    }

    confirmation = PaymentConfirmationCapability().handle({"context": context})
    assert confirmation["success"] is False
    assert confirmation["fraud_review"] is True
    assert confirmation["risk_assessment"]["high_risk"] is True

    # PaymentConfirmation's rejection doesn't stop ActionExecutor from
    # running Payment anyway (by design, for every capability here) — the
    # check must be independently re-verified at the actual charge point.
    payment = PaymentCapability().handle({"context": context})
    assert payment["success"] is False
    assert payment["fraud_review"] is True

    # Held, not charged: no side effects at all.
    assert kg.get_entity("prod_milk").attributes["quantity"] == 100
    assert kg.get_entity(CREDIT_ACCOUNT_ID).attributes["balance"] == 0.0


def test_mb3014_normal_transaction_is_not_held():
    kg = _seed_world()

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
    assert payment.get("fraud_review") is None
