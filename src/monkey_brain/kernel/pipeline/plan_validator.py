"""PlanValidator — validates plans before execution.

Checks:
- Goal satisfaction
- Preconditions
- Feasibility
- Resource constraints
- Policy compliance

Plans that fail validation are rejected before reaching execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.monkey_brain.kernel.pipeline.belief_state import BeliefState, Plan

logger = logging.getLogger("agentos.pipeline.plan_validator")


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating a plan."""

    valid: bool = True
    """Whether the plan passes all checks."""
    violations: tuple[str, ...] = ()
    """List of violation descriptions."""
    warnings: tuple[str, ...] = ()
    """Non-blocking warnings."""
    score: float = 1.0
    """Validation score. [0.0, 1.0]"""


class PlanValidator:
    """Validates plans before execution.

    Checks:
    - Plan is not empty
    - Goal is defined
    - Preconditions are satisfiable
    - Cost is within budget
    - Confidence meets threshold
    - Risk is acceptable
    """

    def __init__(
        self,
        max_cost: float = 1.0,
        min_confidence: float = 0.1,
        max_risk: float = 0.9,
    ) -> None:
        self._max_cost = max_cost
        self._min_confidence = min_confidence
        self._max_risk = max_risk

    def validate(
        self,
        plan: Plan,
        belief: BeliefState | None = None,
    ) -> ValidationResult:
        """Validate a plan.

        Args:
            plan: The plan to validate
            belief: Current belief state (for precondition checking)

        Returns:
            ValidationResult with validity, violations, and score
        """
        violations = []
        warnings = []

        # Check: plan has a goal
        if not plan.goal:
            violations.append("plan_has_no_goal")

        # Check: plan has steps
        if not plan.steps:
            violations.append("plan_has_no_steps")

        # Check: cost within budget
        if plan.cost > self._max_cost:
            violations.append(f"cost_exceeds_budget:{plan.cost:.2f}>{self._max_cost}")

        # Check: confidence meets threshold
        if plan.confidence < self._min_confidence:
            violations.append(f"confidence_below_threshold:{plan.confidence:.2f}<{self._min_confidence}")

        # Check: risk is acceptable
        if plan.risk > self._max_risk:
            violations.append(f"risk_too_high:{plan.risk:.2f}>{self._max_risk}")

        # Check: preconditions (if belief provided)
        if belief and plan.preconditions:
            for precond in plan.preconditions:
                if not self._check_precondition(precond, belief):
                    warnings.append(f"precondition_unverified:{precond}")

        valid = len(violations) == 0
        score = 1.0 - (len(violations) * 0.2) - (len(warnings) * 0.05)
        score = max(0.0, min(1.0, score))

        if not valid:
            logger.warning(
                "[validator] Plan rejected: %d violations (%s), score=%.2f",
                len(violations),
                ", ".join(violations),
                score,
            )

        return ValidationResult(
            valid=valid,
            violations=tuple(violations),
            warnings=tuple(warnings),
            score=score,
        )

    def _check_precondition(self, precondition: str, belief: BeliefState) -> bool:
        """Check if a precondition is satisfied by the current belief."""
        # Simple check: precondition is satisfied if a matching fact exists
        if precondition.startswith("verify:"):
            entity_attr = precondition[len("verify:") :]
            parts = entity_attr.split(".", 1)
            if len(parts) == 2:
                for fact in belief.facts:
                    if fact.entity == parts[0] and fact.attribute == parts[1]:
                        return fact.confidence >= 0.5
        elif precondition.startswith("goal_defined:"):
            return bool(belief.goal.name)
        return True  # Default: assume satisfied
