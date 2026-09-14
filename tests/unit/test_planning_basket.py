"""CCB-101 — Cheapest Basket, now flowing through the centralized
LLMPlanner (kernel/pipeline/llm_planner.py) instead of hardcoded
consolidate-vs-split arithmetic. Per the centralized-planning
constitution, the kernel never decides which store wins or whether to
split the basket — it only serializes the goal+facts into a prompt and
parses back whatever plan the model returns. These tests inject a
FakeBackend with a canned JSON response standing in for what a real LLM
would decide, given the same milk/eggs/bread-across-stores facts CCB-101
has used all session, and verify the plumbing carries that decision
through to a real Plan/PlanStep — not that any particular arithmetic is
"correct" (there is none left in this code path to test).
"""

from __future__ import annotations

import asyncio
import json

from src.monkey_brain.kernel.pipeline.belief_state import BeliefState, Goal
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner


class FakeBackend:
    def __init__(self, response: str):
        self.response = response
        self.last_prompt = None

    async def complete(self, prompt, system="", max_tokens=None, **kwargs):
        self.last_prompt = prompt
        return self.response


def _basket_goal():
    return Goal(
        name="acquire_basket",
        description="milk eggs bread",
        success_criteria=("milk", "eggs", "bread"),
    )


def _basket_belief():
    """Same fact shape CCB-101's manual walkthrough used: composite
    "store:item" entity keys for per-item price/stock, plain "store"
    entity keys for travel distance. The kernel doesn't parse this
    convention — it's serialized as plain text; only the LLM (or, in
    these tests, the canned FakeBackend response standing in for one)
    interprets it."""
    belief = BeliefState(actor_id="alice")
    belief.add_fact(entity="costco", attribute="distance", value=6.0, confidence=0.9)
    belief.add_fact(entity="costco:milk", attribute="price", value=2.10, confidence=0.9)
    belief.add_fact(entity="costco:milk", attribute="stock", value=50, confidence=0.9)
    belief.add_fact(entity="costco:eggs", attribute="price", value=2.50, confidence=0.9)
    belief.add_fact(entity="costco:eggs", attribute="stock", value=40, confidence=0.9)
    belief.add_fact(entity="cornerstore", attribute="distance", value=0.3, confidence=0.9)
    for item, price in (("milk", 3.20), ("eggs", 3.50), ("bread", 2.80)):
        belief.add_fact(entity=f"cornerstore:{item}", attribute="price", value=price, confidence=0.9)
        belief.add_fact(entity=f"cornerstore:{item}", attribute="stock", value=10, confidence=0.9)
    return belief


def test_consolidated_decision_flows_through_to_plan():
    """Costco doesn't stock bread at all, so a real model reasoning over
    these facts would have to consolidate at cornerstore (the only store
    covering the whole basket) despite it being pricier per item — this
    stands in for that decision via a canned response."""
    response = json.dumps(
        {
            "steps": [
                {
                    "action": "buy_milk",
                    "description": "Buy milk at cornerstore: $3.20 (consolidated)",
                    "expected_outcome": "milk acquired",
                    "cost": 0.1,
                    "confidence": 0.85,
                },
                {
                    "action": "buy_eggs",
                    "description": "Buy eggs at cornerstore: $3.50 (consolidated)",
                    "expected_outcome": "eggs acquired",
                    "cost": 0.1,
                    "confidence": 0.85,
                },
                {
                    "action": "buy_bread",
                    "description": "Buy bread at cornerstore: $2.80 (consolidated)",
                    "expected_outcome": "bread acquired",
                    "cost": 0.1,
                    "confidence": 0.85,
                },
            ],
            "summary": "Consolidated at cornerstore — costco doesn't stock bread and is far enough "
            "that the extra trip isn't worth the per-item savings.",
            "confidence": 0.85,
        }
    )
    plan = asyncio.run(LLMPlanner(backend=FakeBackend(response)).plan(_basket_belief(), _basket_goal(), None))

    assert len(plan.steps) == 3
    assert all("cornerstore" in s.description for s in plan.steps)
    assert "Consolidated at cornerstore" in plan.metadata["summary"]


def test_split_decision_flows_through_to_plan():
    """With costco close enough, a real model could instead decide
    splitting (cheaper items at costco, bread at cornerstore) beats
    consolidating — this stands in for that opposite decision."""
    response = json.dumps(
        {
            "steps": [
                {
                    "action": "buy_milk",
                    "description": "Buy milk at costco: $2.10",
                    "expected_outcome": "milk acquired",
                    "cost": 0.1,
                    "confidence": 0.9,
                },
                {
                    "action": "buy_eggs",
                    "description": "Buy eggs at costco: $2.50",
                    "expected_outcome": "eggs acquired",
                    "cost": 0.1,
                    "confidence": 0.9,
                },
                {
                    "action": "buy_bread",
                    "description": "Buy bread at cornerstore: $2.80",
                    "expected_outcome": "bread acquired",
                    "cost": 0.1,
                    "confidence": 0.9,
                },
            ],
            "summary": "Split across costco and cornerstore — cheaper overall even with two stops.",
            "confidence": 0.9,
        }
    )
    plan = asyncio.run(LLMPlanner(backend=FakeBackend(response)).plan(_basket_belief(), _basket_goal(), None))

    steps_by_action = {s.action: s.description for s in plan.steps}
    assert "costco" in steps_by_action["buy_milk"]
    assert "costco" in steps_by_action["buy_eggs"]
    assert "cornerstore" in steps_by_action["buy_bread"]


def test_unfulfillable_basket_decision_flows_through_to_plan():
    response = json.dumps(
        {
            "steps": [],
            "summary": "Bread is unavailable at every known store — basket cannot be completed.",
            "confidence": 0.0,
        }
    )
    belief = BeliefState(actor_id="alice")
    belief.add_fact(entity="costco:milk", attribute="price", value=2.10, confidence=0.9)
    belief.add_fact(entity="costco:milk", attribute="stock", value=50, confidence=0.9)

    plan = asyncio.run(LLMPlanner(backend=FakeBackend(response)).plan(belief, _basket_goal(), None))

    assert plan.steps == ()
    assert plan.confidence == 0.0
    assert "unavailable" in plan.metadata["summary"]


def test_basket_facts_serialized_without_kernel_interpreting_them():
    """The kernel must not parse "store:item" itself — it only formats
    facts as plain text for the model to interpret. Confirms no basket-
    specific logic snuck back into the prompt-building path."""
    backend = FakeBackend(json.dumps({"steps": [], "summary": "", "confidence": 0.0}))
    asyncio.run(LLMPlanner(backend=backend).plan(_basket_belief(), _basket_goal(), None))

    assert "costco:milk price=2.1" in backend.last_prompt
    assert "cornerstore:bread price=2.8" in backend.last_prompt
    assert "milk, eggs, bread" in backend.last_prompt
