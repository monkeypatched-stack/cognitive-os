"""Unit tests for the consensus gate — three correctness properties:

1. Low real_trace_fraction tiebreak labeling
   When gate fails and the Q-table winner has real_trace_fraction < 0.5,
   tiebreak_authority must be "synthetic_aggregate" and pipeline metadata
   must contain the "synthetic-aggregate" label — NOT "real_data_privilege".

2. Correlated-pair agreement rejection
   When CorrelationTracker EMA for a pair exceeds 0.8, a 2/3 agreement
   that involves only that correlated pair must be downgraded from
   "partial" to "none" and downgrade_reason must be non-empty.

3. Escalation after threshold divergences
   After DIVERGENCE_THRESHOLD gate failures for the same candidate key
   within DIVERGENCE_WINDOW evaluations, escalation_triggered must be True
   and divergence_count must equal the failure count.
"""

from __future__ import annotations

import sys
import os

# Ensure both repo root and src/ are reachable so intra-module imports resolve.
_repo = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.monkey_brain.kernel.fix.policy.consensus import (
    ConsensusGate,
    CorrelationTracker,
    DivergenceTracker,
    ProvenanceTracker,
    _spearman_rho,
    _DIVERGENCE_THRESHOLD,
    _DIVERGENCE_WINDOW,
    _REAL_TRACE_THRESHOLD,
    _STRUCTURAL_CORRELATION_THRESHOLD,
)

# ── Minimal stubs ─────────────────────────────────────────────────────────────
# These mirror the real dataclasses but carry no framework imports,
# keeping the unit tests hermetic and fast.


@dataclass
class _FakeWorkload:
    workload_id: str = "wl-test"
    metadata: dict = field(default_factory=dict)


@dataclass
class _FakeCandidate:
    candidate_id: str = "cand-test"
    name: str = "test_pipeline"
    confidence: float = 0.5
    steps: list = field(default_factory=list)


@dataclass
class _FakeRanked:
    workload: _FakeWorkload
    candidate: _FakeCandidate
    q_value: float = 0.0
    mc_value: float = 0.0
    mc_std: float = 0.0
    mc_ucb: float = 0.0
    llm_confidence: float = 0.5
    combined_score: float = 0.0
    rollouts_run: int = 0
    real_trace_fraction: float = 0.0


def _make_ranked(
    candidate_id: str = "cand-a",
    name: str = "pipeline_a",
    q_value: float = 0.0,
    mc_value: float = 0.0,
    llm_confidence: float = 0.5,
    combined_score: float = 0.0,
    real_trace_fraction: float = 0.0,
) -> _FakeRanked:
    return _FakeRanked(
        workload=_FakeWorkload(workload_id=f"wl-{candidate_id}", metadata={}),
        candidate=_FakeCandidate(candidate_id=candidate_id, name=name),
        q_value=q_value,
        mc_value=mc_value,
        llm_confidence=llm_confidence,
        combined_score=combined_score,
        real_trace_fraction=real_trace_fraction,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _gate() -> ConsensusGate:
    return ConsensusGate()


def _gate_with_correlated_pair(pair: str = "q_mc") -> ConsensusGate:
    """Return a gate whose CorrelationTracker already has EMA > threshold for `pair`."""
    gate = ConsensusGate()
    # Drive EMA above threshold by feeding many perfect-correlation observations
    required = 20
    for _ in range(required):
        gate._correlation.update(
            rho_q_mc=0.95 if pair == "q_mc" else 0.0,
            rho_q_llm=0.95 if pair == "q_llm" else 0.0,
            rho_mc_llm=0.95 if pair == "mc_llm" else 0.0,
        )
    assert pair in gate._correlation.structurally_correlated(), (
        f"Setup failed: EMA for {pair!r} = "
        f"{gate._correlation._ema[pair]:.3f} not above threshold {_STRUCTURAL_CORRELATION_THRESHOLD}"
    )
    return gate


# ── Spearman rho sanity tests ─────────────────────────────────────────────────


class TestSpearmanRho:
    def test_identical_ranks_returns_one(self):
        assert _spearman_rho([1, 2, 3], [1, 2, 3]) == 1.0

    def test_reversed_ranks_returns_minus_one(self):
        assert _spearman_rho([1, 2, 3], [3, 2, 1]) == -1.0

    def test_single_element_returns_one(self):
        assert _spearman_rho([1], [1]) == 1.0

    def test_partial_agreement(self):
        rho = _spearman_rho([1, 2, 3, 4], [1, 3, 2, 4])
        assert -1.0 <= rho <= 1.0


# ── Test 1: Low real_trace_fraction tiebreak labeling ─────────────────────────


class TestRealTraceFractionLabeling:
    """When gate fails and the Q-table winner has real_trace_fraction < threshold,
    tiebreak_authority must be "synthetic_aggregate", not "real_data_privilege"."""

    def _make_no_agreement_ranking(
        self,
        rtf_q_winner: float,
    ) -> list[_FakeRanked]:
        """Three candidates where Q, MC, LLM each prefer a different one."""
        # q ranks: A=1 B=2 C=3   → top_by_q = A
        # mc ranks: B=1 C=2 A=3  → top_by_mc = B
        # llm ranks: C=1 A=2 B=3 → top_by_llm = C
        return [
            _make_ranked(
                "cand-a",
                "pipeline_a",
                q_value=0.9,
                mc_value=0.1,
                llm_confidence=0.4,
                combined_score=0.5,
                real_trace_fraction=rtf_q_winner,
            ),
            _make_ranked(
                "cand-b",
                "pipeline_b",
                q_value=0.5,
                mc_value=0.9,
                llm_confidence=0.2,
                combined_score=0.5,
            ),
            _make_ranked(
                "cand-c",
                "pipeline_c",
                q_value=0.1,
                mc_value=0.5,
                llm_confidence=0.9,
                combined_score=0.5,
            ),
        ]

    def test_low_rtf_produces_synthetic_aggregate_label(self):
        gate = _gate()
        ranking = self._make_no_agreement_ranking(rtf_q_winner=0.1)
        result = gate.evaluate(ranking)

        assert result.agreement_level == "none"
        assert result.tiebreaker_used == "q_table"
        assert result.tiebreak_authority == "synthetic_aggregate", (
            f"Expected 'synthetic_aggregate', got {result.tiebreak_authority!r}"
        )
        meta = result.recommended.workload.metadata.get("consensus", {})
        assert meta.get("tiebreak_authority") == "synthetic_aggregate"
        assert meta.get("real_trace_fraction") < _REAL_TRACE_THRESHOLD

    def test_high_rtf_produces_real_data_privilege_label(self):
        gate = _gate()
        ranking = self._make_no_agreement_ranking(rtf_q_winner=0.9)
        result = gate.evaluate(ranking)

        assert result.tiebreak_authority == "real_data_privilege"
        meta = result.recommended.workload.metadata.get("consensus", {})
        assert meta.get("tiebreak_authority") == "real_data_privilege"

    def test_zero_rtf_is_never_labelled_as_real_data(self):
        gate = _gate()
        ranking = self._make_no_agreement_ranking(rtf_q_winner=0.0)
        result = gate.evaluate(ranking)

        assert result.tiebreak_authority != "real_data_privilege"

    def test_exactly_at_threshold_uses_real_data_privilege(self):
        gate = _gate()
        ranking = self._make_no_agreement_ranking(rtf_q_winner=_REAL_TRACE_THRESHOLD)
        result = gate.evaluate(ranking)

        assert result.tiebreak_authority == "real_data_privilege"

    def test_just_below_threshold_uses_synthetic_aggregate(self):
        gate = _gate()
        ranking = self._make_no_agreement_ranking(rtf_q_winner=_REAL_TRACE_THRESHOLD - 0.01)
        result = gate.evaluate(ranking)

        assert result.tiebreak_authority == "synthetic_aggregate"


# ── Test 2: Correlated-pair agreement rejection ────────────────────────────────


class TestCorrelatedPairDowngrade:
    """A 2/3 agreement between a structurally-correlated pair must be downgraded
    from 'partial' to 'none', with a non-empty downgrade_reason."""

    def _make_q_mc_agree_ranking(self) -> list[_FakeRanked]:
        """Q and MC agree on A; LLM prefers B."""
        return [
            _make_ranked(
                "cand-a",
                "pipeline_a",
                q_value=0.9,
                mc_value=0.9,
                llm_confidence=0.2,
                combined_score=0.6,
            ),
            _make_ranked(
                "cand-b",
                "pipeline_b",
                q_value=0.3,
                mc_value=0.3,
                llm_confidence=0.9,
                combined_score=0.4,
            ),
        ]

    def test_uncorrelated_pair_produces_partial(self):
        gate = _gate()  # fresh gate: no structural correlation yet
        ranking = self._make_q_mc_agree_ranking()
        result = gate.evaluate(ranking)

        assert result.agreement_level == "partial"
        assert result.downgrade_reason == ""

    def test_correlated_q_mc_pair_downgraded_to_none(self):
        gate = _gate_with_correlated_pair("q_mc")
        ranking = self._make_q_mc_agree_ranking()
        result = gate.evaluate(ranking)

        assert result.agreement_level == "none", (
            f"Expected 'none' after correlated-pair downgrade, got {result.agreement_level!r}"
        )
        assert result.downgrade_reason != "", "downgrade_reason must be non-empty"

    def test_downgrade_reason_names_the_correlated_pair(self):
        gate = _gate_with_correlated_pair("q_mc")
        ranking = self._make_q_mc_agree_ranking()
        result = gate.evaluate(ranking)

        assert "q" in result.downgrade_reason or "mc" in result.downgrade_reason

    def test_correlated_pair_is_flagged_in_structurally_correlated_pairs(self):
        gate = _gate_with_correlated_pair("q_mc")
        ranking = self._make_q_mc_agree_ranking()
        result = gate.evaluate(ranking)

        assert "q_mc" in result.structurally_correlated_pairs

    def test_three_way_agreement_not_downgraded_by_correlation(self):
        """3/3 agreement can't be faked by a single correlated pair."""
        gate = _gate_with_correlated_pair("q_mc")
        # All three signals agree on A
        ranking = [
            _make_ranked(
                "cand-a",
                "pipeline_a",
                q_value=0.9,
                mc_value=0.9,
                llm_confidence=0.9,
                combined_score=0.9,
            ),
            _make_ranked(
                "cand-b",
                "pipeline_b",
                q_value=0.1,
                mc_value=0.1,
                llm_confidence=0.1,
                combined_score=0.1,
            ),
        ]
        result = gate.evaluate(ranking)

        assert result.agreement_level == "strong"
        assert result.downgrade_reason == ""

    def test_non_correlated_pair_agreement_not_downgraded(self):
        """If q_mc is correlated but mc_llm is not, mc&llm agreement should pass as partial."""
        gate = _gate_with_correlated_pair("q_mc")  # only q_mc is correlated
        # mc and llm agree on A; Q prefers B
        ranking = [
            _make_ranked(
                "cand-a",
                "pipeline_a",
                q_value=0.1,
                mc_value=0.9,
                llm_confidence=0.9,
                combined_score=0.5,
            ),
            _make_ranked(
                "cand-b",
                "pipeline_b",
                q_value=0.9,
                mc_value=0.1,
                llm_confidence=0.1,
                combined_score=0.4,
            ),
        ]
        result = gate.evaluate(ranking)

        assert result.agreement_level == "partial"


# ── Test 3: Escalation after threshold divergences ────────────────────────────


class TestEscalation:
    """After DIVERGENCE_THRESHOLD gate failures for the same candidate key,
    escalation_triggered must be True."""

    def _make_no_agreement_ranking(self, candidate_name: str = "sticky_pipeline") -> list[_FakeRanked]:
        """Three candidates in maximal disagreement (each signal picks a different one)."""
        return [
            _make_ranked(
                "cand-a",
                candidate_name,
                q_value=0.9,
                mc_value=0.1,
                llm_confidence=0.4,
                combined_score=0.5,
            ),
            _make_ranked(
                "cand-b",
                "other_b",
                q_value=0.5,
                mc_value=0.9,
                llm_confidence=0.2,
                combined_score=0.5,
            ),
            _make_ranked(
                "cand-c",
                "other_c",
                q_value=0.1,
                mc_value=0.5,
                llm_confidence=0.9,
                combined_score=0.5,
            ),
        ]

    def test_no_escalation_before_threshold(self):
        gate = _gate()
        for _ in range(_DIVERGENCE_THRESHOLD - 1):
            result = gate.evaluate(self._make_no_agreement_ranking())

        assert result.escalation_triggered is False
        assert result.divergence_count < _DIVERGENCE_THRESHOLD

    def test_escalation_fires_at_threshold(self):
        gate = _gate()
        for i in range(_DIVERGENCE_THRESHOLD):
            result = gate.evaluate(self._make_no_agreement_ranking())

        assert result.escalation_triggered is True, (
            f"Expected escalation after {_DIVERGENCE_THRESHOLD} failures, divergence_count={result.divergence_count}"
        )

    def test_escalation_count_matches_failures(self):
        gate = _gate()
        for _ in range(_DIVERGENCE_THRESHOLD):
            result = gate.evaluate(self._make_no_agreement_ranking())

        assert result.divergence_count == _DIVERGENCE_THRESHOLD

    def test_strong_agreement_resets_divergence_count(self):
        gate = _gate()

        # Drive up to threshold
        for _ in range(_DIVERGENCE_THRESHOLD):
            gate.evaluate(self._make_no_agreement_ranking())

        # Strong agreement resets the window
        strong_ranking = [
            _make_ranked(
                "cand-a",
                "sticky_pipeline",
                q_value=0.9,
                mc_value=0.9,
                llm_confidence=0.9,
                combined_score=0.9,
            ),
        ]
        gate.evaluate(strong_ranking)

        # After reset, failures start from 0
        assert gate._divergence.failure_count("sticky_pipeline") == 0

    def test_escalation_clears_after_strong_agreement_then_rearm(self):
        gate = _gate()

        # Trigger escalation
        for _ in range(_DIVERGENCE_THRESHOLD):
            gate.evaluate(self._make_no_agreement_ranking())

        # Flush with a strong agreement
        gate.evaluate(
            [
                _make_ranked(
                    "cand-a",
                    "sticky_pipeline",
                    q_value=0.9,
                    mc_value=0.9,
                    llm_confidence=0.9,
                    combined_score=0.9,
                )
            ]
        )

        # Should no longer escalate on the next failure
        result = gate.evaluate(self._make_no_agreement_ranking())
        assert result.escalation_triggered is False

    def test_gate_never_deadlocks_on_escalation(self):
        """Escalation must still return a recommendation, not raise or return None."""
        gate = _gate()
        for _ in range(_DIVERGENCE_THRESHOLD + 5):
            result = gate.evaluate(self._make_no_agreement_ranking())

        assert result.recommended is not None
        assert result.recommended.workload is not None

    def test_pipeline_metadata_carries_escalation_flag(self):
        gate = _gate()
        for _ in range(_DIVERGENCE_THRESHOLD):
            result = gate.evaluate(self._make_no_agreement_ranking())

        meta = result.recommended.workload.metadata.get("consensus", {})
        assert meta.get("escalation_triggered") is True
        assert meta.get("provisional") is True

    def test_rolling_window_expires_old_failures(self):
        """Failures older than DIVERGENCE_WINDOW should not count."""
        gate = _gate()
        # Fill window with failures
        for _ in range(_DIVERGENCE_THRESHOLD):
            gate.evaluate(self._make_no_agreement_ranking())

        # Flood the window with strong-agreement results to push old failures out
        for _ in range(_DIVERGENCE_WINDOW):
            gate.evaluate(
                [
                    _make_ranked(
                        "cand-a",
                        "sticky_pipeline",
                        q_value=0.9,
                        mc_value=0.9,
                        llm_confidence=0.9,
                        combined_score=0.9,
                    )
                ]
            )

        assert not gate._divergence.should_escalate("sticky_pipeline")

    def test_different_candidate_keys_are_tracked_independently(self):
        gate = _gate()

        def _ranking_for(name: str) -> list[_FakeRanked]:
            return [
                _make_ranked(
                    "cand-a",
                    name,
                    q_value=0.9,
                    mc_value=0.1,
                    llm_confidence=0.4,
                    combined_score=0.5,
                ),
                _make_ranked(
                    "cand-b",
                    "other",
                    q_value=0.1,
                    mc_value=0.9,
                    llm_confidence=0.9,
                    combined_score=0.4,
                ),
                _make_ranked(
                    "cand-c",
                    "another",
                    q_value=0.5,
                    mc_value=0.5,
                    llm_confidence=0.1,
                    combined_score=0.3,
                ),
            ]

        # Drive pipeline_x to threshold
        for _ in range(_DIVERGENCE_THRESHOLD):
            gate.evaluate(_ranking_for("pipeline_x"))

        # pipeline_y should have zero failures
        assert gate._divergence.failure_count("pipeline_y") == 0
        assert not gate._divergence.should_escalate("pipeline_y")


# ── ProvenanceTracker unit tests ───────────────────────────────────────────────


class TestProvenanceTracker:
    def test_zero_fraction_for_unseen_key(self):
        p = ProvenanceTracker()
        assert p.real_trace_fraction("state-abc", "action-x") == 0.0

    def test_fraction_equals_one_after_all_real(self):
        p = ProvenanceTracker()
        for _ in range(10):
            p.record_real("s", "a")
        assert p.real_trace_fraction("s", "a") == 1.0

    def test_fraction_increases_with_each_real_update(self):
        p = ProvenanceTracker()
        p.record_real("s", "a")
        assert p.real_trace_fraction("s", "a") == 1.0

    def test_summary_reports_grounded_count(self):
        p = ProvenanceTracker()
        for _ in range(5):
            p.record_real("s1", "a1")  # fraction = 1.0
        p.record_real("s2", "a2")  # fraction = 1.0
        s = p.summary()
        assert s["tracked_state_actions"] == 2
        assert s["grounded_above_threshold"] == 2


# ── CorrelationTracker unit tests ─────────────────────────────────────────────


class TestCorrelationTracker:
    def test_no_flags_before_min_observations(self):
        ct = CorrelationTracker()
        for _ in range(4):
            ct.update(1.0, 1.0, 1.0)
        assert ct.structurally_correlated() == frozenset()

    def test_flags_pair_after_min_observations(self):
        ct = CorrelationTracker()
        for _ in range(10):
            ct.update(rho_q_mc=0.95, rho_q_llm=0.0, rho_mc_llm=0.0)
        correlated = ct.structurally_correlated()
        assert "q_mc" in correlated
        assert "q_llm" not in correlated

    def test_ema_decays_slowly(self):
        ct = CorrelationTracker(alpha=0.1)
        # Seed with high correlation
        ct.update(1.0, 1.0, 1.0)
        # Then feed low correlation — EMA should decay toward 0
        for _ in range(5):
            ct.update(0.0, 0.0, 0.0)
        # After 5 updates with alpha=0.1 and input=0.0, EMA ≈ 1.0 * 0.9^5 ≈ 0.59
        assert ct._ema["q_mc"] < _STRUCTURAL_CORRELATION_THRESHOLD

    def test_ema_rises_to_threshold_over_many_updates(self):
        ct = CorrelationTracker(alpha=0.1)
        for _ in range(100):
            ct.update(1.0, 0.0, 0.0)
        # After 100 updates EMA → 1.0
        assert ct._ema["q_mc"] > _STRUCTURAL_CORRELATION_THRESHOLD


# ── DivergenceTracker unit tests ──────────────────────────────────────────────


class TestDivergenceTracker:
    def test_no_escalation_at_zero(self):
        dt = DivergenceTracker()
        assert not dt.should_escalate("key")

    def test_escalates_at_threshold(self):
        dt = DivergenceTracker(window=20, threshold=3)
        for _ in range(3):
            dt.record_failure("key")
        assert dt.should_escalate("key")

    def test_strong_resets_failure_count(self):
        dt = DivergenceTracker()
        for _ in range(_DIVERGENCE_THRESHOLD):
            dt.record_failure("key")
        dt.record_strong("key")
        # Strong agreement clears the failure history
        assert dt.failure_count("key") == 0

    def test_window_maxlen_evicts_old_entries(self):
        dt = DivergenceTracker(window=5, threshold=3)
        for _ in range(3):
            dt.record_failure("key")
        # Fill rest of window with strong results — failures pushed out
        for _ in range(5):
            dt.record_strong("key")
        assert not dt.should_escalate("key")

    def test_independent_keys(self):
        dt = DivergenceTracker()
        for _ in range(_DIVERGENCE_THRESHOLD):
            dt.record_failure("key-a")
        assert not dt.should_escalate("key-b")
