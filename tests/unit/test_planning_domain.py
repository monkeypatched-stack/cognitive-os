"""Tests for the planning domain model (Step 8.1).

Validates construction, defaults, and immutability for all 8 planning domain
types. These are pure data objects — no algorithm, no mocking needed.
"""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from src.monkey_brain.kernel.pipeline.planning import (
    ValidationStatus,
    Goal,
    SubGoal,
    PlanningOperator,
    PlanningConstraint,
    PlanStep,
    Plan,
    PlanCandidate,
    PlanningContext,
)

# ═══════════════════════════════════════════════════════════════════════════
# Goal
# ═══════════════════════════════════════════════════════════════════════════


class TestGoal:
    def test_construction_minimal(self):
        goal = Goal(name="acquire_milk")
        assert goal.name == "acquire_milk"
        assert goal.description == ""
        assert goal.completion_criteria == ()
        assert goal.priority == 0.5
        assert goal.metadata == {}
        assert goal.goal_id  # auto-generated

    def test_construction_full(self):
        goal = Goal(
            name="acquire_milk",
            description="Acquire 2L whole milk",
            completion_criteria=("milk_in_cart",),
            priority=0.9,
            metadata={"category": "grocery"},
        )
        assert goal.completion_criteria == ("milk_in_cart",)
        assert goal.priority == 0.9
        assert goal.metadata == {"category": "grocery"}

    def test_goal_ids_are_unique(self):
        assert Goal().goal_id != Goal().goal_id

    def test_frozen(self):
        goal = Goal(name="x")
        with pytest.raises(FrozenInstanceError):
            goal.name = "y"


# ═══════════════════════════════════════════════════════════════════════════
# SubGoal
# ═══════════════════════════════════════════════════════════════════════════


class TestSubGoal:
    def test_construction(self):
        parent = Goal(name="prepare_breakfast")
        sub = SubGoal(
            parent_goal_id=parent.goal_id,
            name="buy_milk",
            depends_on=(),
        )
        assert sub.parent_goal_id == parent.goal_id
        assert sub.name == "buy_milk"
        assert sub.depends_on == ()

    def test_dependencies_round_trip(self):
        a = SubGoal(name="buy_flour")
        b = SubGoal(name="bake_bread", depends_on=(a.goal_id,))
        assert a.goal_id in b.depends_on

    def test_frozen(self):
        sub = SubGoal(name="x")
        with pytest.raises(FrozenInstanceError):
            sub.name = "y"


# ═══════════════════════════════════════════════════════════════════════════
# PlanningOperator
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanningOperator:
    def test_construction(self):
        op = PlanningOperator(
            name="AcquireItem",
            preconditions=("item_available",),
            effects=("item_acquired",),
            estimated_cost=0.3,
            estimated_duration=120.0,
            required_capabilities=("shopping",),
        )
        assert op.name == "AcquireItem"
        assert op.preconditions == ("item_available",)
        assert op.effects == ("item_acquired",)
        assert op.required_capabilities == ("shopping",)

    def test_defaults(self):
        op = PlanningOperator()
        assert op.preconditions == ()
        assert op.effects == ()
        assert op.estimated_cost == 0.0
        assert op.estimated_duration == 0.0

    def test_is_pure_data_no_apply_method(self):
        op = PlanningOperator(name="Navigate")
        assert not hasattr(op, "apply")
        assert not hasattr(op, "execute")

    def test_frozen(self):
        op = PlanningOperator(name="x")
        with pytest.raises(FrozenInstanceError):
            op.name = "y"


# ═══════════════════════════════════════════════════════════════════════════
# PlanningConstraint
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanningConstraint:
    def test_construction(self):
        c = PlanningConstraint(
            kind="budget",
            description="Total cost must stay under $20",
            parameters={"max_cost": 20.0},
            hard=True,
        )
        assert c.kind == "budget"
        assert c.parameters == {"max_cost": 20.0}
        assert c.hard is True
        assert c.constraint_id  # auto-generated

    def test_soft_constraint(self):
        c = PlanningConstraint(kind="policy", hard=False)
        assert c.hard is False

    def test_frozen(self):
        c = PlanningConstraint(kind="time")
        with pytest.raises(FrozenInstanceError):
            c.kind = "budget"


# ═══════════════════════════════════════════════════════════════════════════
# PlanStep
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanStep:
    def test_construction_without_operator(self):
        step = PlanStep(sequence=0, description="Walk to store")
        assert step.sequence == 0
        assert step.operator is None
        assert step.step_id  # auto-generated

    def test_construction_with_operator(self):
        op = PlanningOperator(name="Navigate")
        step = PlanStep(sequence=1, operator=op, description="Navigate to store")
        assert step.operator is op
        assert step.operator.name == "Navigate"

    def test_frozen(self):
        step = PlanStep(description="x")
        with pytest.raises(FrozenInstanceError):
            step.description = "y"


# ═══════════════════════════════════════════════════════════════════════════
# Plan
# ═══════════════════════════════════════════════════════════════════════════


class TestPlan:
    def test_construction_minimal(self):
        plan = Plan()
        assert isinstance(plan.goal, Goal)
        assert plan.steps == ()
        assert plan.validation_status == ValidationStatus.UNVALIDATED
        assert plan.constraints == ()
        assert plan.trace == ()
        assert plan.plan_id  # auto-generated

    def test_composes_goal_and_steps(self):
        goal = Goal(name="acquire_milk", description="Acquire 2L whole milk")
        steps = (
            PlanStep(sequence=0, description="Walk to store", estimated_cost=0.1),
            PlanStep(sequence=1, description="Buy milk", estimated_cost=0.2),
        )
        constraint = PlanningConstraint(kind="budget", parameters={"max_cost": 20.0})

        plan = Plan(
            goal=goal,
            steps=steps,
            estimated_cost=0.3,
            estimated_duration=900.0,
            confidence=0.85,
            constraints=(constraint,),
            validation_status=ValidationStatus.VALID,
            metadata={"scenario": "get 2 l of milk"},
            trace=("decomposed goal", "generated 2 steps"),
        )

        assert plan.goal.name == "acquire_milk"
        assert len(plan.steps) == 2
        assert plan.steps[0].sequence == 0
        assert plan.steps[1].sequence == 1
        assert plan.constraints[0].kind == "budget"
        assert plan.validation_status == ValidationStatus.VALID
        assert plan.trace == ("decomposed goal", "generated 2 steps")

    def test_frozen(self):
        plan = Plan()
        with pytest.raises(FrozenInstanceError):
            plan.confidence = 0.9


# ═══════════════════════════════════════════════════════════════════════════
# PlanCandidate
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanCandidate:
    def test_construction_unscored(self):
        plan = Plan(goal=Goal(name="acquire_milk"))
        candidate = PlanCandidate(plan=plan)
        assert candidate.plan is plan
        assert candidate.score is None
        assert candidate.rejected is False
        assert candidate.rejection_reason == ""

    def test_scored_and_rejected(self):
        plan = Plan(goal=Goal(name="acquire_milk"))
        candidate = PlanCandidate(plan=plan, score=0.55, rejected=True, rejection_reason="too slow")
        assert candidate.score == 0.55
        assert candidate.rejected is True
        assert candidate.rejection_reason == "too slow"

    def test_frozen(self):
        candidate = PlanCandidate()
        with pytest.raises(FrozenInstanceError):
            candidate.score = 0.9


# ═══════════════════════════════════════════════════════════════════════════
# PlanningContext
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanningContext:
    def test_composes_operators_and_constraints(self):
        goal = Goal(name="acquire_milk")
        sub = SubGoal(parent_goal_id=goal.goal_id, name="walk_to_store")
        operator = PlanningOperator(name="AcquireItem")
        constraint = PlanningConstraint(kind="budget")

        ctx = PlanningContext(
            goal=goal,
            subgoals=(sub,),
            available_operators=(operator,),
            constraints=(constraint,),
        )

        assert ctx.goal is goal
        assert ctx.subgoals[0].name == "walk_to_store"
        assert ctx.available_operators[0].name == "AcquireItem"
        assert ctx.constraints[0].kind == "budget"

    def test_defaults(self):
        ctx = PlanningContext()
        assert isinstance(ctx.goal, Goal)
        assert ctx.subgoals == ()
        assert ctx.available_operators == ()
        assert ctx.constraints == ()

    def test_frozen(self):
        ctx = PlanningContext()
        with pytest.raises(FrozenInstanceError):
            ctx.goal = Goal(name="y")


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary — model-only, no coupling to runtime/execution
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_runtime_or_execution_imports(self):
        """domain.py must not import CognitiveRuntime or ExecutionEngine — Step 8.1
        is model-only; those stay untouched until Step 8.7."""
        import inspect
        import src.monkey_brain.kernel.pipeline.planning.domain as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "action_executor",
            "from src.monkey_brain.kernel.pipeline.execution import",
        ]
        for imp in forbidden:
            assert imp not in source, f"domain.py must not import: {imp}"
