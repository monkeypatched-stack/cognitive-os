"""Reward & Evaluation Engine (Step 10.3) — turns a captured LearningExperience's
outcome into a scalar reward.

This is the first *algorithmic* piece of Step 10 (10.1/10.2 were pure data +
transcription). It is deliberately self-contained: it depends only on
LearningExperience/LearningOutcome from domain.py, nothing from CognitiveState,
ExecutionEngine, or CognitiveRuntime — reward evaluation is a pure function of
"what happened," not of how it happened or which pipeline produced it. This
mirrors the layering Step 9.4's RetryExecutor and Step 8.6's scoring already
used: a standalone engine, wired into the runtime later (10.7), not depending
on it now.

RewardWeights/RewardBreakdown live here rather than in domain.py, matching the
precedent retry.py set for RecoveryAction/RecoveryPolicy/RetryResult in Step
9.4 — sub-step-specific behavioral types belong with their engine, not folded
back into the algorithm-independent model.

experience.reward stays 0.0 (untouched) unless you call RewardEngine.evaluate();
LearningExperience is frozen, so evaluate() returns a *new* experience with
reward populated, the same replace-not-mutate idiom Step 8.6's plan scoring
used for PlanCandidate.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningExperience,
    LearningOutcome,
)


@dataclass(frozen=True)
class RewardWeights:
    """Configurable contribution of each factor to the final reward, all on
    a comparable [0, 1]-ish scale so total() naturally lands near [0, 1]."""

    goal_achieved: float = 0.7
    partial_credit: float = 0.3
    speed_bonus: float = 0.2
    budget_bonus: float = 0.1
    error_penalty: float = 0.15


@dataclass(frozen=True)
class RewardBreakdown:
    """Explains a reward as the sum of its parts — Step 10.9's trace consumes
    this directly rather than re-deriving "why" from a bare float."""

    base: float = 0.0
    speed_bonus: float = 0.0
    budget_bonus: float = 0.0
    error_penalty: float = 0.0
    total: float = 0.0
    rationale: str = ""


def compute_reward(
    outcome: LearningOutcome,
    weights: RewardWeights = RewardWeights(),
    *,
    duration_budget_seconds: float | None = None,
    cost_budget: float | None = None,
) -> RewardBreakdown:
    """Pure function: LearningOutcome -> RewardBreakdown.

    - Goal fully achieved -> base credit; partially achieved -> smaller base.
    - Achieving the goal faster than duration_budget_seconds earns a bonus
      proportional to how much budget was left unused.
    - Staying under cost_budget (or incurring zero cost, when no budget is
      given) earns an equivalent budget bonus.
    - Every recorded error subtracts a flat penalty.
    - Bonuses only apply when the goal was actually achieved — speed/budget
      without success isn't rewarded.
    """
    if outcome.goal_achieved:
        base = weights.goal_achieved
    elif outcome.partial:
        base = weights.partial_credit
    else:
        base = 0.0

    speed_bonus = 0.0
    if outcome.goal_achieved and duration_budget_seconds and duration_budget_seconds > 0:
        ratio = min(outcome.duration_seconds / duration_budget_seconds, 1.0)
        speed_bonus = weights.speed_bonus * (1.0 - ratio)

    budget_bonus = 0.0
    if outcome.goal_achieved:
        if cost_budget and cost_budget > 0:
            ratio = min(outcome.cost / cost_budget, 1.0)
            budget_bonus = weights.budget_bonus * (1.0 - ratio)
        elif outcome.cost <= 0:
            budget_bonus = weights.budget_bonus

    error_penalty = weights.error_penalty * len(outcome.errors)

    total = max(0.0, min(1.0, base + speed_bonus + budget_bonus - error_penalty))

    if outcome.goal_achieved:
        rationale = "goal achieved"
        if speed_bonus > 0:
            rationale += ", quickly"
        if budget_bonus > 0:
            rationale += ", within budget"
    elif outcome.partial:
        rationale = "goal partially achieved"
    else:
        rationale = "goal not achieved"
    if outcome.errors:
        rationale += f", {len(outcome.errors)} error(s) penalized"

    return RewardBreakdown(
        base=base,
        speed_bonus=speed_bonus,
        budget_bonus=budget_bonus,
        error_penalty=error_penalty,
        total=total,
        rationale=rationale,
    )


class ExperienceRewardEngine:
    """Evaluates captured experiences, producing a new experience with
    `.reward` populated and the breakdown attached to `.metadata` for
    explainability."""

    def __init__(
        self,
        weights: RewardWeights = RewardWeights(),
        *,
        duration_budget_seconds: float | None = None,
        cost_budget: float | None = None,
    ) -> None:
        self._weights = weights
        self._duration_budget_seconds = duration_budget_seconds
        self._cost_budget = cost_budget

    def evaluate(self, experience: LearningExperience) -> LearningExperience:
        breakdown = compute_reward(
            experience.outcome,
            self._weights,
            duration_budget_seconds=self._duration_budget_seconds,
            cost_budget=self._cost_budget,
        )
        metadata = dict(experience.metadata)
        metadata["reward_breakdown"] = dataclasses.asdict(breakdown)
        return dataclasses.replace(experience, reward=breakdown.total, metadata=metadata)


def evaluate_experience(experience: LearningExperience, **kwargs) -> LearningExperience:
    """Module-level convenience wrapper around ExperienceRewardEngine().evaluate()."""
    return ExperienceRewardEngine(**kwargs).evaluate(experience)
