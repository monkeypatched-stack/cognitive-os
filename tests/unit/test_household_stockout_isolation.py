"""Isolation regression: predict_household_stockout's real call site in
HouseholdCognitionCapability never threaded the actor_id predict_demand
itself already supports (predict_demand's own docstring documents the
exact bug this closes: "one actor's demand prediction silently absorbed
every OTHER actor's concurrent purchases as if they were its own
history"). Without actor_id, a household's "how many days will this
last" was answered from every actor sharing the KG's combined purchase
rate, not this household's real consumption -- inflating the rate (and
therefore incorrectly predicting an URGENT stockout, or masking a real
one) for reasons that have nothing to do with what this household
actually buys.
"""

from __future__ import annotations

import time

from src.monkey_brain.kernel.domains.grocery import (
    predict_household_stockout,
    update_order_stats,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

PRODUCT_ID = "product_milk"


def _seed(kg, current_pantry_qty: float):
    kg.add_entity(PRODUCT_ID, EntityType.ASSET, "Milk", {"price": 3.99, "quantity": 50})
    now = time.time()
    # Alice: a light, real 1-unit-per-day habit.
    update_order_stats(kg, "alice", [{"product_id": PRODUCT_ID, "qty": 1}], now)
    # Bob: a much heavier, unrelated 20-units-a-day habit sharing the same KG.
    update_order_stats(kg, "bob", [{"product_id": PRODUCT_ID, "qty": 20}], now)
    pantry_entity = kg.get_entity(PRODUCT_ID)
    pantry_entity.attributes["quantity"] = current_pantry_qty
    return pantry_entity


def test_actor_scoped_prediction_reflects_only_that_actors_own_purchases():
    kg = KnowledgeGraph()
    pantry_entity = _seed(kg, current_pantry_qty=10.0)

    alice_stockout = predict_household_stockout(kg, pantry_entity, PRODUCT_ID, actor_id="alice")

    # Alice's own real rate is ~1/day; 10 units on hand should last
    # roughly 10 days, not the few hours a 21/day combined rate implies.
    assert alice_stockout["daily_rate"] == 1.0
    assert alice_stockout["days_remaining"] == 10.0
    assert alice_stockout["urgent"] is False


def test_unscoped_call_still_preserves_the_old_combined_behavior():
    """actor_id=None is an explicit, documented opt-out (existing
    callers that haven't threaded an actor through yet) -- this proves
    the isolation fix is additive, not a silent behavior change for
    anything still relying on the old signature."""
    kg = KnowledgeGraph()
    pantry_entity = _seed(kg, current_pantry_qty=10.0)

    combined_stockout = predict_household_stockout(kg, pantry_entity, PRODUCT_ID)

    assert combined_stockout["daily_rate"] != 1.0
