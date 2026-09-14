"""Monte Carlo future-state simulation for workload candidate ranking.

Three signals are fused into one score per LLM-proposed candidate:

  1. q_value     — Q(initial_state, workload_id) from the Bellman table;
                   historical exploitation signal.
  2. mc_value    — mean discounted return across N rollouts; each rollout
                   walks the candidate's step sequence, sampling next-states
                   from the transition table (empirical model) and reading
                   Q(step_state, capability) as the per-step reward estimate.
  3. llm_conf    — the explorer's self-assessed probability for this candidate.

Combined score:
  score = w_q * q_value + w_mc * mc_value + w_conf * llm_conf - w_var * mc_std

Rollout mechanics:
  state_0 = initial_state
  for step_i in candidate.steps[:MAX_DEPTH]:
    action_i  = step_i.capability_name
    q_i       = Q(state_i, action_i)          # 0 if unseen
    return   += discount^i * q_i
    discount *= gamma
    # next state: sample empirically, else synthesise
    past = transition_table.query(state_i, action_i)
    state_{i+1} = random.choice(past).next_state if past else {**state_i, "_mc_step": action_i}

When the transition table is empty (cold start) every rollout is deterministic
and mc_value collapses to the plain discounted Q-sum — which degrades gracefully
to Bellman-only behaviour.
"""

from __future__ import annotations


import logging
import random
from dataclasses import dataclass
from statistics import mean, stdev
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.fix.policy.transition import (
    ReadOnlyTransitionView,
    hash_state,
)
from src.monkey_brain.kernel.fix.policy.consensus import ProvenanceTracker

if TYPE_CHECKING:
    from src.monkey_brain.kernel.plan.workload.workload import Workload
    from src.monkey_brain.kernel.execute.provider.llm_explorer import WorkflowCandidate

logger = logging.getLogger("monkey_brain.rl.monte_carlo")

_DEFAULT_N_ROLLOUTS = 32
_DEFAULT_MAX_DEPTH = 10
_DEFAULT_W_Q = 0.30  # weight: Bellman Q-value
_DEFAULT_W_MC = 0.50  # weight: MC rollout mean
_DEFAULT_W_CONF = 0.20  # weight: LLM confidence
_DEFAULT_W_VAR = 0.10  # penalty: rollout std dev


@dataclass
class RankedCandidate:
    """A workload candidate annotated with Monte Carlo simulation results."""

    workload: "Workload"
    candidate: "WorkflowCandidate"
    q_value: float  # Q(state, first_step_capability) — historical signal
    mc_value: float  # mean discounted return across rollouts
    mc_std: float  # rollout standard deviation
    mc_ucb: float  # optimistic upper confidence bound
    llm_confidence: float  # explorer's self-assessed probability
    combined_score: float  # final ranking score fed to the policy
    rollouts_run: int
    real_trace_fraction: float = 0.0  # fraction of Q-updates backed by real executed traces

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_id": self.workload.workload_id,
            "candidate_name": self.candidate.name,
            "candidate_id": self.candidate.candidate_id,
            "source": self.candidate.source,
            "q_value": round(self.q_value, 6),
            "mc_value": round(self.mc_value, 6),
            "mc_std": round(self.mc_std, 6),
            "mc_ucb": round(self.mc_ucb, 6),
            "llm_confidence": round(self.llm_confidence, 4),
            "combined_score": round(self.combined_score, 6),
            "rollouts_run": self.rollouts_run,
            "real_trace_fraction": round(self.real_trace_fraction, 4),
            "step_count": len(self.candidate.steps),
        }


class MonteCarloPlanner:
    """Simulates future states for each LLM candidate and returns a ranked list.

    Instantiated once inside BellmanPolicy; the Q-table and TransitionTable are
    passed per-call so the planner never owns mutable RL state.

    Parameters
    ----------
    n_rollouts : int
        Number of stochastic rollouts per candidate.  32 is sufficient for
        low-variance Q-tables; increase to 64-128 as the transition table fills.
    max_depth : int
        Maximum number of steps to simulate per rollout.  Caps compute cost on
        deep ETASS workloads.
    w_q, w_mc, w_conf, w_var : float
        Weights for the four signals in the combined score formula.
    exploration_constant : float
        UCB coefficient applied to mc_std: ucb = mc_value + c * mc_std.
        Passed through to RankedCandidate.mc_ucb for callers that want
        optimistic selection instead of the default combined score.
    """

    def __init__(
        self,
        n_rollouts: int = _DEFAULT_N_ROLLOUTS,
        max_depth: int = _DEFAULT_MAX_DEPTH,
        w_q: float = _DEFAULT_W_Q,
        w_mc: float = _DEFAULT_W_MC,
        w_conf: float = _DEFAULT_W_CONF,
        w_var: float = _DEFAULT_W_VAR,
        exploration_constant: float = 0.1,
    ) -> None:
        self.n_rollouts = n_rollouts
        self.max_depth = max_depth
        self.w_q = w_q
        self.w_mc = w_mc
        self.w_conf = w_conf
        self.w_var = w_var
        self.exploration_constant = exploration_constant

        self._total_rollouts: int = 0
        self._total_simulations: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def simulate(
        self,
        candidates: list["WorkflowCandidate"],
        workloads: list["Workload"],
        initial_state: dict[str, Any],
        q_table: dict[tuple[str, str], float],
        transition_table: ReadOnlyTransitionView,
        gamma: float = 0.95,
        provenance: ProvenanceTracker | None = None,
        grounding_confidence: float = 1.0,
    ) -> list[RankedCandidate]:
        """Run MC rollouts for every candidate and return them ranked best-first.

        grounding_confidence (0–1) reflects how well the knowledge pack covers
        the planning context.  When low, the combined score shifts weight from
        MC rollouts (speculative) toward the Q-table (empirically grounded).

        The returned list has the same length as `candidates`.  If simulation
        fails for any candidate it still appears in the list with mc_value = 0.
        """
        if not candidates:
            return []

        if len(candidates) != len(workloads):
            raise ValueError(f"candidates ({len(candidates)}) and workloads ({len(workloads)}) must be same length")

        state_hash_init = hash_state(initial_state)
        ranked: list[RankedCandidate] = []

        for candidate, workload in zip(candidates, workloads):
            rc = self._rank_candidate(
                candidate,
                workload,
                initial_state,
                state_hash_init,
                q_table,
                transition_table,
                gamma,
                provenance,
                grounding_confidence=grounding_confidence,
            )
            ranked.append(rc)
            self._total_simulations += 1

        ranked.sort(key=lambda r: r.combined_score, reverse=True)

        if logger.isEnabledFor(logging.DEBUG):
            for i, r in enumerate(ranked):
                logger.debug(
                    "MC rank %d: %s  score=%.4f  mc=%.4f±%.4f  q=%.4f  conf=%.2f",
                    i + 1,
                    r.candidate.name,
                    r.combined_score,
                    r.mc_value,
                    r.mc_std,
                    r.q_value,
                    r.llm_confidence,
                )

        return ranked

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rank_candidate(
        self,
        candidate: "WorkflowCandidate",
        workload: "Workload",
        initial_state: dict[str, Any],
        state_hash_init: str,
        q_table: dict[tuple[str, str], float],
        transition_table: ReadOnlyTransitionView,
        gamma: float,
        provenance: ProvenanceTracker | None = None,
        grounding_confidence: float = 1.0,
    ) -> RankedCandidate:
        step_actions = [s.capability_name for s in workload.steps][: self.max_depth]

        # Q-value: keyed by first-step capability (matches what BellmanPolicy.update()
        # stores via transition.action = capability_name).
        first_action = step_actions[0] if step_actions else workload.workload_id
        q_value = q_table.get((state_hash_init, first_action), 0.0)

        # Real-trace fraction for the first step's Q-value — tells the consensus gate
        # how much authority this Q estimate actually has.
        real_trace_fraction = provenance.real_trace_fraction(state_hash_init, first_action) if provenance else 0.0

        if not step_actions:
            return self._zero_ranked(candidate, workload, q_value, real_trace_fraction)

        returns: list[float] = []
        for _ in range(self.n_rollouts):
            r = self._rollout(step_actions, initial_state, q_table, transition_table, gamma)
            returns.append(r)

        self._total_rollouts += len(returns)

        mc_value = mean(returns)
        mc_std = stdev(returns) if len(returns) >= 2 else 0.0
        mc_ucb = mc_value + self.exploration_constant * mc_std

        # When grounding_confidence is low the knowledge pack doesn't cover this
        # planning context well — MC rollouts are speculative.  Shift weight from
        # w_mc (simulation-based) toward w_q (empirically grounded real transitions).
        gc = max(0.0, min(1.0, grounding_confidence))
        effective_w_mc = self.w_mc * gc
        effective_w_q = self.w_q + self.w_mc * (1.0 - gc)

        combined = (
            effective_w_q * q_value
            + effective_w_mc * mc_value
            + self.w_conf * candidate.confidence
            - self.w_var * mc_std
        )

        return RankedCandidate(
            workload=workload,
            candidate=candidate,
            q_value=q_value,
            mc_value=mc_value,
            mc_std=mc_std,
            mc_ucb=mc_ucb,
            llm_confidence=candidate.confidence,
            combined_score=combined,
            rollouts_run=len(returns),
            real_trace_fraction=real_trace_fraction,
        )

    def _rollout(
        self,
        step_actions: list[str],
        initial_state: dict[str, Any],
        q_table: dict[tuple[str, str], float],
        transition_table: ReadOnlyTransitionView,
        gamma: float,
    ) -> float:
        state = dict(initial_state)
        total = 0.0
        discount = 1.0

        for action in step_actions:
            sh = hash_state(state)
            q_i = q_table.get((sh, action), 0.0)
            total += discount * q_i
            discount *= gamma

            # Sample next state from empirical transition model
            past = transition_table.query(state, action)
            if past:
                state = dict(random.choice(past).next_state)
            else:
                # Synthesise: tag state with the action taken so subsequent
                # Q-lookups see a different (unseen) state — avoids false hits.
                state = {**state, "_mc_step": action}

        return total

    def _zero_ranked(
        self,
        candidate: "WorkflowCandidate",
        workload: "Workload",
        q_value: float,
        real_trace_fraction: float = 0.0,
    ) -> RankedCandidate:
        combined = self.w_q * q_value + self.w_conf * candidate.confidence
        return RankedCandidate(
            workload=workload,
            candidate=candidate,
            q_value=q_value,
            mc_value=0.0,
            mc_std=0.0,
            mc_ucb=0.0,
            llm_confidence=candidate.confidence,
            combined_score=combined,
            rollouts_run=0,
            real_trace_fraction=real_trace_fraction,
        )

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        return {
            "n_rollouts": self.n_rollouts,
            "max_depth": self.max_depth,
            "weights": {
                "q": self.w_q,
                "mc": self.w_mc,
                "conf": self.w_conf,
                "var": self.w_var,
            },
            "exploration_constant": self.exploration_constant,
            "total_simulations": self._total_simulations,
            "total_rollouts": self._total_rollouts,
        }
