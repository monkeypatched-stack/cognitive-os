"""MB-3049 Loyalty Program — award/redeem points scenario.

Award points. Redeem points.

No points/loyalty concept existed. Built kernel/domains/finance.py::
award_points()/redeem_points()/get_points_balance() for this. Earning:
1 point per dollar of a real, "completed" order (MB-3022) — never for
an order still in progress, cancelled, or returned — and idempotent
per order_id, so a retried award call can never double-credit one
purchase. Per explicit design choice ("direct wallet credit"):
redemption converts points straight into real wallet balance (100
points = $1) via the same wallet _find_wallet() already resolves for
checkout, not a separate parallel currency or a one-order discount
code. A points balance lives on its own EntityType.OTHER marker,
deliberately not an EntityType.ACCOUNT, so it's never mistaken for a
spendable wallet elsewhere in finance.py.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.finance import (
    FinanceCapability,
    award_points,
    get_points_balance,
    redeem_points,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

ACTOR_ID = "alice"
ORDER_ID = "ORD-1"


def _seed_completed_order(order_id: str = ORDER_ID, total: float = 45.0) -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(
        "wallet_1",
        EntityType.ACCOUNT,
        "Alice Wallet",
        {
            "account_type": "debit",
            "balance": 10.0,
            "owner": ACTOR_ID,
        },
    )
    kg.add_entity(
        order_id,
        EntityType.EVENT,
        "Grocery Order",
        {
            "status": "completed",
            "total": total,
            "buyer_id": ACTOR_ID,
        },
    )
    return kg


def test_mb3049_cannot_award_points_for_a_non_completed_order():
    kg = _seed_completed_order()
    kg.update_entity(ORDER_ID, attributes={"status": "delivered"})

    result = award_points(kg, ACTOR_ID, ORDER_ID)

    assert result["success"] is False


def test_mb3049_award_points_for_a_completed_order():
    kg = _seed_completed_order(total=45.0)

    result = award_points(kg, ACTOR_ID, ORDER_ID)

    assert result["success"] is True
    assert result["points_earned"] == 45
    assert result["points_balance"] == 45
    assert get_points_balance(kg, ACTOR_ID)["points_balance"] == 45


def test_mb3049_cannot_award_points_twice_for_the_same_order():
    kg = _seed_completed_order()
    award_points(kg, ACTOR_ID, ORDER_ID)

    result = award_points(kg, ACTOR_ID, ORDER_ID)

    assert result["success"] is False
    assert "already awarded" in result["error"]


def test_mb3049_points_accumulate_across_orders():
    kg = _seed_completed_order(total=45.0)
    award_points(kg, ACTOR_ID, ORDER_ID)
    kg.add_entity(
        "ORD-2",
        EntityType.EVENT,
        "Grocery Order",
        {
            "status": "completed",
            "total": 10.0,
            "buyer_id": ACTOR_ID,
        },
    )

    award_points(kg, ACTOR_ID, "ORD-2")

    assert get_points_balance(kg, ACTOR_ID)["points_balance"] == 55


def test_mb3049_cannot_redeem_more_points_than_available():
    kg = _seed_completed_order()
    award_points(kg, ACTOR_ID, ORDER_ID)

    result = redeem_points(kg, ACTOR_ID, 1000)

    assert result["success"] is False


def test_mb3049_redeeming_points_credits_the_real_wallet():
    kg = _seed_completed_order(total=45.0)
    award_points(kg, ACTOR_ID, ORDER_ID)

    result = redeem_points(kg, ACTOR_ID, 40)

    assert result["success"] is True
    assert result["dollar_value"] == 0.4
    assert kg.get_entity("wallet_1").attributes["balance"] == 10.4
    assert get_points_balance(kg, ACTOR_ID)["points_balance"] == 5


def test_mb3049_cannot_redeem_a_non_positive_amount():
    kg = _seed_completed_order()
    award_points(kg, ACTOR_ID, ORDER_ID)

    result = redeem_points(kg, ACTOR_ID, 0)

    assert result["success"] is False


def test_mb3049_actor_with_no_points_cannot_redeem():
    kg = KnowledgeGraph()

    result = redeem_points(kg, "nobody", 10)

    assert result["success"] is False


def test_mb3049_points_with_no_wallet_cannot_be_redeemed():
    kg = KnowledgeGraph()
    kg.add_entity(
        "ORD-3",
        EntityType.EVENT,
        "Grocery Order",
        {
            "status": "completed",
            "total": 20.0,
            "buyer_id": "carol",
        },
    )
    award_points(kg, "carol", "ORD-3")

    result = redeem_points(kg, "carol", 10)

    assert result["success"] is False
    assert "no wallet found" in result["error"]


def test_mb3049_loyalty_operations_via_capability():
    kg = _seed_completed_order()
    cap = FinanceCapability()

    for op in ("award_points", "redeem_points", "get_points_balance"):
        assert cap.can_handle(op), op

    result = cap.invoke("award_points", kg, ACTOR_ID, ORDER_ID)
    assert result["success"] is True
