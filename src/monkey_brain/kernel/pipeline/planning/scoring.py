"""Plan scoring (Step 8.6) — choose the best candidate.

Scores each PlanCandidate on cost, duration, confidence, risk, and policy,
combining them into one [0.0, 1.0] total via a pluggable ScoringPolicy.
select_best() then picks the highest-scoring, non-rejected candidate.

Note on "risk": the Plan domain model (Step 8.1) has no risk field — it
wasn't part of that step's listed fields (Goal, steps, cost, duration,
confidence, constraints, validation_status, metadata, trace). Rather than
retroactively add one to an already-delivered, already-tested dataclass, risk
is *derived* here from what's already on the Plan: an INVALID
validation_status (Step 8.4) is maximal risk; otherwise risk falls as
confidence rises. This keeps Step 8.1's model unmodified, matching this
step's "keep scoring pluggable" spirit — a caller who wants Plan to carry its
own risk estimate can inject a ScoringPolicy that reads it from
plan.metadata instead.

Cost and duration are normalized by min-max across the candidate set being
scored (there's no fixed "good"/"bad" cost or duration in the abstract —
only relative to the alternatives on the table), so scoring is inherently a
function of the whole candidate set, not one candidate in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable

from src.monkey_brain.kernel.pipeline.planning.domain import (
    PlanCandidate,
    ValidationStatus,
)


@dataclass(frozen=True)
class ScoreBreakdown:
    """Per-dimension contribution to a candidate's total score.

    Every sub-score is normalized to [0.0, 1.0] where higher is always
    better, so they can be combined (and later, in Step 8.8, explained)
    uniformly regardless of the raw units of what they measure.
    """

    cost_score: float = 0.0
    duration_score: float = 0.0
    confidence_score: float = 0.0
    risk_score: float = 0.0
    policy_score: float = 0.0
    total: float = 0.0


@runtime_checkable
class ScoringPolicy(Protocol):
    """Interface every scoring policy satisfies."""

    def score(
        self,
        candidate: PlanCandidate,
        candidates: tuple[PlanCandidate, ...],
    ) -> ScoreBreakdown: ...


def _min_max_normalize_lower_is_better(value: float, values: tuple[float, ...]) -> float:
    """1.0 for the lowest value in `values`, 0.0 for the highest; 1.0 for
    everyone if all values are equal (nothing to differentiate on)."""
    lo, hi = min(values), max(values)
    if hi <= lo:
        return 1.0
    return 1.0 - (value - lo) / (hi - lo)


def _derive_risk(candidate: PlanCandidate) -> float:
    """[0.0, 1.0], higher means riskier. See module docstring."""
    plan = candidate.plan
    if plan.validation_status == ValidationStatus.INVALID:
        return 1.0
    if plan.confidence > 0.0:
        return 1.0 - plan.confidence
    return 0.5  # unscored confidence — neither confident nor known-risky


@dataclass(frozen=True)
class DefaultScoringPolicy:
    """Weighted combination of cost, duration, confidence, risk, and policy
    preference. Weights need not sum to 1.0 — they're normalized internally.
    """

    cost_weight: float = 0.25
    duration_weight: float = 0.25
    confidence_weight: float = 0.2
    risk_weight: float = 0.2
    policy_weight: float = 0.1
    preferred_operators: frozenset[str] = field(default_factory=frozenset)
    penalized_operators: frozenset[str] = field(default_factory=frozenset)

    def score(
        self,
        candidate: PlanCandidate,
        candidates: tuple[PlanCandidate, ...],
    ) -> ScoreBreakdown:
        plan = candidate.plan
        costs = tuple(c.plan.estimated_cost for c in candidates)
        durations = tuple(c.plan.estimated_duration for c in candidates)

        cost_score = _min_max_normalize_lower_is_better(plan.estimated_cost, costs)
        duration_score = _min_max_normalize_lower_is_better(plan.estimated_duration, durations)
        confidence_score = max(0.0, min(1.0, plan.confidence))
        risk_score = 1.0 - _derive_risk(candidate)
        policy_score = self._policy_score(plan)

        weights = (
            self.cost_weight,
            self.duration_weight,
            self.confidence_weight,
            self.risk_weight,
            self.policy_weight,
        )
        weight_sum = sum(weights) or 1.0
        total = (
            cost_score * self.cost_weight
            + duration_score * self.duration_weight
            + confidence_score * self.confidence_weight
            + risk_score * self.risk_weight
            + policy_score * self.policy_weight
        ) / weight_sum

        return ScoreBreakdown(
            cost_score=cost_score,
            duration_score=duration_score,
            confidence_score=confidence_score,
            risk_score=risk_score,
            policy_score=policy_score,
            total=round(total, 4),
        )

    def _policy_score(self, plan) -> float:
        if not self.preferred_operators and not self.penalized_operators:
            return 0.5  # no policy configured — neutral, doesn't move the total
        operator_names = [s.operator.name for s in plan.steps if s.operator is not None]
        if not operator_names:
            return 0.5
        preferred = sum(1 for n in operator_names if n in self.preferred_operators)
        penalized = sum(1 for n in operator_names if n in self.penalized_operators)
        net = (preferred - penalized) / len(operator_names)
        return max(0.0, min(1.0, 0.5 + 0.5 * net))


class PlanScorer:
    """Scores a set of candidates and selects the best one."""

    def __init__(self, policy: ScoringPolicy | None = None) -> None:
        self._policy = policy or DefaultScoringPolicy()

    def score_candidates(self, candidates: tuple[PlanCandidate, ...]) -> tuple[PlanCandidate, ...]:
        """Return NEW candidates (PlanCandidate is frozen) with `.score` set."""
        if not candidates:
            return ()
        return tuple(replace(c, score=self._policy.score(c, candidates).total) for c in candidates)

    def score_breakdown(
        self,
        candidate: PlanCandidate,
        candidates: tuple[PlanCandidate, ...],
    ) -> ScoreBreakdown:
        """The full per-dimension breakdown for one candidate — useful for
        explainability (Step 8.8) without re-deriving it from `.score` alone."""
        return self._policy.score(candidate, candidates)

    def select_best(self, candidates: tuple[PlanCandidate, ...]) -> PlanCandidate | None:
        """The highest-scoring, non-rejected candidate — or None if every
        candidate is rejected or there are no candidates at all."""
        scored = self.score_candidates(candidates)
        eligible = [c for c in scored if not c.rejected]
        if not eligible:
            return None
        return max(eligible, key=lambda c: c.score if c.score is not None else -1.0)
