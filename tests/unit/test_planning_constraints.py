"""Tests for the constraint engine (Step 8.4).

Validates each of the 7 named constraint evaluators individually, the
ConstraintEngine's aggregation (hard vs. soft, unrecognized-kind handling),
and validated_plan()'s immutability-preserving status update.
"""

from __future__ import annotations

from src.monkey_brain.kernel.pipeline.planning.domain import (
    Goal,
    Plan,
    PlanStep,
    PlanningConstraint,
    PlanningOperator,
    ValidationStatus,
)
from src.monkey_brain.kernel.pipeline.planning.constraints import (
    ConstraintCheckResult,
    ValidationReport,
    ConstraintEvaluator,
    BudgetConstraintEvaluator,
    TimeConstraintEvaluator,
    PolicyConstraintEvaluator,
    CapabilityConstraintEvaluator,
    InventoryConstraintEvaluator,
    SafetyConstraintEvaluator,
    PermissionsConstraintEvaluator,
    DEFAULT_EVALUATORS,
    ConstraintEngine,
)


def _plan_with_operator(op: PlanningOperator, **plan_kwargs) -> Plan:
    step = PlanStep(sequence=0, operator=op)
    return Plan(goal=Goal(name="x"), steps=(step,), **plan_kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# Budget
# ═══════════════════════════════════════════════════════════════════════════


class TestBudgetConstraintEvaluator:
    def test_within_budget(self):
        plan = Plan(goal=Goal(name="x"), estimated_cost=0.3)
        constraint = PlanningConstraint(kind="budget", parameters={"max_cost": 1.0})
        result = BudgetConstraintEvaluator().evaluate(constraint, plan)
        assert result.satisfied is True

    def test_over_budget(self):
        plan = Plan(goal=Goal(name="x"), estimated_cost=2.0)
        constraint = PlanningConstraint(kind="budget", parameters={"max_cost": 1.0})
        result = BudgetConstraintEvaluator().evaluate(constraint, plan)
        assert result.satisfied is False
        assert "exceeds budget" in result.message

    def test_missing_max_cost_parameter_fails(self):
        plan = Plan(goal=Goal(name="x"))
        constraint = PlanningConstraint(kind="budget", parameters={})
        result = BudgetConstraintEvaluator().evaluate(constraint, plan)
        assert result.satisfied is False


# ═══════════════════════════════════════════════════════════════════════════
# Time
# ═══════════════════════════════════════════════════════════════════════════


class TestTimeConstraintEvaluator:
    def test_within_time_limit(self):
        plan = Plan(goal=Goal(name="x"), estimated_duration=100.0)
        constraint = PlanningConstraint(kind="time", parameters={"max_duration": 600.0})
        assert TimeConstraintEvaluator().evaluate(constraint, plan).satisfied is True

    def test_over_time_limit(self):
        plan = Plan(goal=Goal(name="x"), estimated_duration=900.0)
        constraint = PlanningConstraint(kind="time", parameters={"max_duration": 600.0})
        result = TimeConstraintEvaluator().evaluate(constraint, plan)
        assert result.satisfied is False
        assert "exceeds limit" in result.message


# ═══════════════════════════════════════════════════════════════════════════
# Policy
# ═══════════════════════════════════════════════════════════════════════════


class TestPolicyConstraintEvaluator:
    def test_no_denied_operators_used(self):
        plan = _plan_with_operator(PlanningOperator(name="Navigate"))
        constraint = PlanningConstraint(kind="policy", parameters={"denied_operators": ["ForbiddenOp"]})
        assert PolicyConstraintEvaluator().evaluate(constraint, plan).satisfied is True

    def test_denied_operator_used(self):
        plan = _plan_with_operator(PlanningOperator(name="ForbiddenOp"))
        constraint = PlanningConstraint(kind="policy", parameters={"denied_operators": ["ForbiddenOp"]})
        result = PolicyConstraintEvaluator().evaluate(constraint, plan)
        assert result.satisfied is False
        assert "ForbiddenOp" in result.message


# ═══════════════════════════════════════════════════════════════════════════
# Capability
# ═══════════════════════════════════════════════════════════════════════════


class TestCapabilityConstraintEvaluator:
    def test_all_capabilities_available(self):
        op = PlanningOperator(name="Navigate", required_capabilities=("navigation",))
        plan = _plan_with_operator(op)
        constraint = PlanningConstraint(kind="capability", parameters={"available_capabilities": ["navigation"]})
        assert CapabilityConstraintEvaluator().evaluate(constraint, plan).satisfied is True

    def test_missing_capability(self):
        op = PlanningOperator(name="AcquireItem", required_capabilities=("shopping", "payment"))
        plan = _plan_with_operator(op)
        constraint = PlanningConstraint(kind="capability", parameters={"available_capabilities": ["shopping"]})
        result = CapabilityConstraintEvaluator().evaluate(constraint, plan)
        assert result.satisfied is False
        assert "payment" in result.message


# ═══════════════════════════════════════════════════════════════════════════
# Inventory
# ═══════════════════════════════════════════════════════════════════════════


class TestInventoryConstraintEvaluator:
    def test_sufficient_inventory(self):
        op = PlanningOperator(name="AcquireItem", metadata={"item": "milk", "quantity": 2})
        plan = _plan_with_operator(op)
        constraint = PlanningConstraint(kind="inventory", parameters={"available_inventory": {"milk": 5}})
        assert InventoryConstraintEvaluator().evaluate(constraint, plan).satisfied is True

    def test_insufficient_inventory(self):
        op = PlanningOperator(name="AcquireItem", metadata={"item": "milk", "quantity": 2})
        plan = _plan_with_operator(op)
        constraint = PlanningConstraint(kind="inventory", parameters={"available_inventory": {"milk": 1}})
        result = InventoryConstraintEvaluator().evaluate(constraint, plan)
        assert result.satisfied is False
        assert "milk" in result.message

    def test_ignores_steps_without_item_metadata(self):
        op = PlanningOperator(name="Navigate")  # no "item" in metadata
        plan = _plan_with_operator(op)
        constraint = PlanningConstraint(kind="inventory", parameters={"available_inventory": {}})
        assert InventoryConstraintEvaluator().evaluate(constraint, plan).satisfied is True


# ═══════════════════════════════════════════════════════════════════════════
# Safety
# ═══════════════════════════════════════════════════════════════════════════


class TestSafetyConstraintEvaluator:
    def test_no_forbidden_effects(self):
        op = PlanningOperator(name="AcquireItem", effects=("milk_acquired",))
        plan = _plan_with_operator(op)
        constraint = PlanningConstraint(kind="safety", parameters={"forbidden_effects": ["explosion"]})
        assert SafetyConstraintEvaluator().evaluate(constraint, plan).satisfied is True

    def test_forbidden_effect_triggered(self):
        op = PlanningOperator(name="Dangerous", effects=("explosion",))
        plan = _plan_with_operator(op)
        constraint = PlanningConstraint(kind="safety", parameters={"forbidden_effects": ["explosion"]})
        result = SafetyConstraintEvaluator().evaluate(constraint, plan)
        assert result.satisfied is False
        assert "explosion" in result.message


# ═══════════════════════════════════════════════════════════════════════════
# Permissions
# ═══════════════════════════════════════════════════════════════════════════


class TestPermissionsConstraintEvaluator:
    def test_required_permissions_granted(self):
        plan = Plan(goal=Goal(name="x"))
        constraint = PlanningConstraint(
            kind="permissions",
            parameters={
                "required_permissions": ["shop"],
                "granted_permissions": ["shop", "admin"],
            },
        )
        assert PermissionsConstraintEvaluator().evaluate(constraint, plan).satisfied is True

    def test_missing_permission(self):
        plan = Plan(goal=Goal(name="x"))
        constraint = PlanningConstraint(
            kind="permissions",
            parameters={
                "required_permissions": ["shop", "admin"],
                "granted_permissions": ["shop"],
            },
        )
        result = PermissionsConstraintEvaluator().evaluate(constraint, plan)
        assert result.satisfied is False
        assert "admin" in result.message


# ═══════════════════════════════════════════════════════════════════════════
# ConstraintEngine — aggregation
# ═══════════════════════════════════════════════════════════════════════════


class TestConstraintEngine:
    def test_default_evaluators_cover_all_seven_kinds(self):
        expected = {
            "budget",
            "time",
            "policy",
            "capability",
            "inventory",
            "safety",
            "permissions",
        }
        assert set(DEFAULT_EVALUATORS.keys()) == expected

    def test_valid_plan_no_violations(self):
        plan = Plan(
            goal=Goal(name="x"),
            estimated_cost=0.1,
            constraints=(PlanningConstraint(kind="budget", parameters={"max_cost": 1.0}, hard=True),),
        )
        report = ConstraintEngine().validate(plan)
        assert report.valid is True
        assert report.violations == ()

    def test_hard_failure_makes_plan_invalid(self):
        plan = Plan(
            goal=Goal(name="x"),
            estimated_cost=5.0,
            constraints=(PlanningConstraint(kind="budget", parameters={"max_cost": 1.0}, hard=True),),
        )
        report = ConstraintEngine().validate(plan)
        assert report.valid is False
        assert len(report.violations) == 1

    def test_soft_failure_is_a_warning_not_a_violation(self):
        plan = Plan(
            goal=Goal(name="x"),
            estimated_cost=5.0,
            constraints=(PlanningConstraint(kind="budget", parameters={"max_cost": 1.0}, hard=False),),
        )
        report = ConstraintEngine().validate(plan)
        assert report.valid is True  # soft failure doesn't invalidate
        assert report.violations == ()
        assert len(report.warnings) == 1

    def test_mixed_hard_and_soft_failures(self):
        plan = Plan(
            goal=Goal(name="x"),
            estimated_cost=5.0,
            estimated_duration=900.0,
            constraints=(
                PlanningConstraint(kind="budget", parameters={"max_cost": 1.0}, hard=False),
                PlanningConstraint(kind="time", parameters={"max_duration": 600.0}, hard=True),
            ),
        )
        report = ConstraintEngine().validate(plan)
        assert report.valid is False
        assert len(report.violations) == 1
        assert len(report.warnings) == 1

    def test_unrecognized_kind_is_treated_as_failed(self):
        plan = Plan(
            goal=Goal(name="x"),
            constraints=(PlanningConstraint(kind="nonexistent_kind", hard=True),),
        )
        report = ConstraintEngine().validate(plan)
        assert report.valid is False
        assert "no evaluator registered" in report.violations[0]

    def test_no_constraints_is_vacuously_valid(self):
        plan = Plan(goal=Goal(name="x"))
        report = ConstraintEngine().validate(plan)
        assert report.valid is True
        assert report.results == ()

    def test_custom_evaluator_registration(self):
        class AlwaysFails:
            kind = "custom"

            def evaluate(self, constraint, plan):
                return ConstraintCheckResult(kind="custom", hard=constraint.hard, satisfied=False, message="nope")

        engine = ConstraintEngine(evaluators={})
        engine.register_evaluator(AlwaysFails())
        plan = Plan(
            goal=Goal(name="x"),
            constraints=(PlanningConstraint(kind="custom", hard=True),),
        )

        report = engine.validate(plan)
        assert report.valid is False
        assert report.violations == ("nope",)

    def test_satisfies_constraint_evaluator_protocol(self):
        assert isinstance(BudgetConstraintEvaluator(), ConstraintEvaluator)


# ═══════════════════════════════════════════════════════════════════════════
# validated_plan — immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestValidatedPlan:
    def test_returns_new_plan_with_valid_status(self):
        plan = Plan(
            goal=Goal(name="x"),
            estimated_cost=0.1,
            constraints=(PlanningConstraint(kind="budget", parameters={"max_cost": 1.0}, hard=True),),
        )
        assert plan.validation_status == ValidationStatus.UNVALIDATED

        new_plan, report = ConstraintEngine().validated_plan(plan)

        assert plan.validation_status == ValidationStatus.UNVALIDATED  # original untouched
        assert new_plan.validation_status == ValidationStatus.VALID
        assert new_plan.plan_id == plan.plan_id
        assert report.valid is True

    def test_returns_new_plan_with_invalid_status(self):
        plan = Plan(
            goal=Goal(name="x"),
            estimated_cost=5.0,
            constraints=(PlanningConstraint(kind="budget", parameters={"max_cost": 1.0}, hard=True),),
        )

        new_plan, report = ConstraintEngine().validated_plan(plan)

        assert new_plan.validation_status == ValidationStatus.INVALID
        assert report.valid is False


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_runtime_or_execution_imports(self):
        import inspect
        import src.monkey_brain.kernel.pipeline.planning.constraints as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "action_executor",
            "from src.monkey_brain.kernel.pipeline.execution import",
        ]
        for imp in forbidden:
            assert imp not in source, f"constraints.py must not import: {imp}"
