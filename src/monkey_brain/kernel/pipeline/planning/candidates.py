"""Candidate plan generation (Step 8.5) — multiple alternative plans per goal.

Instead of Goal -> one Plan, produces Goal -> PlanCandidate A, B, C, ... Each
candidate is built by running a registered PlanStrategy (a named, ordered
sequence of operator calls) through the operator templates from Step 8.2.

Do not score yet — this step only enumerates. Every generated PlanCandidate
has score=None (Step 8.6 scores them) and Plan.confidence stays 0.0 (unset).
No coupling to CognitiveRuntime or ExecutionEngine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline.planning.domain import (
    Goal,
    Plan,
    PlanCandidate,
    PlanStep,
)
from src.monkey_brain.kernel.pipeline.planning.operators import (
    OperatorTemplate,
    OPERATOR_TEMPLATES,
)


@dataclass(frozen=True)
class OperatorCall:
    """One operator invocation within a PlanStrategy: which template to use
    (a key into an operator template registry, e.g. OPERATOR_TEMPLATES from
    Step 8.2) and the parameters to build() it with."""

    operator_name: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    """Optional override for the resulting PlanStep's description; falls back
    to the built PlanningOperator's own description if empty."""


@dataclass(frozen=True)
class PlanStrategy:
    """One alternative way to achieve a goal: a named, ordered operator sequence."""

    strategy_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    description: str = ""
    operator_calls: tuple[OperatorCall, ...] = ()


@dataclass(frozen=True)
class GenerationRule:
    """Declares the alternative strategies available for a goal, matched by name."""

    goal_name: str = ""
    strategies: tuple[PlanStrategy, ...] = ()


# Illustrative default rule: the exact "get 2L of milk" scenario used
# throughout this project's examples, with three genuinely different
# strategies (walk / drive / delivery) — matching Step 8's own acceptance
# example. Callers register their own via CandidateGenerator(rules=...) or
# generator.register_rule(...).
DEFAULT_GENERATION_RULES: dict[str, GenerationRule] = {
    "acquire_milk": GenerationRule(
        goal_name="acquire_milk",
        strategies=(
            PlanStrategy(
                name="walk_to_store",
                description="Walk to the nearest store and buy milk",
                operator_calls=(
                    OperatorCall(
                        operator_name="Navigate",
                        description="Walk to the store",
                        params={
                            "destination": "store",
                            "estimated_cost": 0.05,
                            "estimated_duration": 1200.0,
                        },
                    ),
                    OperatorCall(
                        operator_name="AcquireItem",
                        params={"item": "milk", "quantity": 2, "unit": "L"},
                    ),
                ),
            ),
            PlanStrategy(
                name="drive_to_store",
                description="Drive to the nearest store and buy milk",
                operator_calls=(
                    OperatorCall(
                        operator_name="Navigate",
                        description="Drive to the store",
                        params={
                            "destination": "store",
                            "estimated_cost": 0.15,
                            "estimated_duration": 300.0,
                        },
                    ),
                    OperatorCall(
                        operator_name="AcquireItem",
                        params={"item": "milk", "quantity": 2, "unit": "L"},
                    ),
                ),
            ),
            PlanStrategy(
                name="delivery_service",
                description="Order milk for delivery",
                operator_calls=(
                    OperatorCall(
                        operator_name="ReserveResource",
                        params={
                            "resource": "delivery_slot",
                            "estimated_cost": 0.3,
                            "estimated_duration": 60.0,
                        },
                    ),
                    OperatorCall(
                        operator_name="AcquireItem",
                        params={
                            "item": "milk",
                            "quantity": 2,
                            "unit": "L",
                            "estimated_cost": 0.25,
                            "estimated_duration": 3600.0,
                        },
                    ),
                ),
            ),
        ),
    ),
}


class CandidateGenerator:
    """Enumerates alternative PlanCandidates for a Goal. No scoring, no
    selection — that's Step 8.6."""

    def __init__(
        self,
        rules: dict[str, GenerationRule] | None = None,
        operator_templates: dict[str, OperatorTemplate] | None = None,
    ) -> None:
        self._rules: dict[str, GenerationRule] = dict(rules) if rules is not None else dict(DEFAULT_GENERATION_RULES)
        self._operator_templates: dict[str, OperatorTemplate] = (
            dict(operator_templates) if operator_templates is not None else dict(OPERATOR_TEMPLATES)
        )

    def register_rule(self, rule: GenerationRule) -> None:
        self._rules[rule.goal_name] = rule

    def generate(self, goal: Goal) -> tuple[PlanCandidate, ...]:
        """Every registered strategy for `goal`, each as an unscored PlanCandidate.

        A goal with no registered GenerationRule produces no candidates —
        there is no principled way to invent alternative strategies without
        domain knowledge (that would be a planning algorithm, out of scope
        for this step).
        """
        rule = self._rules.get(goal.name)
        if rule is None:
            return ()
        return tuple(self._build_candidate(goal, strategy) for strategy in rule.strategies)

    def _build_candidate(self, goal: Goal, strategy: PlanStrategy) -> PlanCandidate:
        steps: list[PlanStep] = []
        total_cost = 0.0
        total_duration = 0.0

        for sequence, call in enumerate(strategy.operator_calls):
            template = self._operator_templates.get(call.operator_name)
            if template is None:
                continue  # unresolvable operator reference — skip, don't invent one
            operator = template.build(**call.params)
            steps.append(
                PlanStep(
                    sequence=sequence,
                    operator=operator,
                    description=call.description or operator.description,
                    preconditions=operator.preconditions,
                    expected_outcome=operator.effects[0] if operator.effects else "",
                    estimated_cost=operator.estimated_cost,
                    estimated_duration=operator.estimated_duration,
                )
            )
            total_cost += operator.estimated_cost
            total_duration += operator.estimated_duration

        plan = Plan(
            goal=goal,
            steps=tuple(steps),
            estimated_cost=total_cost,
            estimated_duration=total_duration,
            metadata={
                "strategy": strategy.name,
                "strategy_description": strategy.description,
            },
        )
        return PlanCandidate(plan=plan)  # score stays None — not scored yet (Step 8.6)
