"""Tests for planning engine integration (Step 8.7).

Validates IntegratedPlanningEngine: PlanningEngine Protocol conformance,
the registered-strategy pipeline (decompose -> generate -> validate ->
score -> select -> convert), the LLMPlanner fallback (both "no
strategy registered" and "all candidates rejected" cases), and end-to-end
injection into the real, unmodified CognitiveRuntime.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.monkey_brain.kernel.pipeline.contracts import (
    PipelineRequest,
    CompiledRequest,
    RuntimeContext,
)
from src.monkey_brain.kernel.pipeline.belief_state import BeliefState, Goal, Plan
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime
from src.monkey_brain.kernel.pipeline.planning.integration import (
    IntegratedPlanningEngine,
)
from src.monkey_brain.kernel.pipeline.planning.domain import PlanningConstraint

# ═══════════════════════════════════════════════════════════════════════════
# Helpers — matching test_pipeline_planning.py's existing fixtures
# ═══════════════════════════════════════════════════════════════════════════


def _make_compiled(question: str = "get 2 l of milk", goal_name: str = "acquire_milk") -> CompiledRequest:
    request = PipelineRequest(question=question, actor_id="user-1", tenant_id="acme")
    ctx = MagicMock()
    ctx.run_id = "run-001"
    return CompiledRequest(
        request=request,
        intent={"intent": "shopping", "confidence": 0.9},
        goal={"name": goal_name, "description": "Get milk"},
        intent_ir=MagicMock(),
        goal_ir=MagicMock(goal=question),
        execution_context=ctx,
    )


def _make_context() -> RuntimeContext:
    world = MagicMock()
    world.states.return_value = ["start", "middle", "end"]
    world.domains.return_value = ["task"]
    world.nnz.return_value = 5
    return RuntimeContext(world=world, actor=MagicMock())


# ═══════════════════════════════════════════════════════════════════════════
# Protocol conformance — "the runtime API must remain unchanged"
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanningEngineProtocolConformance:
    def test_satisfies_planning_engine_protocol(self):
        """planner.py's PlanningEngine Protocol is not @runtime_checkable
        (and that file must not be modified by this step), so conformance is
        verified the same way the rest of this codebase already does it —
        structurally, not via isinstance() — matching how
        test_pipeline_planning.py's own MockPlanner is checked."""
        engine = IntegratedPlanningEngine()
        assert hasattr(engine, "plan")
        assert callable(engine.plan)

    def test_plan_returns_belief_state_plan(self):
        engine = IntegratedPlanningEngine()
        belief = BeliefState(actor_id="alice")
        plan = engine.plan(belief, Goal(name="acquire_milk"), None)
        assert isinstance(plan, Plan)

    def test_context_argument_is_optional(self):
        """Matches PlanningEngine.plan(belief, goal, context: Any = None)."""
        engine = IntegratedPlanningEngine()
        belief = BeliefState(actor_id="alice")
        plan = engine.plan(belief, Goal(name="acquire_milk"))  # no context
        assert isinstance(plan, Plan)


# ═══════════════════════════════════════════════════════════════════════════
# Registered-strategy pipeline: Decompose -> Generate -> Validate -> Score -> Select
# ═══════════════════════════════════════════════════════════════════════════


class TestRegisteredStrategyPipeline:
    def test_produces_a_plan_from_the_winning_candidate(self):
        engine = IntegratedPlanningEngine()
        belief = BeliefState(actor_id="alice")
        plan = engine.plan(belief, Goal(name="acquire_milk", description="Acquire 2L whole milk"))

        assert plan.planner == "integrated"
        assert plan.goal == "acquire_milk"
        assert len(plan.steps) == 2

    def test_steps_carry_operator_derived_fields(self):
        engine = IntegratedPlanningEngine()
        belief = BeliefState(actor_id="alice")
        plan = engine.plan(belief, Goal(name="acquire_milk"))

        assert plan.steps[0].action == "Navigate"
        assert plan.steps[1].action == "AcquireItem"
        assert all(step.confidence >= 0.0 for step in plan.steps)
        assert all(step.cost >= 0.0 for step in plan.steps)

    def test_confidence_and_risk_derived_from_candidate_score(self):
        engine = IntegratedPlanningEngine()
        belief = BeliefState(actor_id="alice")
        plan = engine.plan(belief, Goal(name="acquire_milk"))

        assert plan.confidence == plan.metadata["candidate_score"]
        assert plan.risk == pytest.approx(1.0 - plan.confidence, abs=1e-4)

    def test_metadata_records_winning_strategy_and_validation(self):
        engine = IntegratedPlanningEngine()
        belief = BeliefState(actor_id="alice")
        plan = engine.plan(belief, Goal(name="acquire_milk"))

        assert plan.metadata["strategy"] in {
            "walk_to_store",
            "drive_to_store",
            "delivery_service",
        }
        assert plan.metadata["validation_status"] == "valid"


# ═══════════════════════════════════════════════════════════════════════════
# Fallback — "compatibility with LLMPlanner"
# ═══════════════════════════════════════════════════════════════════════════


class TestFallback:
    def test_falls_back_when_no_strategy_registered(self):
        engine = IntegratedPlanningEngine()
        belief = BeliefState(actor_id="alice")
        plan = engine.plan(belief, Goal(name="totally_unregistered_goal"))

        assert plan.planner == "integrated:fallback"
        assert plan.metadata["fallback_reason"] == "no_registered_strategy"

    def test_fallback_matches_calling_llm_planner_directly(self):
        """Same belief/goal/context through a real LLMPlanner (with a
        fixed backend) must produce an equivalent plan via the fallback
        path — proves the fallback is a genuine, unmodified LLMPlanner,
        not a special-cased copy of its logic."""
        import json

        class _FakeBackend:
            def complete(self, prompt, system="", max_tokens=None, **kwargs):
                return json.dumps(
                    {
                        "steps": [
                            {
                                "action": "a",
                                "description": "d",
                                "expected_outcome": "o",
                                "cost": 0.2,
                                "confidence": 0.7,
                            }
                        ],
                        "summary": "s",
                        "confidence": 0.7,
                    }
                )

        belief = BeliefState(actor_id="alice")
        goal = Goal(name="totally_unregistered_goal", description="x")
        backend = _FakeBackend()

        direct = asyncio.run(LLMPlanner(backend=backend).plan(belief, goal, None))
        via_fallback = IntegratedPlanningEngine(fallback=LLMPlanner(backend=backend)).plan(belief, goal, None)

        assert via_fallback.confidence == direct.confidence
        assert via_fallback.cost == direct.cost
        assert len(via_fallback.steps) == len(direct.steps)

    def test_falls_back_when_all_candidates_rejected_by_constraints(self):
        engine = IntegratedPlanningEngine(
            default_constraints=(PlanningConstraint(kind="budget", parameters={"max_cost": 0.001}, hard=True),)
        )
        belief = BeliefState(actor_id="alice")
        plan = engine.plan(belief, Goal(name="acquire_milk"))

        assert plan.planner == "integrated:fallback"
        assert plan.metadata["fallback_reason"] == "all_candidates_rejected"

    def test_custom_fallback_engine_is_used(self):
        class StubPlanner:
            def plan(self, belief, goal, context=None):
                return Plan(goal="stub", planner="stub")

        engine = IntegratedPlanningEngine(fallback=StubPlanner())
        belief = BeliefState(actor_id="alice")
        plan = engine.plan(belief, Goal(name="unregistered"))

        assert plan.goal == "stub"
        assert plan.planner == "integrated:fallback"


# ═══════════════════════════════════════════════════════════════════════════
# Constraint-driven candidate rejection actually selects among the rest
# ═══════════════════════════════════════════════════════════════════════════


class TestConstraintDrivenSelection:
    def test_tight_budget_eliminates_expensive_strategies(self):
        """Only walk_to_store (cost 0.25) fits under a 0.3 budget; drive
        (0.35) and delivery (0.55) must be rejected, not silently allowed."""
        engine = IntegratedPlanningEngine(
            default_constraints=(PlanningConstraint(kind="budget", parameters={"max_cost": 0.3}, hard=True),)
        )
        belief = BeliefState(actor_id="alice")
        plan = engine.plan(belief, Goal(name="acquire_milk"))

        assert plan.planner == "integrated"  # not a fallback — one candidate survived
        assert plan.metadata["strategy"] == "walk_to_store"


# ═══════════════════════════════════════════════════════════════════════════
# End-to-end: real, unmodified CognitiveRuntime
# ═══════════════════════════════════════════════════════════════════════════


class TestEndToEndWithCognitiveRuntime:
    @pytest.mark.asyncio
    async def test_injected_into_real_cognitive_runtime(self):
        """IntegratedPlanningEngine is injected like any other PlanningEngine
        — no changes to CognitiveRuntime itself."""
        runtime = CognitiveRuntime(planning_engine=IntegratedPlanningEngine())

        result = await runtime.run(_make_compiled(goal_name="acquire_milk"), _make_context())

        assert result["status"] in ("success", "partial")
        assert result["plan"]["goal"] == "acquire_milk"
        assert len(result["plan"]["steps"]) == 2

    @pytest.mark.asyncio
    async def test_unregistered_goal_still_completes_via_fallback(self):
        runtime = CognitiveRuntime(planning_engine=IntegratedPlanningEngine())

        result = await runtime.run(_make_compiled(goal_name="some_other_goal"), _make_context())

        assert result["status"] in ("success", "partial")

    @pytest.mark.asyncio
    async def test_default_llm_planner_still_works_unchanged(self):
        """Sanity check: not injecting IntegratedPlanningEngine at all — the
        existing default behavior (bare construction, relying on the
        conftest fixture's fake backend) must still produce a valid plan."""
        runtime = CognitiveRuntime()  # default LLMPlanner

        result = await runtime.run(_make_compiled(goal_name="acquire_milk"), _make_context())

        assert result["status"] in ("success", "partial")
        assert result["plan"]["goal"] == "acquire_milk"
