"""Tests for plan scoring (Step 8.6).

Validates the ScoringPolicy interface, DefaultScoringPolicy's five scoring
dimensions (cost, duration, confidence, risk, policy), PlanScorer's
selection logic, and pluggability (a different policy/weighting can flip
which candidate wins).
"""

from __future__ import annotations

from dataclasses import replace

from src.monkey_brain.kernel.pipeline.planning.domain import (
    Goal,
    Plan,
    PlanCandidate,
    PlanStep,
    PlanningOperator,
    ValidationStatus,
)
from src.monkey_brain.kernel.pipeline.planning.candidates import CandidateGenerator
from src.monkey_brain.kernel.pipeline.planning.scoring import (
    ScoreBreakdown,
    ScoringPolicy,
    DefaultScoringPolicy,
    PlanScorer,
)


def _candidate(
    cost: float,
    duration: float,
    confidence: float = 0.0,
    validation_status: ValidationStatus = ValidationStatus.UNVALIDATED,
    operator_names: tuple[str, ...] = (),
) -> PlanCandidate:
    steps = tuple(PlanStep(sequence=i, operator=PlanningOperator(name=name)) for i, name in enumerate(operator_names))
    plan = Plan(
        goal=Goal(name="x"),
        steps=steps,
        estimated_cost=cost,
        estimated_duration=duration,
        confidence=confidence,
        validation_status=validation_status,
    )
    return PlanCandidate(plan=plan)


# ═══════════════════════════════════════════════════════════════════════════
# Acquire-milk scenario — Step 8's own acceptance example
# ═══════════════════════════════════════════════════════════════════════════


class TestAcquireMilkScoring:
    def test_all_candidates_get_a_score_after_scoring(self):
        candidates = CandidateGenerator().generate(Goal(name="acquire_milk"))
        scored = PlanScorer().score_candidates(candidates)
        assert all(c.score is not None for c in scored)
        assert all(0.0 <= c.score <= 1.0 for c in scored)

    def test_original_candidates_unchanged_score_stays_none(self):
        """PlanCandidate is frozen — scoring must not mutate the originals."""
        candidates = CandidateGenerator().generate(Goal(name="acquire_milk"))
        PlanScorer().score_candidates(candidates)
        assert all(c.score is None for c in candidates)

    def test_select_best_returns_the_highest_scored_candidate(self):
        candidates = CandidateGenerator().generate(Goal(name="acquire_milk"))
        scorer = PlanScorer()
        best = scorer.select_best(candidates)
        scored = scorer.score_candidates(candidates)
        assert best.score == max(c.score for c in scored)

    def test_worst_candidate_scores_lowest(self):
        """delivery_service has both the highest cost and longest duration —
        it should never win against a candidate that's better on both."""
        candidates = CandidateGenerator().generate(Goal(name="acquire_milk"))
        scored = {c.plan.metadata["strategy"]: c.score for c in PlanScorer().score_candidates(candidates)}
        assert scored["delivery_service"] < scored["walk_to_store"]
        assert scored["delivery_service"] < scored["drive_to_store"]


# ═══════════════════════════════════════════════════════════════════════════
# Individual scoring dimensions
# ═══════════════════════════════════════════════════════════════════════════


class TestCostAndDurationScoring:
    def test_cheaper_candidate_scores_higher_on_cost_when_duration_tied(self):
        cheap = _candidate(cost=0.1, duration=100.0)
        expensive = _candidate(cost=0.9, duration=100.0)
        policy = DefaultScoringPolicy(
            cost_weight=1.0,
            duration_weight=0.0,
            confidence_weight=0.0,
            risk_weight=0.0,
            policy_weight=0.0,
        )
        scorer = PlanScorer(policy=policy)
        scored = scorer.score_candidates((cheap, expensive))
        by_cost = {round(c.plan.estimated_cost, 1): c.score for c in scored}
        assert by_cost[0.1] > by_cost[0.9]

    def test_faster_candidate_scores_higher_on_duration_when_cost_tied(self):
        fast = _candidate(cost=0.1, duration=10.0)
        slow = _candidate(cost=0.1, duration=1000.0)
        policy = DefaultScoringPolicy(
            cost_weight=0.0,
            duration_weight=1.0,
            confidence_weight=0.0,
            risk_weight=0.0,
            policy_weight=0.0,
        )
        scorer = PlanScorer(policy=policy)
        scored = scorer.score_candidates((fast, slow))
        by_duration = {c.plan.estimated_duration: c.score for c in scored}
        assert by_duration[10.0] > by_duration[1000.0]

    def test_identical_costs_normalize_to_perfect_score(self):
        a = _candidate(cost=0.5, duration=1.0)
        b = _candidate(cost=0.5, duration=1.0)
        policy = DefaultScoringPolicy(
            cost_weight=1.0,
            duration_weight=0.0,
            confidence_weight=0.0,
            risk_weight=0.0,
            policy_weight=0.0,
        )
        breakdown = policy.score(a, (a, b))
        assert breakdown.cost_score == 1.0


class TestConfidenceScoring:
    def test_higher_confidence_scores_higher(self):
        confident = _candidate(cost=0.1, duration=1.0, confidence=0.9)
        unsure = _candidate(cost=0.1, duration=1.0, confidence=0.1)
        policy = DefaultScoringPolicy(
            cost_weight=0.0,
            duration_weight=0.0,
            confidence_weight=1.0,
            risk_weight=0.0,
            policy_weight=0.0,
        )
        breakdown_confident = policy.score(confident, (confident, unsure))
        breakdown_unsure = policy.score(unsure, (confident, unsure))
        assert breakdown_confident.confidence_score > breakdown_unsure.confidence_score
        assert breakdown_confident.confidence_score == 0.9


class TestRiskScoring:
    def test_invalid_plan_is_maximally_risky(self):
        invalid = _candidate(cost=0.1, duration=1.0, validation_status=ValidationStatus.INVALID)
        policy = DefaultScoringPolicy()
        breakdown = policy.score(invalid, (invalid,))
        assert breakdown.risk_score == 0.0  # risk=1.0 -> risk_score = 1-risk = 0.0

    def test_high_confidence_lowers_derived_risk(self):
        confident = _candidate(cost=0.1, duration=1.0, confidence=0.9)
        unsure = _candidate(cost=0.1, duration=1.0, confidence=0.1)
        policy = DefaultScoringPolicy()
        confident_breakdown = policy.score(confident, (confident, unsure))
        unsure_breakdown = policy.score(unsure, (confident, unsure))
        assert confident_breakdown.risk_score > unsure_breakdown.risk_score

    def test_unset_confidence_is_neutral_risk(self):
        candidate = _candidate(cost=0.1, duration=1.0)  # confidence=0.0 (unset)
        policy = DefaultScoringPolicy()
        breakdown = policy.score(candidate, (candidate,))
        assert breakdown.risk_score == 0.5


class TestPolicyScoring:
    def test_no_preferences_configured_is_neutral(self):
        candidate = _candidate(cost=0.1, duration=1.0, operator_names=("Navigate",))
        policy = DefaultScoringPolicy()
        breakdown = policy.score(candidate, (candidate,))
        assert breakdown.policy_score == 0.5

    def test_preferred_operator_raises_policy_score(self):
        candidate = _candidate(cost=0.1, duration=1.0, operator_names=("AcquireItem",))
        policy = DefaultScoringPolicy(preferred_operators=frozenset({"AcquireItem"}))
        breakdown = policy.score(candidate, (candidate,))
        assert breakdown.policy_score > 0.5

    def test_penalized_operator_lowers_policy_score(self):
        candidate = _candidate(cost=0.1, duration=1.0, operator_names=("Navigate",))
        policy = DefaultScoringPolicy(penalized_operators=frozenset({"Navigate"}))
        breakdown = policy.score(candidate, (candidate,))
        assert breakdown.policy_score < 0.5

    def test_pluggable_policy_can_flip_the_winner(self):
        """Weighting policy dominantly and penalizing Navigate must make the
        candidate that avoids Navigate win, even though it's worse on cost
        and duration — the whole point of 'keep scoring pluggable'."""
        candidates = CandidateGenerator().generate(Goal(name="acquire_milk"))
        policy_dominant = DefaultScoringPolicy(
            cost_weight=0.05,
            duration_weight=0.05,
            confidence_weight=0.0,
            risk_weight=0.0,
            policy_weight=0.9,
            penalized_operators=frozenset({"Navigate"}),
        )
        best = PlanScorer(policy=policy_dominant).select_best(candidates)
        assert best.plan.metadata["strategy"] == "delivery_service"


# ═══════════════════════════════════════════════════════════════════════════
# PlanScorer.select_best — selection edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestSelectBest:
    def test_empty_candidates_returns_none(self):
        assert PlanScorer().select_best(()) is None

    def test_all_rejected_returns_none(self):
        candidates = CandidateGenerator().generate(Goal(name="acquire_milk"))
        all_rejected = tuple(replace(c, rejected=True) for c in candidates)
        assert PlanScorer().select_best(all_rejected) is None

    def test_rejected_candidates_are_excluded_from_selection(self):
        candidates = CandidateGenerator().generate(Goal(name="acquire_milk"))
        # Reject everything except delivery_service, even though it scores worst.
        mixed = tuple(
            (c if c.plan.metadata["strategy"] == "delivery_service" else replace(c, rejected=True)) for c in candidates
        )
        best = PlanScorer().select_best(mixed)
        assert best.plan.metadata["strategy"] == "delivery_service"


# ═══════════════════════════════════════════════════════════════════════════
# Interface conformance
# ═══════════════════════════════════════════════════════════════════════════


class TestScoringPolicyInterface:
    def test_default_policy_satisfies_protocol(self):
        assert isinstance(DefaultScoringPolicy(), ScoringPolicy)

    def test_score_breakdown_is_immutable(self):
        import pytest
        from dataclasses import FrozenInstanceError

        breakdown = ScoreBreakdown(total=0.5)
        with pytest.raises(FrozenInstanceError):
            breakdown.total = 0.9


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_runtime_or_execution_imports(self):
        import inspect
        import src.monkey_brain.kernel.pipeline.planning.scoring as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "action_executor",
            "from src.monkey_brain.kernel.pipeline.execution import",
        ]
        for imp in forbidden:
            assert imp not in source, f"scoring.py must not import: {imp}"
