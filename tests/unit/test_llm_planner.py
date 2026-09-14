"""Tests for LLMPlanner (kernel/pipeline/llm_planner.py) — the centralized,
domain-agnostic planner that asks an LLM to decide plans instead of
hardcoding scoring rules. A fake backend (matching ModelBackend.complete's
synchronous signature, kernel/execute/provider/model_backend.py) is
injected so tests run without network/API keys, mirroring this codebase's
established LLM test pattern (test_llm_query_classification.py).
"""

from __future__ import annotations

import asyncio
import json

from src.monkey_brain.kernel.pipeline.belief_state import BeliefState, Goal
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
from src.monkey_brain.kernel.pipeline.planning.domain import PlanningContext


class FakeBackend:
    def __init__(self, response: str):
        self.response = response
        self.last_prompt = None
        self.last_system = None

    async def complete(self, prompt, system="", max_tokens=None, **kwargs):
        self.last_prompt = prompt
        self.last_system = system
        return self.response


def _goal():
    return Goal(
        name="acquire_milk",
        description="buy 1 liter of milk",
        success_criteria=("milk",),
    )


def test_successful_response_parses_into_plan_and_steps():
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
            ],
            "summary": "Buy milk at costco, cheapest and in stock",
            "confidence": 0.9,
        }
    )
    backend = FakeBackend(response)
    belief = BeliefState(actor_id="alice")
    belief.add_fact(entity="costco", attribute="price", value=2.10, confidence=0.9)

    plan = asyncio.run(LLMPlanner(backend=backend).plan(belief, _goal(), None))

    assert plan.planner == "llm"
    assert plan.confidence == 0.9
    assert len(plan.steps) == 1
    assert plan.steps[0].action == "buy_milk"
    assert "costco" in plan.steps[0].description
    assert plan.metadata["summary"] == "Buy milk at costco, cheapest and in stock"


def test_malformed_response_returns_zero_confidence_plan_not_exception():
    backend = FakeBackend("this is not json at all")
    belief = BeliefState(actor_id="alice")
    belief.add_fact(entity="costco", attribute="price", value=2.10, confidence=0.9)

    plan = asyncio.run(LLMPlanner(backend=backend).plan(belief, _goal(), None))

    assert plan.confidence == 0.0
    assert plan.steps == ()
    assert "error" in plan.metadata


def test_backend_exception_returns_zero_confidence_plan_not_exception():
    class RaisingBackend:
        async def complete(self, prompt, system="", max_tokens=None, **kwargs):
            raise ConnectionError("network unavailable")

    belief = BeliefState(actor_id="alice")
    plan = asyncio.run(LLMPlanner(backend=RaisingBackend()).plan(belief, _goal(), None))

    assert plan.confidence == 0.0
    assert "network unavailable" in plan.metadata["error"]


def test_empty_goal_returns_empty_plan_without_calling_backend():
    backend = FakeBackend("{}")
    plan = asyncio.run(LLMPlanner(backend=backend).plan(BeliefState(actor_id="alice"), Goal(name=""), None))

    assert plan.goal == ""
    assert plan.confidence == 0.0
    assert backend.last_prompt is None


def test_dual_shape_accepts_planning_context_directly():
    response = json.dumps({"steps": [], "summary": "no steps needed", "confidence": 0.5})
    backend = FakeBackend(response)
    belief = BeliefState(actor_id="alice")
    ctx = PlanningContext.from_legacy(belief, _goal(), None)

    plan = asyncio.run(LLMPlanner(backend=backend).plan(ctx))

    assert plan.goal == "acquire_milk"
    assert plan.confidence == 0.5


def test_prompt_includes_goal_and_facts_as_plain_text():
    """The kernel doesn't interpret facts — it just serializes them. No
    domain-specific parsing (e.g. "store:item" keys) should appear here;
    that reasoning belongs entirely to the LLM."""
    backend = FakeBackend(json.dumps({"steps": [], "summary": "", "confidence": 0.0}))
    belief = BeliefState(actor_id="alice")
    belief.add_fact(entity="costco:milk", attribute="price", value=2.10, confidence=0.9)

    asyncio.run(LLMPlanner(backend=backend).plan(belief, _goal(), None))

    assert "acquire_milk" in backend.last_prompt
    assert "costco:milk" in backend.last_prompt
    assert "price=2.1" in backend.last_prompt
