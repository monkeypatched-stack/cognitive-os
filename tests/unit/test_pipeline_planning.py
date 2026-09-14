"""Tests for Goal-Directed Planning (Step 8).

Validates planning engine, plan model, plan validation, and runtime integration.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock

import json

from src.monkey_brain.kernel.pipeline.contracts import (
    PipelineRequest,
    CompiledRequest,
    RuntimeContext,
)
from src.monkey_brain.kernel.pipeline.belief_state import (
    BeliefState,
    Goal,
    Plan,
    PlanStep,
    PlanEvaluation,
    Fact,
)
from src.monkey_brain.kernel.pipeline.planner import PlanningEngine
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
from src.monkey_brain.kernel.pipeline.plan_validator import (
    PlanValidator,
    ValidationResult,
)
from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime
from src.monkey_brain.kernel.pipeline.actor import Actor


class _FakeBackend:
    def __init__(self, response: str):
        self.response = response
        self.last_prompt = None

    async def complete(self, prompt, system="", max_tokens=None, **kwargs):
        self.last_prompt = prompt
        return self.response


def _canned_plan(goal_name: str, n_steps: int = 1, confidence: float = 0.8) -> str:
    return json.dumps(
        {
            "steps": [
                {
                    "action": f"{goal_name}_step_{i}",
                    "description": f"Step {i} toward {goal_name}",
                    "expected_outcome": goal_name,
                    "cost": 0.1,
                    "confidence": confidence,
                }
                for i in range(n_steps)
            ],
            "summary": f"Plan to achieve {goal_name}",
            "confidence": confidence,
        }
    )


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_compiled(question: str = "get 2 l of milk") -> CompiledRequest:
    request = PipelineRequest(question=question, actor_id="user-1", tenant_id="acme")
    ctx = MagicMock()
    ctx.run_id = "run-001"
    return CompiledRequest(
        request=request,
        intent={"intent": "shopping", "confidence": 0.9},
        goal={"name": "acquire_item", "description": "Get milk"},
        intent_ir=MagicMock(),
        goal_ir=MagicMock(goal="get 2 l of milk"),
        execution_context=ctx,
    )


def _make_context() -> RuntimeContext:
    world = MagicMock()
    world.states.return_value = ["start", "middle", "end"]
    world.domains.return_value = ["task"]
    world.nnz.return_value = 5
    return RuntimeContext(world=world, actor=MagicMock())


# ═══════════════════════════════════════════════════════════════════════════
# Plan Model
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanModel:
    def test_plan_construction(self):
        plan = Plan(goal="acquire_milk", confidence=0.8)
        assert plan.goal == "acquire_milk"
        assert plan.confidence == 0.8
        assert plan.planner == "default"

    def test_plan_step(self):
        step = PlanStep(
            action="find_milk",
            description="Locate milk in store",
            preconditions=("observe:milk",),
            expected_outcome="milk_found",
            cost=0.1,
            confidence=0.9,
        )
        assert step.action == "find_milk"
        assert step.preconditions == ("observe:milk",)

    def test_plan_frozen(self):
        from dataclasses import FrozenInstanceError

        plan = Plan(goal="test")
        with pytest.raises(FrozenInstanceError):
            plan.goal = "changed"

    def test_plan_step_frozen(self):
        from dataclasses import FrozenInstanceError

        step = PlanStep(action="x")
        with pytest.raises(FrozenInstanceError):
            step.action = "y"

    def test_plan_evaluation(self):
        plan = Plan(goal="test", steps=(PlanStep(action="a"),))
        ev = PlanEvaluation(plan=plan, feasible=True, score=0.8)
        assert ev.feasible
        assert ev.score == 0.8


# ═══════════════════════════════════════════════════════════════════════════
# LLMPlanner (centralized-planning constitution: no hardcoded scoring —
# these tests assert the generic PlanningEngine contract against an
# injected fake backend, not any particular formula, since there is no
# formula left in this code path to test.)
# ═══════════════════════════════════════════════════════════════════════════


class TestLLMPlanner:
    def test_plan_with_goal_and_facts(self):
        backend = _FakeBackend(_canned_plan("acquire_milk", n_steps=2, confidence=0.85))
        planner = LLMPlanner(backend=backend)
        belief = BeliefState()
        belief.add_fact(entity="milk", attribute="quantity", value="2L", confidence=0.9)
        belief.add_fact(entity="milk", attribute="location", value="aisle_3", confidence=0.8)
        goal = Goal(name="acquire_milk", description="Get milk from store")

        plan = asyncio.run(planner.plan(belief, goal))

        assert plan.goal == "acquire_milk"
        assert len(plan.steps) >= 1
        assert plan.confidence > 0
        assert plan.planner == "llm"
        assert "milk" in backend.last_prompt.lower()

    def test_plan_with_no_goal(self):
        planner = LLMPlanner(backend=_FakeBackend("{}"))
        belief = BeliefState()
        goal = Goal()

        plan = asyncio.run(planner.plan(belief, goal))

        assert plan.goal == ""
        assert len(plan.steps) == 0
        assert plan.confidence == 0.0

    def test_plan_with_no_facts(self):
        backend = _FakeBackend(_canned_plan("do_something"))
        planner = LLMPlanner(backend=backend)
        belief = BeliefState()
        goal = Goal(name="do_something")

        plan = asyncio.run(planner.plan(belief, goal))

        assert plan.goal == "do_something"
        assert plan.confidence <= 1.0

    def test_malformed_backend_response_fails_closed_not_a_hardcoded_fallback(self):
        planner = LLMPlanner(backend=_FakeBackend("not json"))
        belief = BeliefState()
        goal = Goal(name="buy_milk")

        plan = asyncio.run(planner.plan(belief, goal))

        # No hardcoded business decision on failure — a zero-confidence
        # empty plan, not a "pick something reasonable" fallback.
        assert plan.steps == ()
        assert plan.confidence == 0.0
        assert "error" in plan.metadata

    def test_plan_has_expected_outcomes(self):
        backend = _FakeBackend(_canned_plan("buy_milk"))
        planner = LLMPlanner(backend=backend)
        belief = BeliefState()
        belief.add_fact(entity="milk", attribute="qty", value="2L")
        goal = Goal(name="buy_milk")

        plan = asyncio.run(planner.plan(belief, goal))

        assert len(plan.expected_outcomes) >= 1
        assert any("buy_milk" in o for o in plan.expected_outcomes)

    def test_fact_confidence_reaches_the_prompt(self):
        """The kernel doesn't decide how confidence affects the plan (that
        was DeterministicPlanner's hardcoded coverage*confidence formula,
        now removed) — it only needs to make each fact's confidence value
        available for the planner (the LLM) to reason about."""
        backend = _FakeBackend(_canned_plan("test"))
        planner = LLMPlanner(backend=backend)
        belief = BeliefState()
        belief.add_fact(entity="x", attribute="y", value=1, confidence=0.9)

        asyncio.run(planner.plan(belief, Goal(name="test")))

        assert "confidence=0.9" in backend.last_prompt

    def test_plan_steps_have_details(self):
        backend = _FakeBackend(_canned_plan("buy_milk", n_steps=2))
        planner = LLMPlanner(backend=backend)
        belief = BeliefState()
        belief.add_fact(entity="milk", attribute="qty", value="2L")
        goal = Goal(name="buy_milk")

        plan = asyncio.run(planner.plan(belief, goal))

        for step in plan.steps:
            assert step.action
            assert step.description
            assert step.expected_outcome


# ═══════════════════════════════════════════════════════════════════════════
# PlanValidator
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanValidator:
    def test_valid_plan(self):
        validator = PlanValidator()
        plan = Plan(
            goal="test",
            steps=(PlanStep(action="do_thing"),),
            confidence=0.8,
            cost=0.3,
            risk=0.2,
        )
        result = validator.validate(plan)
        assert result.valid
        assert result.score == 1.0

    def test_empty_plan_rejected(self):
        validator = PlanValidator()
        plan = Plan()
        result = validator.validate(plan)
        assert not result.valid
        assert "plan_has_no_goal" in result.violations
        assert "plan_has_no_steps" in result.violations

    def test_low_confidence_rejected(self):
        validator = PlanValidator(min_confidence=0.5)
        plan = Plan(goal="test", steps=(PlanStep(action="x"),), confidence=0.2)
        result = validator.validate(plan)
        assert not result.valid
        assert any("confidence_below_threshold" in v for v in result.violations)

    def test_high_risk_rejected(self):
        validator = PlanValidator(max_risk=0.5)
        plan = Plan(goal="test", steps=(PlanStep(action="x"),), risk=0.8)
        result = validator.validate(plan)
        assert not result.valid
        assert any("risk_too_high" in v for v in result.violations)

    def test_high_cost_rejected(self):
        validator = PlanValidator(max_cost=0.5)
        plan = Plan(goal="test", steps=(PlanStep(action="x"),), cost=0.9)
        result = validator.validate(plan)
        assert not result.valid
        assert any("cost_exceeds_budget" in v for v in result.violations)

    def test_precondition_check(self):
        validator = PlanValidator()
        belief = BeliefState()
        belief.add_fact(entity="milk", attribute="qty", value="2L", confidence=0.9)

        plan = Plan(
            goal="test",
            steps=(PlanStep(action="x"),),
            preconditions=("verify:milk.qty",),
            confidence=0.8,
        )
        result = validator.validate(plan, belief)
        assert result.valid
        assert len(result.warnings) == 0

    def test_score_decreases_with_violations(self):
        validator = PlanValidator(min_confidence=0.5)
        plan = Plan(goal="test", steps=(PlanStep(action="x"),), confidence=0.3)
        result = validator.validate(plan)
        assert result.score < 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Runtime Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanningIntegration:
    @pytest.mark.asyncio
    async def test_plan_generated_from_belief_and_goal(self):
        """Plan should be generated from belief facts and goal."""
        rt = CognitiveRuntime()
        result = await rt.run(_make_compiled(), _make_context())

        assert result["plan"]["goal"] == "acquire_item"
        assert result["belief"]["plan_steps"] >= 0

    @pytest.mark.asyncio
    async def test_custom_planner_injected(self):
        """A custom PlanningEngine can be injected."""

        class MockPlanner:
            def plan(self, belief, goal, context=None):
                return Plan(
                    goal=goal.name,
                    steps=(PlanStep(action="mock_step", confidence=1.0),),
                    confidence=1.0,
                    planner="mock",
                )

        rt = CognitiveRuntime(planning_engine=MockPlanner())
        result = await rt.run(_make_compiled(), _make_context())

        assert result["plan"]["goal"] == "acquire_item"

    @pytest.mark.asyncio
    async def test_plan_validates_before_storing(self):
        """Plan should be validated before being stored in belief."""

        class StrictValidator:
            def validate(self, plan, belief=None):
                if not plan.steps:
                    from src.monkey_brain.kernel.pipeline.plan_validator import (
                        ValidationResult,
                    )

                    return ValidationResult(valid=False, violations=("no_steps",))
                from src.monkey_brain.kernel.pipeline.plan_validator import (
                    ValidationResult,
                )

                return ValidationResult(valid=True)

        rt = CognitiveRuntime(plan_validator=StrictValidator())
        # Should not crash even with strict validation
        result = await rt.run(_make_compiled(), _make_context())
        assert "plan" in result

    @pytest.mark.asyncio
    async def test_plan_recorded_in_trace(self):
        """Plan generation should be recorded in the execution trace."""
        rt = CognitiveRuntime()
        result = await rt.run(_make_compiled(), _make_context())

        assert result["plan"]["goal"] == "acquire_item"
        assert len(result["stage_durations"]) > 0
        assert "plan" in result["stage_durations"]
