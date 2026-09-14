"""Adaptive negotiation planning for TransactionCoordinator.

This module owns the two responsibilities the coordinator used to blend
together: *whether to keep negotiating at all* (TerminalStateEvaluator,
fully deterministic — never asks an LLM anything) and *who to negotiate
with next* (NegotiationPlanner, re-ranked from live state every round,
never a static walk of a pre-sorted list).

The coordinator's job shrinks to: ask the planner for a target, negotiate
with it, fold the outcome into TransactionState, ask the evaluator if
that's terminal. If it is, stop — the LLM is never consulted once the
runtime has already proven a terminal condition, so it can never override
one (this was the root cause of the "walks every affiliate regardless of
outcome" bug: the LLM's own next-action choice was previously the only
thing that could end a round, so an LLM that kept suggesting
"contact_another_affiliate" could out-vote an already-achieved objective
or an already-reached Nash equilibrium).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.monkey_brain.kernel.society.transaction import (
        NegotiationTrace,
        TransactionStatus,
    )

# Suggested-action values from _strategic_context's game-theoretic
# evaluation that mean "the equilibrium strategy is to stop" -- the two
# terminal action labels TransactionCoordinator.execute() derives from
# TerminalState.status, defined here directly (not imported) to keep this
# module free of a runtime import cycle with transaction.py.
_EQUILIBRIUM_TERMINAL_ACTIONS = frozenset({"terminate_transaction", "complete_objective"})


@dataclass(frozen=True)
class TerminalState:
    """The TerminalStateEvaluator's verdict for one point in a
    transaction's lifecycle. `status`/`reason` are only meaningful when
    `is_terminal` is True."""

    is_terminal: bool
    status: "TransactionStatus | None" = None
    reason: str = ""


@dataclass
class TransactionState:
    """Mutable, single-source-of-truth transaction state — the "current
    state" both the planner and the evaluator read. `candidates` is the
    original trust-ranked eligible-affiliate list (kept for the planner's
    tie-break order, never mutated); everything about what has actually
    happened lives in the other fields."""

    originating_actor_id: str
    objective: str
    candidates: tuple[str, ...]
    max_steps: int
    started_at: float = field(default_factory=time.time)
    timeout_seconds: float | None = None

    contacted: list[str] = field(default_factory=list)
    failed: set[str] = field(default_factory=set)
    pending_target: str | None = None

    last_target: str | None = None
    last_trace: "NegotiationTrace | None" = None
    last_strategic_context: dict[str, Any] | None = None

    cancelled: bool = False
    error: str | None = None

    def remaining_candidates(self) -> list[str]:
        contacted_set = set(self.contacted)
        return [c for c in self.candidates if c not in contacted_set]

    def record_contact(
        self,
        actor_id: str,
        trace: "NegotiationTrace | None",
        strategic_context: dict[str, Any] | None,
    ) -> None:
        self.contacted.append(actor_id)
        if trace is not None and trace.execution_outcome == "failed":
            self.failed.add(actor_id)
        self.last_target = actor_id
        self.last_trace = trace
        self.last_strategic_context = strategic_context


class TerminalStateEvaluator:
    """Deterministic, LLM-free. The coordinator must call this after every
    round and stop the instant it reports terminal — no strategic
    decision, LLM or otherwise, runs after that point.

    `policy_gate`, if given, is called with the current state and returns
    a non-empty reason string when policy requires termination, else
    None/empty — kept as an injectable hook rather than a hardcoded
    governance call so this evaluator has no dependency on PlanetaryRuntime
    and can be unit-tested in isolation.
    """

    def __init__(
        self,
        *,
        policy_gate: Callable[[TransactionState], str | None] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._policy_gate = policy_gate
        self._timeout_seconds = timeout_seconds

    def evaluate(self, state: TransactionState) -> TerminalState:
        from src.monkey_brain.kernel.society.transaction import TransactionStatus

        if state.cancelled:
            return TerminalState(True, TransactionStatus.TERMINATED, "cancelled")

        if state.error:
            return TerminalState(
                True,
                TransactionStatus.TERMINATED,
                f"unrecoverable error: {state.error}",
            )

        if self._policy_gate is not None:
            policy_reason = self._policy_gate(state)
            if policy_reason:
                return TerminalState(True, TransactionStatus.TERMINATED, policy_reason)

        trace = state.last_trace
        if trace is not None and trace.execution_outcome == "goal_achieved":
            return TerminalState(True, TransactionStatus.COMPLETED, "objective achieved")

        strategic_context = state.last_strategic_context
        if strategic_context and strategic_context.get("equilibrium"):
            suggested = strategic_context.get("suggested_action")
            if suggested in _EQUILIBRIUM_TERMINAL_ACTIONS:
                status = (
                    TransactionStatus.COMPLETED if suggested == "complete_objective" else TransactionStatus.TERMINATED
                )
                return TerminalState(True, status, f"nash equilibrium reached: {suggested}")

        # A queued retry/re-contact of the same affiliate (pending_target)
        # is not "remaining" in the not-yet-contacted sense, but it is a
        # live continuation, not an exhausted search space -- don't
        # terminate out from under it.
        if state.pending_target is None and not state.remaining_candidates():
            return TerminalState(True, TransactionStatus.TERMINATED, "no eligible affiliates remain")

        if self._timeout_seconds is not None and (time.time() - state.started_at) > self._timeout_seconds:
            return TerminalState(True, TransactionStatus.TERMINATED, "timeout")

        if len(state.contacted) >= state.max_steps:
            return TerminalState(
                True,
                TransactionStatus.TERMINATED,
                f"reached max_steps ({state.max_steps}) without a terminal decision",
            )

        return TerminalState(False)


class NegotiationPlanner:
    """Selects the single next affiliate to negotiate with, re-ranked from
    live state every call — never a static walk of the original sorted
    list. `trust_lookup(originating_actor_id, target_id) -> float` is
    called fresh each time so a trust change from the round that just
    finished (TransactionCoordinator._update_trust_from_trace) can change
    who ranks best before the next affiliate is chosen."""

    def __init__(self, *, trust_lookup: Callable[[str, str], float] | None = None) -> None:
        self._trust_lookup = trust_lookup

    def select_best_affiliate(self, state: TransactionState) -> str | None:
        remaining = state.remaining_candidates()
        if not remaining:
            return None
        if self._trust_lookup is None:
            return remaining[0]

        def sort_key(actor_id: str) -> tuple[float, int]:
            trust = self._trust_lookup(state.originating_actor_id, actor_id)
            return (-trust, state.candidates.index(actor_id))

        return min(remaining, key=sort_key)
