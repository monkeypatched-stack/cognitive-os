"""Constraint engine (Step 8.4) — plans become constraint-aware.

Evaluates a Plan's PlanningConstraints (budget, time, policy, capability,
inventory, safety, permissions) and produces a ValidationReport. Validation
happens before execution — this module only checks; it never executes, and
has no coupling to CognitiveRuntime or ExecutionEngine.

Each evaluator is self-contained: everything it needs to check is declared in
the constraint's own `parameters` (e.g. a budget constraint carries its own
`max_cost`) plus what the Plan itself already states (estimated_cost, steps,
each step's operator). No implicit "current runtime state" is consulted —
keeping validation a pure function of (constraint, plan).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from src.monkey_brain.kernel.pipeline.planning.domain import (
    Plan,
    PlanningConstraint,
    ValidationStatus,
)


@dataclass(frozen=True)
class ConstraintCheckResult:
    """The outcome of evaluating one PlanningConstraint against one Plan."""

    constraint_id: str = ""
    kind: str = ""
    hard: bool = True
    satisfied: bool = True
    message: str = ""


@dataclass(frozen=True)
class ValidationReport:
    """The aggregate result of validating a Plan against all its constraints."""

    plan_id: str = ""
    results: tuple[ConstraintCheckResult, ...] = ()
    valid: bool = True
    """False iff at least one HARD constraint failed. Soft-constraint failures
    are recorded in `warnings` but never flip this to False."""
    violations: tuple[str, ...] = ()
    """Messages for failed hard constraints."""
    warnings: tuple[str, ...] = ()
    """Messages for failed soft constraints."""


@runtime_checkable
class ConstraintEvaluator(Protocol):
    """Interface every constraint evaluator satisfies."""

    kind: str

    def evaluate(self, constraint: PlanningConstraint, plan: Plan) -> ConstraintCheckResult: ...


def _check(constraint: PlanningConstraint, kind: str, satisfied: bool, message: str) -> ConstraintCheckResult:
    return ConstraintCheckResult(
        constraint_id=constraint.constraint_id,
        kind=kind,
        hard=constraint.hard,
        satisfied=satisfied,
        message=message,
    )


class BudgetConstraintEvaluator:
    """parameters: {"max_cost": float} — checked against plan.estimated_cost."""

    kind = "budget"

    def evaluate(self, constraint: PlanningConstraint, plan: Plan) -> ConstraintCheckResult:
        max_cost = constraint.parameters.get("max_cost")
        if max_cost is None:
            return _check(
                constraint,
                self.kind,
                False,
                "budget constraint missing 'max_cost' parameter",
            )
        satisfied = plan.estimated_cost <= max_cost
        message = (
            f"estimated_cost {plan.estimated_cost} within budget {max_cost}"
            if satisfied
            else f"estimated_cost {plan.estimated_cost} exceeds budget {max_cost}"
        )
        return _check(constraint, self.kind, satisfied, message)


class TimeConstraintEvaluator:
    """parameters: {"max_duration": float} — checked against plan.estimated_duration."""

    kind = "time"

    def evaluate(self, constraint: PlanningConstraint, plan: Plan) -> ConstraintCheckResult:
        max_duration = constraint.parameters.get("max_duration")
        if max_duration is None:
            return _check(
                constraint,
                self.kind,
                False,
                "time constraint missing 'max_duration' parameter",
            )
        satisfied = plan.estimated_duration <= max_duration
        message = (
            f"estimated_duration {plan.estimated_duration} within limit {max_duration}"
            if satisfied
            else f"estimated_duration {plan.estimated_duration} exceeds limit {max_duration}"
        )
        return _check(constraint, self.kind, satisfied, message)


class PolicyConstraintEvaluator:
    """parameters: {"denied_operators": [str, ...]} — no step may use a denied operator."""

    kind = "policy"

    def evaluate(self, constraint: PlanningConstraint, plan: Plan) -> ConstraintCheckResult:
        denied = set(constraint.parameters.get("denied_operators", ()))
        used = {step.operator.name for step in plan.steps if step.operator is not None}
        violated = sorted(used & denied)
        satisfied = not violated
        message = "no denied operators used" if satisfied else f"denied operators used: {violated}"
        return _check(constraint, self.kind, satisfied, message)


class CapabilityConstraintEvaluator:
    """parameters: {"available_capabilities": [str, ...]} — every step operator's
    required_capabilities must be a subset of what's available."""

    kind = "capability"

    def evaluate(self, constraint: PlanningConstraint, plan: Plan) -> ConstraintCheckResult:
        available = set(constraint.parameters.get("available_capabilities", ()))
        missing: set[str] = set()
        for step in plan.steps:
            if step.operator is not None:
                missing |= set(step.operator.required_capabilities) - available
        satisfied = not missing
        message = "all required capabilities available" if satisfied else f"missing capabilities: {sorted(missing)}"
        return _check(constraint, self.kind, satisfied, message)


class InventoryConstraintEvaluator:
    """parameters: {"available_inventory": {item: quantity}} — checked against
    quantities implied by AcquireItem-style operator metadata (item/quantity)."""

    kind = "inventory"

    def evaluate(self, constraint: PlanningConstraint, plan: Plan) -> ConstraintCheckResult:
        available: dict[str, float] = dict(constraint.parameters.get("available_inventory", {}))
        needed: dict[str, float] = {}
        for step in plan.steps:
            if step.operator is None:
                continue
            item = step.operator.metadata.get("item")
            if item is None:
                continue
            needed[item] = needed.get(item, 0) + step.operator.metadata.get("quantity", 1)

        shortfalls = {
            item: qty - available.get(item, 0) for item, qty in needed.items() if qty > available.get(item, 0)
        }
        satisfied = not shortfalls
        message = "sufficient inventory for all items" if satisfied else f"insufficient inventory: {shortfalls}"
        return _check(constraint, self.kind, satisfied, message)


class SafetyConstraintEvaluator:
    """parameters: {"forbidden_effects": [str, ...]} — no step operator's effects
    may include a forbidden effect."""

    kind = "safety"

    def evaluate(self, constraint: PlanningConstraint, plan: Plan) -> ConstraintCheckResult:
        forbidden = set(constraint.parameters.get("forbidden_effects", ()))
        triggered: set[str] = set()
        for step in plan.steps:
            if step.operator is not None:
                triggered |= set(step.operator.effects) & forbidden
        satisfied = not triggered
        message = "no forbidden effects triggered" if satisfied else f"forbidden effects triggered: {sorted(triggered)}"
        return _check(constraint, self.kind, satisfied, message)


class PermissionsConstraintEvaluator:
    """parameters: {"required_permissions": [...], "granted_permissions": [...]}."""

    kind = "permissions"

    def evaluate(self, constraint: PlanningConstraint, plan: Plan) -> ConstraintCheckResult:
        required = set(constraint.parameters.get("required_permissions", ()))
        granted = set(constraint.parameters.get("granted_permissions", ()))
        missing = sorted(required - granted)
        satisfied = not missing
        message = "all required permissions granted" if satisfied else f"missing permissions: {missing}"
        return _check(constraint, self.kind, satisfied, message)


DEFAULT_EVALUATORS: dict[str, ConstraintEvaluator] = {
    e.kind: e
    for e in (
        BudgetConstraintEvaluator(),
        TimeConstraintEvaluator(),
        PolicyConstraintEvaluator(),
        CapabilityConstraintEvaluator(),
        InventoryConstraintEvaluator(),
        SafetyConstraintEvaluator(),
        PermissionsConstraintEvaluator(),
    )
}


class ConstraintEngine:
    """Validates a Plan against its own PlanningConstraints before execution."""

    def __init__(self, evaluators: dict[str, ConstraintEvaluator] | None = None) -> None:
        self._evaluators: dict[str, ConstraintEvaluator] = (
            dict(evaluators) if evaluators is not None else dict(DEFAULT_EVALUATORS)
        )

    def register_evaluator(self, evaluator: ConstraintEvaluator) -> None:
        self._evaluators[evaluator.kind] = evaluator

    def validate(self, plan: Plan) -> ValidationReport:
        """Evaluate every constraint in plan.constraints. An unrecognized
        constraint kind is treated as failed (we can't verify what we don't
        understand) rather than silently skipped."""
        results = []
        for constraint in plan.constraints:
            evaluator = self._evaluators.get(constraint.kind)
            if evaluator is None:
                results.append(
                    _check(
                        constraint,
                        constraint.kind,
                        False,
                        f"no evaluator registered for constraint kind '{constraint.kind}'",
                    )
                )
                continue
            results.append(evaluator.evaluate(constraint, plan))

        violations = tuple(r.message for r in results if not r.satisfied and r.hard)
        warnings = tuple(r.message for r in results if not r.satisfied and not r.hard)

        return ValidationReport(
            plan_id=plan.plan_id,
            results=tuple(results),
            valid=not violations,
            violations=violations,
            warnings=warnings,
        )

    def validated_plan(self, plan: Plan) -> tuple[Plan, ValidationReport]:
        """Validate `plan` and return (a NEW Plan with validation_status set
        accordingly, the ValidationReport). Plan is frozen — this never
        mutates the original."""
        report = self.validate(plan)
        status = ValidationStatus.VALID if report.valid else ValidationStatus.INVALID
        return replace(plan, validation_status=status), report
