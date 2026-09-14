"""FRAUD-001..004 — fraud-review policy vs. test-harness distinction
qualification tests (Qualification Gap Closure, Phase 6).

The real fraud gate (kernel/domains/finance.py::assess_transaction_risk,
already wired into PaymentConfirmationCapability/PaymentCapability,
qualified by test_mb3014_fraud_detection.py) is untouched — same
threshold, same two signals, same decision. What was missing: no
observable state let a caller (a real user, or a qualification harness
re-testing rapidly) distinguish "the system is broken" from "this
specific transaction is correctly held, and here's when it stops being
held." assess_transaction_risk now also returns velocity_cooldown_until
— derived from the SAME real order-history read the velocity signal
already computes, never a separately-tracked value that could drift from
it — and GET /actors/{id}/fraud-status (api/routes/actors.py) exposes
this real assessment read-only, before any transaction is attempted.
"""

from __future__ import annotations

import time

from src.monkey_brain.kernel.domains.finance import assess_transaction_risk
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

ACTOR_ID = "fraud_policy_actor"


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


def test_fraud001_a_legitimate_transaction_is_not_held():
    """Baseline: no real order history, a modest amount -- the gate isn't
    just always-on."""
    kg = KnowledgeGraph()
    result = assess_transaction_risk(kg, ACTOR_ID, 25.0)
    assert result["high_risk"] is False
    assert result["velocity_cooldown_until"] is None


def test_fraud002_inside_a_real_cooldown_window_is_held_and_attributable():
    """10 real orders inside the last hour trips the velocity signal --
    held, AND the hold is attributable to a real, inspectable policy
    reason with a real cooldown timestamp, not an opaque failure
    indistinguishable from a cognitive bug.

    _FRAUD_VELOCITY_THRESHOLD (finance.py) was later deliberately raised
    from 3 to 10 (that threshold was tripping on ordinary live/demo
    usage — a handful of real purchase walkthroughs in quick succession
    looked identical to actual card-testing at n=3) — this test's own
    seed count was never updated to match, so it silently stopped
    exercising the real gate at all (3 orders no longer trips it)."""
    kg = KnowledgeGraph()
    now = time.time()
    _seed_order_history(kg, ACTOR_ID, count=10, total=20.0, when=now - 60)

    result = assess_transaction_risk(kg, ACTOR_ID, 20.0, now=now)

    assert result["high_risk"] is True
    assert any("orders from this actor in the last" in r for r in result["reasons"])
    assert result["velocity_cooldown_until"] is not None
    assert result["velocity_cooldown_until"] > now


def test_fraud003_after_the_real_cooldown_window_it_succeeds():
    """The exact same actor and the exact same recent-order history, but
    `now` has genuinely advanced past velocity_cooldown_until -- this is
    the real distinction a legitimate rapid re-tester needs: the SAME
    prior activity that held transaction #2 must not hold transaction #3
    once the window has genuinely elapsed."""
    kg = KnowledgeGraph()
    now = time.time()
    # Same threshold correction as test_fraud002 above.
    _seed_order_history(kg, ACTOR_ID, count=10, total=20.0, when=now - 60)

    held = assess_transaction_risk(kg, ACTOR_ID, 20.0, now=now)
    assert held["high_risk"] is True
    cooldown_until = held["velocity_cooldown_until"]

    later = cooldown_until + 1.0
    retried = assess_transaction_risk(kg, ACTOR_ID, 20.0, now=later)
    assert retried["high_risk"] is False
    assert retried["velocity_cooldown_until"] is None


def test_fraud004_a_genuinely_anomalous_amount_is_held_regardless_of_cooldown_state():
    """No velocity issue at all (this actor's real history is old, well
    outside any cooldown), but the requested amount is a real, extreme
    outlier against that actor's own historical average -- security
    remains authoritative on this second, independent signal regardless
    of velocity_cooldown_until being None."""
    kg = KnowledgeGraph()
    now = time.time()
    _seed_order_history(kg, ACTOR_ID, count=3, total=20.0, when=now - 86400)

    result = assess_transaction_risk(kg, ACTOR_ID, 500.0, now=now)  # 25x historical average

    assert result["high_risk"] is True
    assert result["velocity_cooldown_until"] is None
    assert any("historical average" in r for r in result["reasons"])
