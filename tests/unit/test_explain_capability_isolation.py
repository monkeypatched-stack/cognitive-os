"""Isolation regression: ExplainCapability ("why did you buy milk")
used to scan every actor's orders sharing a KnowledgeGraph with zero
ownership filter — a completely generic question from any actor could
return a DIFFERENT actor's real decision trace (candidate stores,
prices, trust scores, and reasoning), since the base KnowledgeGraph a
real actor tick uses is a single flat entity_id -> Entity pool shared
by every actor a PlanetaryRuntime manages (person_id is not a storage
partition), not something scoped per actor.

Exploit confirmed by code inspection: no attacker action beyond asking
a generic, first-person question ("why did you buy milk") was required
-- if the shared KG contained a more-recent or better-matching order
from a DIFFERENT actor, that actor's real reasoning came back in the
response. Fixed by scoping the order scan to attributes["buyer_id"] ==
context["actor_id"], the same field OrderCreationCapability already
persists onto every order.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.grocery import ExplainCapability
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

ALICE = "alice"
BOB = "bob"


def _seed_order(kg, order_id: str, buyer_id: str, item: str, created_at: float):
    kg.add_entity(
        order_id,
        EntityType.EVENT,
        "Grocery Order",
        {
            "order_id": order_id,
            "created_at": created_at,
            "decision_traces": [
                {
                    "item": item,
                    "candidates": [
                        {
                            "store": f"{buyer_id}'s Secret Store",
                            "price": 3.99,
                            "trust": 1.0,
                        }
                    ],
                    "optimization_label": "cost",
                    "chosen": {"store": f"{buyer_id}'s Secret Store"},
                    "confidence": "high",
                }
            ],
            "buyer_id": buyer_id,
        },
    )


def test_a_generic_question_never_returns_a_different_actors_decision_trace():
    kg = KnowledgeGraph()
    # Bob's order is more recent than Alice's -- before the fix, the
    # unscoped "most recent match" scan would return BOB's trace to
    # ALICE's identical question.
    _seed_order(kg, "ORD-alice-1", ALICE, "Milk", created_at=1000.0)
    _seed_order(kg, "ORD-bob-1", BOB, "Milk", created_at=2000.0)

    result = ExplainCapability().handle(
        {
            "context": {
                "knowledge_graph": kg,
                "actor_id": ALICE,
                "question": "why did you buy milk",
            }
        }
    )

    assert result["success"] is True
    assert result["chosen"]["store"] == "alice's Secret Store"
    assert "bob's Secret Store" not in result["explanation"]


def test_an_actor_with_no_orders_of_their_own_gets_an_honest_failure_not_someone_elses_trace():
    kg = KnowledgeGraph()
    _seed_order(kg, "ORD-bob-1", BOB, "Milk", created_at=2000.0)

    result = ExplainCapability().handle(
        {
            "context": {
                "knowledge_graph": kg,
                "actor_id": ALICE,
                "question": "why did you buy milk",
            }
        }
    )

    assert result["success"] is False
    assert "no past decision" in result["error"]


def test_the_actors_own_matching_order_is_still_found_normally():
    kg = KnowledgeGraph()
    _seed_order(kg, "ORD-alice-1", ALICE, "Milk", created_at=1000.0)

    result = ExplainCapability().handle(
        {
            "context": {
                "knowledge_graph": kg,
                "actor_id": ALICE,
                "question": "why did you buy milk",
            }
        }
    )

    assert result["success"] is True
    assert result["chosen"]["store"] == "alice's Secret Store"
