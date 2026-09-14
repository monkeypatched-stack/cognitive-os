"""Consensus gate — cross-signal agreement with provenance, structural correlation, and escalation.

Three issues addressed over the naive version:

1. Q-TABLE INDEPENDENCE
   The Q-table used as a tiebreaker on gate failure isn't an independent arbiter when
   its values are informed by zero-initialisation or bootstrap estimates that have never
   touched a real executed trace.  ProvenanceTracker records, per (state_hash, action),
   how many Q-table updates came from real executed traces.  When the gate fails and
   the Q-table winner is selected, real_trace_fraction is checked:

     >= REAL_TRACE_THRESHOLD (0.5) → tiebreak_authority = "real_data_privilege"
     <  REAL_TRACE_THRESHOLD       → tiebreak_authority = "synthetic_aggregate"
                                      (logged + labelled in workload metadata)

2. NON-INDEPENDENCE OF SIGNALS
   Q, MC, and LLM share upstream assumptions: MC uses Q-table values as per-step
   reward estimates, so Q and MC will naturally correlate as the Q-table matures.
   Two-of-three agreement between a structurally correlated pair is one bias counted
   twice, not two independent witnesses.

   CorrelationTracker maintains an EMA of pairwise Spearman rho across all past
   decisions.  If a pair's EMA exceeds STRUCTURAL_CORRELATION_THRESHOLD (0.8),
   that pair is flagged.  A "partial" (2/3) agreement that involves only a
   structurally-correlated pair is downgraded to "none" — requiring that at least
   one of the agreeing signals is not structurally entangled with the other.

3. ESCALATION ON REPEATED DIVERGENCE
   A "none"-agreement gate failure that just applies a score penalty and continues
   autonomously allows a persistently divergent candidate to keep clearing thresholds
   indefinitely without human visibility.

   DivergenceTracker maintains a rolling window of gate outcomes per candidate name.
   After DIVERGENCE_THRESHOLD (3) failures in DIVERGENCE_WINDOW (20) evaluations,
   the recommendation is flagged provisional and Cingulate is notified (best-effort,
   non-blocking).  A subsequent "strong" agreement for the same key resets the window.
   The gate never deadlocks — escalation returns a recommendation, not a refusal.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.monkey_brain.kernel.predict.mcts.rl_monte_carlo import RankedCandidate

logger = logging.getLogger("monkey_brain.rl.consensus")

# ── Tunables ──────────────────────────────────────────────────────────────────
_ANTI_CORRELATION_THRESHOLD = -0.20  # gate fails if any rho below this
_DIVERGENCE_PENALTY = 0.50  # multiplier on combined_score when gate fails
_REAL_TRACE_THRESHOLD = 0.50  # min real_trace_fraction for "real_data_privilege"
_STRUCTURAL_CORRELATION_THRESHOLD = 0.80  # EMA rho above which a pair is flagged
_EMA_ALPHA = 0.10  # EMA decay — newer observations weighted more
_MIN_OBS_FOR_STRUCTURAL_FLAG = 5  # don't flag structural correlation too early
_DIVERGENCE_WINDOW = 20  # rolling window size per candidate key
_DIVERGENCE_THRESHOLD = 3  # failures within window that trigger escalation

# Maps frozenset of two signal names → canonical pair key used in CorrelationTracker
_SIGNAL_PAIR_KEYS: dict[frozenset[str], str] = {
    frozenset({"q", "mc"}): "q_mc",
    frozenset({"q", "llm"}): "q_llm",
    frozenset({"mc", "llm"}): "mc_llm",
}


# ── ProvenanceTracker ─────────────────────────────────────────────────────────


class ProvenanceTracker:
    """Tracks how many Q-table updates came from real executed traces per (state_hash, action).

    Updated exclusively by BellmanPolicy.update() on source="real" transitions.
    Read by MonteCarloPlanner to annotate RankedCandidate.real_trace_fraction,
    and by ConsensusGate to determine tiebreak authority.

    A real_trace_fraction of 0.0 means: the Q-value for this key is the uninformed
    default (0.0 by initialisation), not "real data showed 0."  The distinction
    matters — a 0.0-fraction Q-table tiebreaker has no authority.
    """

    def __init__(self) -> None:
        self._real: dict[tuple[str, str], int] = {}
        self._total: dict[tuple[str, str], int] = {}

    def record_real(self, state_hash: str, action: str) -> None:
        key = (state_hash, action)
        self._real[key] = self._real.get(key, 0) + 1
        self._total[key] = self._total.get(key, 0) + 1

    def real_trace_fraction(self, state_hash: str, action: str) -> float:
        key = (state_hash, action)
        total = self._total.get(key, 0)
        return self._real.get(key, 0) / total if total > 0 else 0.0

    def summary(self) -> dict[str, Any]:
        n = len(self._total)
        grounded = sum(1 for k in self._total if self._real.get(k, 0) / self._total[k] >= _REAL_TRACE_THRESHOLD)
        return {
            "tracked_state_actions": n,
            "grounded_above_threshold": grounded,
            "fraction_grounded": round(grounded / n, 4) if n else 0.0,
        }


# ── CorrelationTracker ────────────────────────────────────────────────────────


class CorrelationTracker:
    """Exponential moving average of pairwise Spearman rho across past consensus decisions.

    Used to detect structural correlation between signal pairs — situations where
    two signals reliably agree not because they're independent corroborators but
    because they share upstream assumptions (e.g., MC uses Q-table values as rewards).
    """

    def __init__(
        self,
        alpha: float = _EMA_ALPHA,
        threshold: float = _STRUCTURAL_CORRELATION_THRESHOLD,
        min_obs: int = _MIN_OBS_FOR_STRUCTURAL_FLAG,
    ) -> None:
        self._alpha = alpha
        self._threshold = threshold
        self._min_obs = min_obs
        self._ema: dict[str, float] = {"q_mc": 0.0, "q_llm": 0.0, "mc_llm": 0.0}
        self._n = 0

    def update(self, rho_q_mc: float, rho_q_llm: float, rho_mc_llm: float) -> None:
        new_values = {"q_mc": rho_q_mc, "q_llm": rho_q_llm, "mc_llm": rho_mc_llm}
        if self._n == 0:
            self._ema = dict(new_values)
        else:
            for pair, val in new_values.items():
                self._ema[pair] = self._alpha * val + (1.0 - self._alpha) * self._ema[pair]
        self._n += 1

    def structurally_correlated(self) -> frozenset[str]:
        """Return pair names whose EMA rho exceeds the structural correlation threshold.

        Returns empty set until min_obs observations have been recorded — avoids
        flagging early runs where the EMA is still dominated by the zero initialisation.
        """
        if self._n < self._min_obs:
            return frozenset()
        return frozenset(pair for pair, ema in self._ema.items() if ema > self._threshold)

    def ema_snapshot(self) -> dict[str, float]:
        return {k: round(v, 4) for k, v in self._ema.items()}

    def summary(self) -> dict[str, Any]:
        return {
            "observations": self._n,
            "ema": self.ema_snapshot(),
            "structurally_correlated": sorted(self.structurally_correlated()),
            "threshold": self._threshold,
        }


# ── DivergenceTracker ─────────────────────────────────────────────────────────


class DivergenceTracker:
    """Rolling-window gate-failure counter per candidate key.

    Incremented on every "none"-agreement gate failure for the recommended candidate.
    Reset (via True→False) on a "strong" agreement for the same key, so a transient
    divergence doesn't permanently flag a candidate.
    """

    def __init__(
        self,
        window: int = _DIVERGENCE_WINDOW,
        threshold: int = _DIVERGENCE_THRESHOLD,
    ) -> None:
        self._window = window
        self._threshold = threshold
        self._history: dict[str, deque[bool]] = {}

    def record_failure(self, key: str) -> None:
        self._history.setdefault(key, deque(maxlen=self._window)).append(True)

    def record_strong(self, key: str) -> None:
        self._history[key] = deque(maxlen=self._window)

    def failure_count(self, key: str) -> int:
        return int(sum(self._history.get(key, deque())))

    def should_escalate(self, key: str) -> bool:
        return self.failure_count(key) >= self._threshold

    def summary(self) -> dict[str, Any]:
        return {
            "tracked_keys": len(self._history),
            "keys_over_threshold": [k for k in self._history if self.should_escalate(k)],
        }


# ── ConsensusResult ───────────────────────────────────────────────────────────


@dataclass
class ConsensusResult:
    """Full output of ConsensusGate.evaluate()."""

    # Agreement
    agreement_level: str  # "strong" | "partial" | "none"
    agreement_score: float  # 0.0–1.0
    gate_passed: bool
    downgrade_reason: str  # non-empty when partial→none downgrade applied

    # Per-signal top candidates (candidate_id)
    top_by_q: str
    top_by_mc: str
    top_by_llm: str

    # Pairwise Spearman correlations (current evaluation)
    rho_q_mc: float
    rho_q_llm: float
    rho_mc_llm: float

    # Structural correlation (from CorrelationTracker EMA)
    structurally_correlated_pairs: list[str]

    # Recommendation
    recommended: RankedCandidate
    tiebreaker_used: str  # "none" | "majority" | "q_table"
    tiebreak_authority: str  # "n/a" | "majority_vote" | "real_data_privilege" | "synthetic_aggregate"
    dissenting_signals: list[str]
    effective_score: float

    # Divergence / escalation
    divergence_count: int
    escalation_triggered: bool

    # Rank breakdown (for observability / debugging)
    rank_table: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agreement_level": self.agreement_level,
            "agreement_score": round(self.agreement_score, 4),
            "gate_passed": self.gate_passed,
            "downgrade_reason": self.downgrade_reason,
            "top_by_q": self.top_by_q,
            "top_by_mc": self.top_by_mc,
            "top_by_llm": self.top_by_llm,
            "rho_q_mc": self.rho_q_mc,
            "rho_q_llm": self.rho_q_llm,
            "rho_mc_llm": self.rho_mc_llm,
            "structurally_correlated_pairs": self.structurally_correlated_pairs,
            "recommended": self.recommended.candidate.candidate_id,
            "recommended_name": self.recommended.candidate.name,
            "tiebreaker_used": self.tiebreaker_used,
            "tiebreak_authority": self.tiebreak_authority,
            "dissenting_signals": self.dissenting_signals,
            "effective_score": round(self.effective_score, 6),
            "divergence_count": self.divergence_count,
            "escalation_triggered": self.escalation_triggered,
            "rank_table": self.rank_table,
        }


# ── ConsensusGate ─────────────────────────────────────────────────────────────


class ConsensusGate:
    """Evaluates cross-signal agreement and returns a gated, annotated recommendation.

    Holds three sub-components that persist state across decisions:
      _provenance   — ProvenanceTracker (injected; owned by BellmanPolicy)
      _correlation  — CorrelationTracker (owned; EMA evolves across evaluations)
      _divergence   — DivergenceTracker  (owned; rolling window per candidate key)
    """

    def __init__(
        self,
        provenance: ProvenanceTracker | None = None,
        ema_alpha: float = _EMA_ALPHA,
        correlation_threshold: float = _STRUCTURAL_CORRELATION_THRESHOLD,
        divergence_window: int = _DIVERGENCE_WINDOW,
        divergence_threshold: int = _DIVERGENCE_THRESHOLD,
        real_trace_threshold: float = _REAL_TRACE_THRESHOLD,
    ) -> None:
        self._provenance = provenance or ProvenanceTracker()
        self._correlation = CorrelationTracker(alpha=ema_alpha, threshold=correlation_threshold)
        self._divergence = DivergenceTracker(window=divergence_window, threshold=divergence_threshold)
        self._real_trace_threshold = real_trace_threshold

        self._evaluations: int = 0
        self._gate_failures: int = 0
        self._escalation_count: int = 0
        self._partial_count: int = 0
        self._downgrade_count: int = 0

    def set_provenance(self, provenance: ProvenanceTracker) -> None:
        """Replace the provenance tracker — called by BellmanPolicy after construction."""
        self._provenance = provenance

    # ── Public API ─────────────────────────────────────────────────────────────

    def evaluate(
        self,
        mc_ranking: list[RankedCandidate],
        initial_state_hash: str = "",
    ) -> ConsensusResult:
        """Compute cross-signal agreement and return a gated recommendation.

        Parameters
        ----------
        mc_ranking : list[RankedCandidate]
            Output of MonteCarloPlanner.simulate(), best-first by combined_score.
        initial_state_hash : str
            Hash of the initial state passed to the planner — used for provenance
            lookup when the gate fails and Q-table winner is selected.
        """
        if not mc_ranking:
            raise ValueError("ConsensusGate.evaluate() requires at least one candidate")

        self._evaluations += 1

        # ── Trivial: single candidate ─────────────────────────────────────────
        if len(mc_ranking) == 1:
            rc = mc_ranking[0]
            self._divergence.record_strong(rc.candidate.name)
            self._correlation.update(1.0, 1.0, 1.0)
            return self._build_trivial(rc)

        n = len(mc_ranking)

        # ── Per-signal rank lists (index i → rank of mc_ranking[i]) ───────────
        def _ranks(attr: str) -> list[int]:
            order = sorted(range(n), key=lambda i: getattr(mc_ranking[i], attr), reverse=True)
            rank_of = {order[r]: r + 1 for r in range(n)}
            return [rank_of[i] for i in range(n)]

        q_ranks = _ranks("q_value")
        mc_ranks = _ranks("mc_value")
        llm_ranks = _ranks("llm_confidence")

        ids = [rc.candidate.candidate_id for rc in mc_ranking]
        top_by_q = ids[q_ranks.index(1)]
        top_by_mc = ids[mc_ranks.index(1)]
        top_by_llm = ids[llm_ranks.index(1)]

        rho_q_mc = _spearman_rho(q_ranks, mc_ranks)
        rho_q_llm = _spearman_rho(q_ranks, llm_ranks)
        rho_mc_llm = _spearman_rho(mc_ranks, llm_ranks)

        # ── Update CorrelationTracker BEFORE reading structural flags ──────────
        self._correlation.update(rho_q_mc, rho_q_llm, rho_mc_llm)
        correlated_pairs = self._correlation.structurally_correlated()

        # ── Agreement classification with structural-correlation downgrade ─────
        signal_tops = {"q": top_by_q, "mc": top_by_mc, "llm": top_by_llm}
        top_counts: dict[str, int] = {}
        for cid in signal_tops.values():
            top_counts[cid] = top_counts.get(cid, 0) + 1

        majority_count = max(top_counts.values())
        majority_id = max(top_counts, key=lambda k: top_counts[k])

        agreement_level = "none"
        downgrade_reason = ""

        if majority_count == 3:
            agreement_level = "strong"
        elif majority_count == 2:
            # Identify the agreeing pair
            agreeing = frozenset(sig for sig, cid in signal_tops.items() if cid == majority_id)
            pair_key = _SIGNAL_PAIR_KEYS.get(agreeing, "")
            if pair_key and pair_key in correlated_pairs:
                # Issue 2: correlated pair — downgrade partial to none
                agreement_level = "none"
                downgrade_reason = (
                    f"signals '{' & '.join(sorted(agreeing))}' agree but are "
                    f"structurally correlated (EMA rho={self._correlation._ema.get(pair_key, 0):.2f} "
                    f"> {self._correlation._threshold:.1f}) — agreement counts as one vote, not two"
                )
                self._downgrade_count += 1
                logger.info("ConsensusGate: partial→none downgrade: %s", downgrade_reason)
            else:
                agreement_level = "partial"
                self._partial_count += 1

        # ── Tiebreaker selection ───────────────────────────────────────────────
        if agreement_level == "strong":
            rec_id = majority_id
            tiebreaker = "none"
            tiebreak_auth = "n/a"
            dissenters: list[str] = []

        elif agreement_level == "partial":
            rec_id = majority_id
            tiebreaker = "majority"
            tiebreak_auth = "majority_vote"
            dissenters = [sig for sig, cid in signal_tops.items() if cid != majority_id]

        else:
            # Gate failure — Q-table tiebreaker (Issue 1)
            rec_id = top_by_q
            tiebreaker = "q_table"
            dissenters = [sig for sig in ("mc", "llm") if signal_tops[sig] != top_by_q]
            self._gate_failures += 1

            # Provenance check on the Q-table winner (Issue 1)
            q_winner_rc = next((rc for rc in mc_ranking if rc.candidate.candidate_id == rec_id), None)
            # real_trace_fraction is populated by MonteCarloPlanner via whatever
            # ProvenanceTracker it happened to be called with (possibly a
            # different instance, or None) at simulate() time, which may now be
            # stale relative to self._provenance — the tracker actually owned by
            # this gate and kept current by BellmanPolicy.update() in between.
            # When the caller supplies initial_state_hash, look it up fresh here
            # instead of trusting the candidate's baked-in value: same
            # (state_hash, first_action) key MonteCarloPlanner._simulate_one()
            # uses (rl_monte_carlo.py) — workload.steps, not candidate.steps
            # (WorkflowCandidate.steps is a list of plain dicts; Workload.steps
            # is the attribute-bearing WorkloadStep list _simulate_one keys on).
            if initial_state_hash and q_winner_rc is not None:
                steps = getattr(q_winner_rc.workload, "steps", None)
                first_action = steps[0].capability_name if steps else q_winner_rc.workload.workload_id
                rtf = self._provenance.real_trace_fraction(initial_state_hash, first_action)
            else:
                rtf = getattr(q_winner_rc, "real_trace_fraction", 0.0) if q_winner_rc else 0.0

            if rtf >= self._real_trace_threshold:
                tiebreak_auth = "real_data_privilege"
            else:
                tiebreak_auth = "synthetic_aggregate"
                logger.warning(
                    "ConsensusGate: Q-table tiebreaker for %r has real_trace_fraction=%.3f "
                    "(< threshold %.2f) — synthetic-aggregate tiebreak, not ground-truth-backed. "
                    "tops: q=%s mc=%s llm=%s  rho(q,mc)=%.2f rho(q,llm)=%.2f rho(mc,llm)=%.2f",
                    rec_id,
                    rtf,
                    self._real_trace_threshold,
                    top_by_q,
                    top_by_mc,
                    top_by_llm,
                    rho_q_mc,
                    rho_q_llm,
                    rho_mc_llm,
                )

        # ── Gate pass condition ────────────────────────────────────────────────
        min_rho = min(rho_q_mc, rho_q_llm, rho_mc_llm)
        gate_passed = agreement_level in ("strong", "partial") or min_rho >= _ANTI_CORRELATION_THRESHOLD

        # ── Divergence tracking and escalation (Issue 3) ──────────────────────
        rec_rc = next(rc for rc in mc_ranking if rc.candidate.candidate_id == rec_id)
        candidate_key = rec_rc.candidate.name

        if agreement_level == "none":
            self._divergence.record_failure(candidate_key)
        elif agreement_level == "strong":
            self._divergence.record_strong(candidate_key)
        # "partial" does not reset nor increment — it's ambiguous

        divergence_count = self._divergence.failure_count(candidate_key)
        escalation_triggered = self._divergence.should_escalate(candidate_key)

        if escalation_triggered:
            self._escalation_count += 1
            logger.error(
                "ConsensusGate ESCALATION: candidate %r accumulated %d gate failures "
                "in the last %d evaluations — routing to Cingulate for human review. "
                "Workload will proceed as provisional pending review.",
                candidate_key,
                divergence_count,
                self._divergence._window,
            )
            self._notify_cingulate(candidate_key, divergence_count, rec_rc)

        # ── Effective score ────────────────────────────────────────────────────
        effective_score = rec_rc.combined_score * _DIVERGENCE_PENALTY if not gate_passed else rec_rc.combined_score

        # ── Workload metadata annotation ───────────────────────────────────────
        rtf_for_meta = getattr(rec_rc, "real_trace_fraction", 0.0)
        rec_rc.workload.metadata["consensus"] = {
            "agreement_level": agreement_level,
            "gate_passed": gate_passed,
            "downgrade_reason": downgrade_reason,
            "tiebreaker": tiebreaker,
            "tiebreak_authority": tiebreak_auth,
            "dissenting_signals": dissenters,
            "effective_score": round(effective_score, 6),
            "real_trace_fraction": round(rtf_for_meta, 4),
            "structurally_correlated_pairs": sorted(correlated_pairs),
            "divergence_count": divergence_count,
            "escalation_triggered": escalation_triggered,
            "provisional": escalation_triggered,
        }

        rank_table = [
            {
                "candidate_id": mc_ranking[i].candidate.candidate_id,
                "name": mc_ranking[i].candidate.name,
                "q_rank": q_ranks[i],
                "mc_rank": mc_ranks[i],
                "llm_rank": llm_ranks[i],
                "q_value": round(mc_ranking[i].q_value, 6),
                "mc_value": round(mc_ranking[i].mc_value, 6),
                "llm_confidence": round(mc_ranking[i].llm_confidence, 4),
                "combined_score": round(mc_ranking[i].combined_score, 6),
                "real_trace_fraction": round(getattr(mc_ranking[i], "real_trace_fraction", 0.0), 4),
            }
            for i in range(n)
        ]

        return ConsensusResult(
            agreement_level=agreement_level,
            agreement_score=_agreement_score(majority_count),
            gate_passed=gate_passed,
            downgrade_reason=downgrade_reason,
            top_by_q=top_by_q,
            top_by_mc=top_by_mc,
            top_by_llm=top_by_llm,
            rho_q_mc=rho_q_mc,
            rho_q_llm=rho_q_llm,
            rho_mc_llm=rho_mc_llm,
            structurally_correlated_pairs=sorted(correlated_pairs),
            recommended=rec_rc,
            tiebreaker_used=tiebreaker,
            tiebreak_authority=tiebreak_auth,
            dissenting_signals=dissenters,
            effective_score=effective_score,
            divergence_count=divergence_count,
            escalation_triggered=escalation_triggered,
            rank_table=rank_table,
        )

    # ── Observability ──────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        return {
            "evaluations": self._evaluations,
            "gate_failures": self._gate_failures,
            "partial_agreements": self._partial_count,
            "downgrades": self._downgrade_count,
            "escalations": self._escalation_count,
            "gate_failure_rate": (round(self._gate_failures / self._evaluations, 4) if self._evaluations else 0.0),
            "correlation": self._correlation.summary(),
            "divergence": self._divergence.summary(),
            "provenance": self._provenance.summary(),
        }

    # ── Internals ──────────────────────────────────────────────────────────────

    def _build_trivial(self, rc: RankedCandidate) -> ConsensusResult:
        rc.workload.metadata["consensus"] = {
            "agreement_level": "strong",
            "gate_passed": True,
            "tiebreaker": "none",
            "tiebreak_authority": "n/a",
            "dissenting_signals": [],
            "effective_score": rc.combined_score,
            "real_trace_fraction": round(getattr(rc, "real_trace_fraction", 0.0), 4),
            "structurally_correlated_pairs": [],
            "divergence_count": 0,
            "escalation_triggered": False,
            "provisional": False,
            "downgrade_reason": "",
        }
        return ConsensusResult(
            agreement_level="strong",
            agreement_score=1.0,
            gate_passed=True,
            downgrade_reason="",
            top_by_q=rc.candidate.candidate_id,
            top_by_mc=rc.candidate.candidate_id,
            top_by_llm=rc.candidate.candidate_id,
            rho_q_mc=1.0,
            rho_q_llm=1.0,
            rho_mc_llm=1.0,
            structurally_correlated_pairs=[],
            recommended=rc,
            tiebreaker_used="none",
            tiebreak_authority="n/a",
            dissenting_signals=[],
            effective_score=rc.combined_score,
            divergence_count=0,
            escalation_triggered=False,
            rank_table=[
                {
                    "candidate_id": rc.candidate.candidate_id,
                    "name": rc.candidate.name,
                    "q_rank": 1,
                    "mc_rank": 1,
                    "llm_rank": 1,
                    "q_value": rc.q_value,
                    "mc_value": rc.mc_value,
                    "llm_confidence": rc.llm_confidence,
                    "combined_score": rc.combined_score,
                    "real_trace_fraction": round(getattr(rc, "real_trace_fraction", 0.0), 4),
                }
            ],
        )

    @staticmethod
    def _notify_cingulate(
        candidate_key: str,
        divergence_count: int,
        rec_rc: RankedCandidate,
    ) -> None:
        """Best-effort, non-blocking Cingulate escalation notification.

        If Cingulate is unavailable the gate still returns a provisional recommendation.
        The metadata and logger.error() above provide the fallback audit trail.
        """
        try:
            import asyncio

            from src.cingulate.review import CingulateReview

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.error(
                    "Cingulate escalation for %s skipped: no running event loop",
                    candidate_key,
                )
                return
            task = loop.create_task(
                CingulateReview.escalate(
                    candidate_key=candidate_key,
                    divergence_count=divergence_count,
                    workload_id=rec_rc.workload.workload_id,
                    reason="consensus_gate_repeated_divergence",
                )
            )
            # ensure_future() held no reference: asyncio keeps only a WEAK ref, so this
            # GOVERNANCE escalation could be garbage-collected mid-flight, and any exception
            # inside it vanished. Retain the task and surface failures.
            _ESCALATION_TASKS.add(task)
            task.add_done_callback(_ESCALATION_TASKS.discard)
            task.add_done_callback(_log_escalation_failure)
        except Exception:
            logger.exception("Cingulate escalation for %s failed to schedule", candidate_key)


# Strong references to in-flight escalation tasks (asyncio only holds a weak ref, so a task
# with no strong reference can be garbage-collected mid-execution).
_ESCALATION_TASKS: set = set()


def _log_escalation_failure(task) -> None:
    if task.cancelled():
        logger.error("Cingulate escalation was cancelled before completing")
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Cingulate escalation failed: %s", exc, exc_info=exc)


# ── Module-level helpers ───────────────────────────────────────────────────────


def _spearman_rho(ranks_a: list[int], ranks_b: list[int]) -> float:
    """Spearman rank correlation:  ρ = 1 − 6Σd²/ n(n²−1)."""
    n = len(ranks_a)
    if n <= 1:
        return 1.0
    d_sq = sum((a - b) ** 2 for a, b in zip(ranks_a, ranks_b, strict=True))
    return round(max(-1.0, min(1.0, 1.0 - 6.0 * d_sq / (n * (n * n - 1)))), 4)


def _agreement_score(majority_count: int) -> float:
    return {3: 1.0, 2: 2 / 3, 1: 0.0, 0: 0.0}.get(majority_count, 0.0)
