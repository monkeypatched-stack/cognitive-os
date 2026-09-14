"""Required Transaction Execution Logic — LLM-driven multi-actor
negotiation, facilitated (not decided) by the Planetary Runtime.

The runtime deliberately contains no hardcoded negotiation workflow: it
resolves who's eligible to participate, delivers messages, and lets each
affiliate run its own normal cognitive tick. The *originating actor*
(via an LLM call this coordinator makes on its behalf) decides what
happens next after each round — contact another affiliate, ask the same
one for more information, keep negotiating, terminate, or complete the
objective — until the transaction reaches a terminal state.

    Identify Relevant Societies -> Resolve Affiliations -> Filter
    Eligible Affiliations -> Send Grounded Message -> Affiliate Ticks ->
    Negotiation Trace -> Aggregate -> LLM Decides Next Step -> Stream
    Progress -> Repeat Until Terminal
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from src.monkey_brain.kernel.society.negotiation_planning import (
    NegotiationPlanner,
    TerminalState,
    TerminalStateEvaluator,
    TransactionState,
)

if TYPE_CHECKING:
    from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

logger = logging.getLogger("agentos.transaction")

_MAX_STEPS_DEFAULT = 8
# Deterministic-terminal actions (terminate_transaction/complete_objective)
# are deliberately excluded here: only TerminalStateEvaluator may end a
# transaction (see negotiation_planning.py). The LLM consulted in
# _decide_next_action is only ever asked *how* to keep negotiating, and
# only when the runtime has already proven the round is not terminal --
# so it structurally cannot override a terminal condition.
_STRATEGIC_ACTIONS = frozenset(
    {
        "contact_another_affiliate",
        "request_additional_information",
        "continue_negotiation",
    }
)


class TransactionStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    TERMINATED = "terminated"


@dataclass(frozen=True)
class NegotiationTrace:
    """Step 6: what one affiliate's cognitive tick produced in response
    to one transaction message — reasoning summary, observations, local
    belief updates, execution outcome, confidence, natural-language
    explanation. Built by duck-typing the affiliate's own tick result
    (same attributes _publish_tick_events/_build_negotiation_trace in
    integration.py already read), never by asking the affiliate to
    self-report in a fixed schema."""

    trace_id: str = field(default_factory=lambda: uuid4().hex)
    actor_id: str = ""
    reasoning_summary: str = ""
    observations: dict[str, Any] = field(default_factory=dict)
    belief_updates: dict[str, Any] = field(default_factory=dict)
    execution_outcome: str = ""
    confidence: float = 0.5
    explanation: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class TransactionStep:
    """One round: the message sent to one affiliate, its negotiation
    trace, and the LLM-decided next action for the originating actor."""

    step_number: int
    target_actor_id: str
    message: str
    trace: NegotiationTrace | None
    next_action: str
    next_action_reason: str
    strategic_context: dict[str, Any] | None = None
    """Game-theoretic grounding for this round's decision — see
    TransactionCoordinator._strategic_context(). None when unavailable
    (e.g. no trace yet, or evaluation failed non-fatally)."""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class TransactionResult:
    transaction_id: str
    originating_actor_id: str
    objective: str
    status: TransactionStatus
    steps: tuple[TransactionStep, ...] = ()
    societies_involved: tuple[str, ...] = ()
    affiliates_contacted: tuple[str, ...] = ()
    duration_ms: float = 0.0
    final_outcome: str = ""
    timestamp: float = field(default_factory=time.time)


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


class TransactionCoordinator:
    """Facilitates transaction execution for PlanetaryRuntime — owns none
    of the negotiation logic itself. Constructed with a back-reference to
    the owning PlanetaryRuntime (same composition pattern this codebase
    already uses for GameTheoryRuntime/CoordinationEngine/etc. — see
    PlanetaryRuntime.__init__), since resolving societies, affiliations,
    authorization, and message delivery all live there.

    `policy_gate`, forwarded to TerminalStateEvaluator on every execute()
    call, is an optional extension point for a future "policy requires
    termination" signal. It defaults to None (never blocks): the
    codebase's governance layer (SocietyGovernanceEngine.authorize) is
    grant-based, not deny-based, so there is no existing concept of a
    policy explicitly blocking an in-progress negotiation to wire up
    honestly yet -- inventing a default-deny check here would break every
    real transaction rather than add a real termination condition.
    """

    def __init__(
        self,
        planetary: "PlanetaryRuntime",
        *,
        policy_gate: "Callable[[TransactionState], str | None] | None" = None,
    ) -> None:
        self._planetary = planetary
        self._policy_gate = policy_gate

    # ── Public entry point ──────────────────────────────────────────────

    async def execute(
        self,
        originating_actor_id: str,
        objective: str,
        *,
        max_steps: int = _MAX_STEPS_DEFAULT,
        candidates: tuple[str, ...] | None = None,
    ) -> TransactionResult:
        """Adaptive planning loop, not an exhaustive search: on every
        round, NegotiationPlanner re-ranks from live state to pick the
        single best next affiliate, and TerminalStateEvaluator -- fully
        deterministic, no LLM involved -- decides whether that round ended
        the transaction. The LLM is consulted only when the evaluator has
        already established the round is NOT terminal, and only to decide
        how to keep negotiating (contact someone else, retry, ask for more
        information); it has no vocabulary to end the transaction itself,
        so it structurally cannot override a terminal condition the
        runtime already proved (e.g. objective achieved, Nash equilibrium
        reached) -- the root cause of the "walks every affiliate anyway"
        bug this replaces."""
        transaction_id = uuid4().hex
        started = time.time()

        # Pre-commit negotiation gate (TransitionGate,
        # kernel/society/transition_gate.py): the gate already knows
        # exactly which actor(s) must agree (an explicit counterparty on
        # the contested resource) -- when the caller supplies candidates
        # directly there is no affiliate search to run, and no notion of
        # "relevant societies" to resolve it from.
        if candidates is not None:
            relevant_society_ids: set[str] = set()
        else:
            relevant_society_ids = self._relevant_societies(originating_actor_id, objective)
            candidates = self._eligible_affiliates(originating_actor_id, relevant_society_ids)

        await self._stream_event(
            transaction_id,
            {
                "type": "transaction_started",
                "transaction_id": transaction_id,
                "originating_actor_id": originating_actor_id,
                "objective": objective,
                "societies_involved": sorted(relevant_society_ids),
                "eligible_affiliates": list(candidates),
            },
        )

        state = TransactionState(
            originating_actor_id=originating_actor_id,
            objective=objective,
            candidates=candidates,
            max_steps=max_steps,
            started_at=started,
        )
        evaluator = TerminalStateEvaluator(policy_gate=self._policy_gate)
        planner = NegotiationPlanner(trust_lookup=self._current_trust)
        steps: list[TransactionStep] = []

        terminal = evaluator.evaluate(state)
        while not terminal.is_terminal:
            target = state.pending_target or planner.select_best_affiliate(state)
            if target is None:
                terminal = TerminalState(True, TransactionStatus.TERMINATED, "no eligible affiliates remain")
                break
            state.pending_target = None

            step_number = len(steps) + 1
            message = self._build_message(originating_actor_id, objective, target, steps)
            try:
                trace = await self._send_message(originating_actor_id, target, message)
            except Exception as exc:
                logger.error(
                    "execute: unrecoverable error negotiating with %r: %s",
                    target,
                    exc,
                    exc_info=True,
                )
                state.error = str(exc)
                terminal = evaluator.evaluate(state)
                break

            self._update_trust_from_trace(originating_actor_id, target, trace)
            self._publish_belief_perturbation(target, trace)

            await self._stream_event(
                transaction_id,
                {
                    "type": "negotiation_trace",
                    "transaction_id": transaction_id,
                    "step_number": step_number,
                    "target_actor_id": target,
                    "message": message,
                    "trace": _asdict_shallow(trace) if trace is not None else None,
                },
            )

            pending_remaining = [c for c in state.remaining_candidates() if c != target]
            strategic_context = self._strategic_context(originating_actor_id, target, trace, pending_remaining)
            state.record_contact(target, trace, strategic_context)

            terminal = evaluator.evaluate(state)
            if terminal.is_terminal:
                next_action = (
                    "complete_objective" if terminal.status is TransactionStatus.COMPLETED else "terminate_transaction"
                )
                steps.append(
                    TransactionStep(
                        step_number=step_number,
                        target_actor_id=target,
                        message=message,
                        trace=trace,
                        next_action=next_action,
                        next_action_reason=terminal.reason,
                        strategic_context=strategic_context,
                    )
                )
                await self._stream_event(
                    transaction_id,
                    {
                        "type": "step_completed",
                        "transaction_id": transaction_id,
                        "step_number": step_number,
                        "target_actor_id": target,
                        "next_action": next_action,
                        "reason": terminal.reason,
                        "strategic_context": strategic_context,
                    },
                )
                break

            # The runtime has NOT proven a terminal condition for this
            # round -- only now is the LLM consulted, and only to decide
            # HOW to keep negotiating (its vocabulary excludes ending the
            # transaction; see _STRATEGIC_ACTIONS).
            decision = await self._decide_next_action(
                originating_actor_id,
                objective,
                steps,
                target,
                trace,
                state.remaining_candidates(),
                strategic_context,
            )
            steps.append(
                TransactionStep(
                    step_number=step_number,
                    target_actor_id=target,
                    message=message,
                    trace=trace,
                    next_action=decision["next_action"],
                    next_action_reason=decision["reason"],
                    strategic_context=strategic_context,
                )
            )
            await self._stream_event(
                transaction_id,
                {
                    "type": "step_completed",
                    "transaction_id": transaction_id,
                    "step_number": step_number,
                    "target_actor_id": target,
                    "next_action": decision["next_action"],
                    "reason": decision["reason"],
                    "strategic_context": strategic_context,
                },
            )

            if decision["next_action"] == "contact_another_affiliate":
                suggested = decision.get("target_actor_id")
                if suggested and suggested in state.remaining_candidates():
                    state.pending_target = suggested
                # else: leave unset -- the planner re-ranks and picks the
                # best remaining affiliate at the top of the next round.
            else:
                # "request_additional_information" / "continue_negotiation":
                # re-contact the same affiliate next round.
                state.pending_target = target

            terminal = evaluator.evaluate(state)

        status = terminal.status or TransactionStatus.TERMINATED
        final_outcome = terminal.reason

        # A whole-transaction outcome, on top of the per-round updates
        # already applied above -- e.g. an affiliate contacted early who
        # gave an ambiguous "acted" trace still shares in the eventual
        # completion/termination outcome, per the spec's "successful
        # negotiations; failed negotiations" trust factors.
        for target_actor_id in dict.fromkeys(state.contacted):
            self._apply_trust_outcome(
                originating_actor_id,
                target_actor_id,
                goal_achieved=status is TransactionStatus.COMPLETED,
            )

        result = TransactionResult(
            transaction_id=transaction_id,
            originating_actor_id=originating_actor_id,
            objective=objective,
            status=status,
            steps=tuple(steps),
            societies_involved=tuple(sorted(relevant_society_ids)),
            affiliates_contacted=tuple(dict.fromkeys(state.contacted)),
            duration_ms=(time.time() - started) * 1000,
            final_outcome=final_outcome,
        )
        await self._stream_event(
            transaction_id,
            {
                "type": "transaction_completed",
                "transaction_id": transaction_id,
                "status": status.value,
                "final_outcome": final_outcome,
                "steps_taken": len(steps),
                "affiliates_contacted": list(result.affiliates_contacted),
                "duration_ms": round(result.duration_ms, 2),
            },
        )

        # Closes a real gap: before this, TransactionCoordinator negotiated
        # entirely through sr.tick() (see _send_message below), bypassing
        # PlanetaryRuntime.execute_actor_request/_finalize_actor_execution
        # entirely, so _record_decision (decision_kind="negotiation") was
        # never reachable from this path and negotiations wrote zero
        # Timeline DECISION entries. This is deliberately NOT the same
        # scope shape _build_negotiation_trace produces (that reads
        # single-actor plan-step outcomes like EvaluateStrategy/
        # CompeteForResource that don't exist in this round-based
        # transaction flow) -- only fields genuinely available here are
        # populated; the rest (utility_evaluation, candidate_strategies)
        # are left at _record_decision's own honest defaults rather than
        # fabricated.
        # self._planetary is None in _ScriptedCoordinator-style unit tests
        # (see tests/unit/test_transaction_coordinator.py) that exercise
        # this control flow without a live PlanetaryRuntime — guarded the
        # same non-fatal way every other optional-collaborator use in this
        # class already is (e.g. _stream_event's nats_client checks).
        if self._planetary is not None:
            last_trace = steps[-1].trace if steps and steps[-1].trace is not None else None
            self._planetary._record_decision(
                originating_actor_id,
                {
                    "reason": final_outcome,
                    "negotiation_outcome": status.value,
                    "is_cooperative": True,
                    "is_competitive": False,
                    "colleagues_involved": tuple(dict.fromkeys(state.contacted)),
                },
                execution_id=transaction_id,
                correlation_id=transaction_id,
                causation_id=last_trace.trace_id if last_trace is not None else "",
            )
        return result

    async def execute_for_gate(
        self,
        actor_id: str,
        counterparties: tuple[str, ...],
        transition_summary: str,
        *,
        max_steps: int = _MAX_STEPS_DEFAULT,
    ) -> TransactionResult:
        """Named entry point for TransitionGate-triggered negotiation
        (kernel/society/transition_gate.py): the gate already identified
        exactly who must agree (an explicit counterparty on a contested
        resource, e.g. a co-owner whose consent a shared budget declares
        as required) — there is no affiliate search to run here, unlike
        the general execute() entry point. Reuses execute()'s real
        negotiate/decide/terminal loop unchanged via its candidates
        override; invents no second negotiation implementation."""
        return await self.execute(
            actor_id,
            transition_summary,
            max_steps=max_steps,
            candidates=counterparties,
        )

    # ── Step 1: Identify Relevant Societies ─────────────────────────────

    def _relevant_societies(self, actor_id: str, objective: str) -> set[str]:
        home_societies = self._planetary._societies_for(actor_id)
        home_ids = {sr.society.society_id for sr in home_societies}
        if len(home_societies) <= 1:
            return home_ids

        keywords = {w.lower() for w in objective.split() if len(w) > 2}
        matched = {
            sr.society.society_id
            for sr in home_societies
            if keywords & {e.lower() for e in sr.society.subscribed_events}
        }
        # A keyword filter that eliminates every home society the actor
        # belongs to is a sign the filter isn't informative here, not
        # that no society is relevant — fall back to all of them rather
        # than stranding the transaction with zero participants.
        return matched or home_ids

    # ── Steps 2-3: Resolve + Filter Eligible Affiliations ───────────────

    def _affiliation_manager_for(self, actor_id: str) -> Any | None:
        """The actor's own AffiliationManager, reached via its
        ActorRuntimeState across whichever home society currently holds it.
        Shared by eligibility ranking and trust-outcome updates so both
        read/write the same Affiliation.trust_level records."""
        pr = self._planetary
        home_societies = pr._societies_for(actor_id)
        state = next(
            (sr.get_actor(actor_id) for sr in home_societies if sr.get_actor(actor_id) is not None),
            None,
        )
        return getattr(getattr(state, "actor_runtime", None), "affiliations", None) if state else None

    def _eligible_affiliates(self, actor_id: str, relevant_society_ids: set[str]) -> tuple[str, ...]:
        pr = self._planetary
        affiliations = self._affiliation_manager_for(actor_id)
        if affiliations is None:
            return ()

        active = affiliations.active() if hasattr(affiliations, "active") else affiliations.all()
        eligible: list[tuple[float, str]] = []
        seen: set[str] = set()
        for affiliation in active:
            # "member_of" affiliations (mirrored society memberships — see
            # PlanetaryRuntime._mirror_membership_affiliation) point at a
            # society_id, not a negotiable actor: this loop is only for
            # actor-to-actor eligibility, so skip them explicitly rather
            # than relying on the society-overlap check below to no-op.
            if getattr(affiliation, "affiliation_type", "") == "member_of":
                continue
            target_id = getattr(affiliation, "target_id", "")
            if not target_id or target_id == actor_id or target_id in seen:
                continue
            seen.add(target_id)

            target_societies = {sr.society.society_id for sr in pr._societies_for(target_id)}
            if relevant_society_ids and not (target_societies & relevant_society_ids):
                continue

            decision = pr.resolve_communication(actor_id, target_id)
            if not decision.allowed:
                continue

            trust = getattr(affiliation, "trust_level", 0.5)
            eligible.append((trust, target_id))

        # Higher-trust affiliates first — a reasonable, policy-driven
        # (not hardcoded-per-actor) default ordering for who gets
        # contacted first; the LLM can still pick a different one via
        # "contact_another_affiliate"'s target_actor_id.
        eligible.sort(key=lambda pair: pair[0], reverse=True)
        return tuple(target_id for _, target_id in eligible)

    # ── Steps 4-7: Send Grounded Message, Affiliate Ticks, Trace ────────

    def _build_message(
        self,
        originating_actor_id: str,
        objective: str,
        target_actor_id: str,
        prior_steps: list[TransactionStep],
    ) -> str:
        context_snippets = self._grounding_context(originating_actor_id, objective) + self._somatic_context(objective)
        history = "; ".join(
            f"round {s.step_number} with {s.target_actor_id}: {s.trace.execution_outcome if s.trace else 'no response'}"
            for s in prior_steps[-3:]
        )
        parts = [
            f"Actor {originating_actor_id!r} is pursuing this objective: {objective!r}.",
            f"You ({target_actor_id!r}) are an affiliate being asked to participate.",
        ]
        if history:
            parts.append(f"Transaction so far: {history}.")
        if context_snippets:
            parts.append(f"Relevant known context: {'; '.join(context_snippets)}.")
        parts.append(
            "If one of your available actions directly addresses this, take it now "
            "and report the outcome, rather than only investigating further."
        )
        return " ".join(parts)

    def _grounding_context(self, originating_actor_id: str, objective: str, limit: int = 3) -> list[str]:
        """Prior conversations/negotiations/experiences relevant to this
        objective — the retrieval-before-LLM-invocation grounding the
        architecture spec calls "SittingFace". kernel/pipeline/planning/
        context_engine.py::ContextConstructionEngine is the component that
        actually fulfills that role in this codebase (multi-source:
        CognitiveMemory, timeline, organizational knowledge); reused here
        via PlanetaryRuntime's existing instance rather than building a
        second retrieval path."""
        engine = getattr(self._planetary, "_context_engine", None)
        if engine is None:
            return []
        try:
            return engine.grounding_snippets(originating_actor_id, objective, limit=limit)
        except Exception:
            logger.debug("_grounding_context: lookup failed (non-fatal)", exc_info=True)
            return []

    def _somatic_context(self, objective: str, limit: int = 3) -> list[str]:
        """Chart snippets via the shared SittingFace retriever (cycle-cached)."""
        try:
            from src.monkey_brain.kernel.knowledge.sittingface_retrieval import (
                get_external_knowledge_retriever,
            )

            report = get_external_knowledge_retriever().retrieve_sync(
                objective,
                cycle_id=f"negotiation:{objective[:48]}",
                force=True,
            )
            return [item.content for item in report.items[:limit] if item.content]
        except Exception:
            logger.debug("_somatic_context: lookup failed (non-fatal)", exc_info=True)
            return []

    async def _send_message(
        self,
        originating_actor_id: str,
        target_actor_id: str,
        message: str,
    ) -> NegotiationTrace | None:
        pr = self._planetary
        target_societies = pr._societies_for(target_actor_id)
        if not target_societies:
            return None

        sr = target_societies[0]
        try:
            tick_result = await sr.tick(target_actor_id=target_actor_id, prompt_request={"question": message})
        except Exception as exc:
            # One affiliate's cognitive tick failing must not abort the
            # whole transaction — surface it as a failed trace so the
            # LLM decision step (which sees this trace) can dynamically
            # choose to retry, move to another affiliate, or terminate,
            # rather than the runtime hardcoding that choice here.
            logger.warning(
                "_send_message: tick failed for affiliate %r (non-fatal to the transaction): %s",
                target_actor_id,
                exc,
                exc_info=True,
            )
            return NegotiationTrace(
                actor_id=target_actor_id,
                reasoning_summary="tick raised an exception",
                execution_outcome="failed",
                confidence=0.0,
                explanation=f"{target_actor_id}'s cognitive tick failed: {exc}",
            )

        result = tick_result.actor_execution_result
        if result is None:
            return None

        return self._trace_from_tick_result(target_actor_id, result)

    @staticmethod
    def _trace_from_tick_result(actor_id: str, result: Any) -> NegotiationTrace:
        """Duck-typed against whatever the affiliate's own Actor Runtime
        returns (the same _CognitiveTickResult shape _publish_tick_events
        and _build_negotiation_trace in integration.py already read) —
        never a fixed schema the affiliate has to self-report into."""
        plan = getattr(result, "plan", None) or {}
        goal = plan.get("goal", "") if isinstance(plan, dict) else str(getattr(plan, "goal", ""))
        actions = getattr(result, "actions", None) or []
        outcome = getattr(result, "actual_outcome", None) or getattr(result, "outcome", None) or {}
        achieved = bool(outcome.get("goal_achieved")) if isinstance(outcome, dict) else False
        error = getattr(result, "error", None)
        confidence = max(0.0, min(1.0, 1.0 - float(error))) if isinstance(error, (int, float)) else 0.5

        execution_outcome = "goal_achieved" if achieved else ("acted" if actions else "no_action")
        explanation = (
            f"{actor_id} pursued '{goal}' and took {len(actions)} action(s); "
            f"outcome: {'achieved' if achieved else 'not yet achieved'}."
        )
        return NegotiationTrace(
            actor_id=actor_id,
            reasoning_summary=f"goal={goal!r}, {len(actions)} action(s)",
            observations=getattr(result, "observations", None) or {},
            belief_updates={"belief_updated": bool(getattr(result, "belief_updated", False))},
            execution_outcome=execution_outcome,
            confidence=confidence,
            explanation=explanation,
        )

    # ── World Perturbation Events: reconcile local belief -> Global World State ──

    def _publish_belief_perturbation(self, target_actor_id: str, trace: NegotiationTrace | None) -> None:
        """Publishes a World Perturbation Event when an affiliate's tick
        actually updated its local belief, so PlanetaryRuntime's next cycle
        (see integration.py::_run_cycle's reconciliation pass) can fold it
        into the Global World State -- closing the gap where
        SharedWorld.perturb() only ever applied random simulated noise,
        never the actors' own observed outcomes. Recording the event here
        does not itself mutate SharedWorld (actors never modify it
        directly, per the spec) -- reconciliation happens exclusively in
        the next Planetary Cycle. Non-fatal: a transaction's outcome is
        unaffected if this can't reach the world model.
        """
        if trace is None or not trace.belief_updates.get("belief_updated"):
            return
        world_model = getattr(self._planetary, "_world_model", None)
        if world_model is None:
            return
        try:
            from src.monkey_brain.kernel.society.world import WorldEvent, EventType

            world_model.record_event(
                WorldEvent(
                    event_type=EventType.OBSERVATION,
                    source_actor_id=target_actor_id,
                    description=trace.explanation or f"{target_actor_id} updated its local belief",
                    attributes=dict(trace.belief_updates),
                    confidence=trace.confidence,
                )
            )
        except Exception:
            logger.debug(
                "_publish_belief_perturbation: failed to record world event for %r (non-fatal)",
                target_actor_id,
                exc_info=True,
            )

    # ── Trust: close the outcome -> ranking learning loop ────────────────

    def _update_trust_from_trace(
        self,
        originating_actor_id: str,
        target_actor_id: str,
        trace: NegotiationTrace | None,
    ) -> None:
        """Closes the loop the spec calls "trust evolves through learning":
        _eligible_affiliates ranks by Affiliation.trust_level, so that value
        must move in response to what actually happened this round, not
        stay fixed at whatever it was set to when the affiliation was
        created. Only acts on decisive outcomes -- "acted"/"no_action"
        carry no clear success/failure signal, so trust is left alone
        rather than nudged on ambiguous evidence."""
        if trace is None or trace.execution_outcome not in ("goal_achieved", "failed"):
            return
        self._apply_trust_outcome(
            originating_actor_id,
            target_actor_id,
            goal_achieved=trace.execution_outcome == "goal_achieved",
        )

    def _apply_trust_outcome(
        self,
        originating_actor_id: str,
        target_actor_id: str,
        *,
        goal_achieved: bool,
    ) -> None:
        affiliations = self._affiliation_manager_for(originating_actor_id)
        if affiliations is None:
            return
        try:
            affiliations.update_trust_from_outcome(target_actor_id, goal_achieved=goal_achieved)
        except Exception:
            logger.debug(
                "_apply_trust_outcome: trust update failed for %r -> %r (non-fatal)",
                originating_actor_id,
                target_actor_id,
                exc_info=True,
            )

    def _current_trust(self, originating_actor_id: str, target_actor_id: str) -> float:
        """Live trust read for NegotiationPlanner -- called fresh on every
        planner.select_best_affiliate() call (not the snapshot captured
        when _eligible_affiliates first ranked candidates), so a trust
        change from the round that just finished (_update_trust_from_trace
        above) can change who ranks best before the next affiliate is
        chosen. Missing manager/record reads as neutral (0.0), consistent
        with _eligible_affiliates' own default when a record is absent."""
        affiliations = self._affiliation_manager_for(originating_actor_id)
        if affiliations is None:
            return 0.0
        try:
            return affiliations.get_trust(target_actor_id)
        except Exception:
            logger.debug(
                "_current_trust: lookup failed for %r -> %r (non-fatal)",
                originating_actor_id,
                target_actor_id,
                exc_info=True,
            )
            return 0.0

    # ── Step 8: LLM decides the next action ─────────────────────────────

    def _strategic_context(
        self,
        originating_actor_id: str,
        target_actor_id: str,
        trace: NegotiationTrace | None,
        remaining_candidates: list[str],
    ) -> dict[str, Any] | None:
        """Lightweight game-theoretic grounding for the next-action decision:
        evaluates the five valid next actions as strategies for both the
        originating actor and the just-contacted affiliate (GameTheoryRuntime
        already implements utility evaluation + Nash-equilibrium stability —
        reused here, not reimplemented), and reports whether the
        highest-mutual-utility action is equilibrium-stable. This *informs*
        the LLM's decision prompt; it does not replace the LLM's judgment,
        keeping "no hardcoded negotiation workflow" intact while giving the
        LLM a game-theoretically grounded signal instead of none. Returns
        None (non-fatal) if there's no trace yet or evaluation fails.
        """
        if trace is None:
            return None
        try:
            from src.monkey_brain.kernel.society.game_theory import (
                GameTheoryRuntime,
                Strategy,
                StrategyProfile,
            )

            achieved = trace.execution_outcome == "goal_achieved"
            failed = trace.execution_outcome == "failed"
            has_remaining = bool(remaining_candidates)

            def strategies() -> tuple[Strategy, ...]:
                return (
                    Strategy(
                        "complete_objective",
                        expected_outcome={"progress": 1.0 if achieved else 0.2},
                    ),
                    Strategy(
                        "terminate_transaction",
                        expected_outcome={"progress": 0.0, "cost": 0.1},
                    ),
                    Strategy(
                        "contact_another_affiliate",
                        expected_outcome={
                            "progress": 0.6 if has_remaining else 0.0,
                            "cost": 0.3,
                        },
                    ),
                    Strategy(
                        "request_additional_information",
                        expected_outcome={
                            "progress": 0.3,
                            "cost": 0.4 if failed else 0.2,
                        },
                    ),
                    Strategy(
                        "continue_negotiation",
                        expected_outcome={"progress": 0.4, "cost": 0.3},
                    ),
                )

            originating_profile = StrategyProfile(
                actor_id=originating_actor_id,
                strategies=strategies(),
                preferences={"progress": 1.0, "cost": -0.3},
            )
            # The just-contacted affiliate already spent effort producing
            # `trace` — it "prefers" actions that don't waste that, so it
            # weighs cost more heavily than the originator does.
            affiliate_profile = StrategyProfile(
                actor_id=target_actor_id,
                strategies=strategies(),
                preferences={"progress": 0.4, "cost": -0.8},
            )

            agreement = GameTheoryRuntime().negotiate(
                "next_action",
                (originating_profile, affiliate_profile),
            )
            return {
                "suggested_action": (agreement.chosen_strategy.name if agreement.chosen_strategy else None),
                "equilibrium": agreement.equilibrium,
                "utilities": dict(agreement.utilities),
                "rationale": agreement.rationale,
            }
        except Exception:
            logger.debug(
                "_strategic_context: game-theory evaluation failed (non-fatal)",
                exc_info=True,
            )
            return None

    async def _decide_next_action(
        self,
        originating_actor_id: str,
        objective: str,
        prior_steps: list[TransactionStep],
        last_target: str,
        last_trace: NegotiationTrace | None,
        remaining_candidates: list[str],
        strategic_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Only ever called once TerminalStateEvaluator has already
        established the current round is NOT terminal (see execute()) --
        so this method's job is exclusively *how* to keep negotiating, not
        *whether* to. Its vocabulary (_STRATEGIC_ACTIONS) excludes ending
        the transaction; a parse/provider failure or an out-of-vocabulary
        response fails safe by defaulting to "let the planner pick the
        next affiliate" rather than to termination -- the deterministic
        TerminalStateEvaluator (remaining-candidates / max_steps / timeout
        checks) is what guarantees the transaction still ends."""
        prompt = self._decision_prompt(
            originating_actor_id,
            objective,
            prior_steps,
            last_target,
            last_trace,
            remaining_candidates,
            strategic_context,
        )
        try:
            from src.monkey_brain.kernel.execute.provider.model_backend import (
                get_backend,
            )

            backend = get_backend()
            # ModelBackend.complete() is a real awaited async call now (see
            # model_backend.py's module docstring) -- genuinely cancellable
            # for the Ollama provider, not a synchronous call hidden behind
            # asyncio.to_thread.
            raw = await backend.complete(prompt, system=_DECISION_SYSTEM_PROMPT, max_tokens=400)
            decision = json.loads(_strip_code_fence(raw))
        except Exception:
            logger.warning(
                "_decide_next_action: LLM decision failed for actor %r — defaulting to planner selection",
                originating_actor_id,
                exc_info=True,
            )
            return {
                "next_action": "contact_another_affiliate",
                "reason": "LLM decision unavailable",
                "target_actor_id": None,
                "strategic_context": strategic_context,
            }

        next_action = decision.get("next_action")
        if next_action not in _STRATEGIC_ACTIONS:
            logger.warning(
                "_decide_next_action: invalid next_action %r — defaulting to planner selection",
                next_action,
            )
            return {
                "next_action": "contact_another_affiliate",
                "reason": f"invalid LLM decision {next_action!r} — defaulted to planner selection",
                "target_actor_id": None,
                "strategic_context": strategic_context,
            }

        return {
            "next_action": next_action,
            "reason": str(decision.get("reason") or ""),
            "target_actor_id": decision.get("target_actor_id"),
            "strategic_context": strategic_context,
        }

    @staticmethod
    def _decision_prompt(
        originating_actor_id: str,
        objective: str,
        prior_steps: list[TransactionStep],
        last_target: str,
        last_trace: NegotiationTrace | None,
        remaining_candidates: list[str],
        strategic_context: dict[str, Any] | None = None,
    ) -> str:
        history_lines = [
            f"- round {s.step_number}: contacted {s.target_actor_id}, "
            f"outcome={s.trace.execution_outcome if s.trace else 'no response'}, "
            f"decision={s.next_action} ({s.next_action_reason})"
            for s in prior_steps
        ]
        last_summary = last_trace.explanation if last_trace is not None else f"{last_target} did not respond."
        strategic_note = ""
        if strategic_context and strategic_context.get("suggested_action"):
            stability = "equilibrium-stable" if strategic_context.get("equilibrium") else "not equilibrium-stable"
            strategic_note = (
                f"\nGame-theoretic analysis (advisory, not binding): the highest-mutual-utility "
                f"action given both sides' incentives is {strategic_context['suggested_action']!r} "
                f"({stability}). Use this as one input, not a hardcoded answer.\n"
            )
        return (
            f"Actor {originating_actor_id!r} is executing a transaction toward this objective: "
            f"{objective!r}.\n\n"
            f"History so far:\n" + ("\n".join(history_lines) if history_lines else "(no prior rounds)") + "\n\n"
            f"Latest round result: {last_summary}\n"
            f"Remaining eligible affiliates not yet contacted this transaction: {remaining_candidates}\n"
            f"{strategic_note}\n"
            "The runtime has already determined this transaction is not yet resolved, so decide only "
            "HOW to keep negotiating -- you cannot end the transaction yourself. Respond with ONLY a "
            "JSON object (no prose, no markdown fences) of the shape: "
            '{"next_action": "<one of: contact_another_affiliate, request_additional_information, '
            'continue_negotiation>", '
            '"target_actor_id": "<optional -- required only for contact_another_affiliate if you have '
            'a specific preference, else null to let the planner choose>", '
            '"reason": "<short natural-language reason>"}'
        )

    # ── Step 9: Stream negotiation progress ─────────────────────────────

    async def _stream_event(self, transaction_id: str, event: dict[str, Any]) -> None:
        event = {**event, "timestamp": event.get("timestamp", time.time())}

        from src.monkey_brain.kernel.society.transaction_event_hub import (
            get_transaction_event_hub,
        )

        try:
            await get_transaction_event_hub().publish(transaction_id, event)
        except Exception:
            logger.debug("_stream_event: websocket publish failed (non-fatal)", exc_info=True)

        nats_client = getattr(self._planetary, "_nats_client", None)
        if nats_client is not None:
            try:
                await nats_client.publish(
                    f"monkeybrain.transaction.{transaction_id}",
                    json.dumps(event).encode(),
                )
            except Exception:
                logger.debug("_stream_event: NATS publish failed (non-fatal)", exc_info=True)

        # Real-Time World Changes refactor (Context Stream spec): the WS/
        # NATS fan-out above is live-only (no history, nothing to query
        # later), so without this, TransactionCoordinator negotiations
        # were structurally invisible to ContextConstructionEngine's
        # grounding -- only the older InteractionManager-based negotiation
        # path (SocietyRuntime.route_interaction/respond_to_interaction)
        # ever reached SocietyContextStream. Publish once per actor
        # involved in this event so ContextConstructionEngine._retrieve_
        # context_stream's existing actor-scoped INTERACTION query (no new
        # query needed) picks it up as a "negotiation update" for both the
        # originating actor and the affiliate being negotiated with.
        self._publish_negotiation_context_event(transaction_id, event)

    def _publish_negotiation_context_event(self, transaction_id: str, event: dict[str, Any]) -> None:
        context_stream = getattr(self._planetary, "context_stream", None)
        if context_stream is None:
            return
        actor_ids = {a for a in (event.get("originating_actor_id"), event.get("target_actor_id")) if a}
        if not actor_ids:
            return
        try:
            from src.monkey_brain.kernel.society.context_stream import (
                ContextEvent,
                ContextEventType,
            )

            description = f"Transaction {transaction_id}: {event.get('type', 'update')}"
            # transaction_id already spans the whole negotiation — reuse it
            # as correlation_id rather than minting a new one. causation_id
            # is the specific round's NegotiationTrace.trace_id when this
            # event carries one (negotiation_trace events); other event
            # types (transaction_started/step_completed/transaction_
            # completed) have no single upstream record to point at, so
            # it's left empty rather than fabricated.
            trace = event.get("trace")
            causation_id = trace.get("trace_id", "") if isinstance(trace, dict) else ""
            for actor_id in actor_ids:
                context_stream.publish(
                    ContextEvent(
                        event_type=ContextEventType.INTERACTION,
                        actor_id=actor_id,
                        description=description,
                        payload={"transaction_id": transaction_id, **event},
                        provenance="society:transaction",
                        correlation_id=transaction_id,
                        causation_id=causation_id,
                    )
                )
        except Exception:
            logger.debug(
                "_publish_negotiation_context_event: publish failed (non-fatal)",
                exc_info=True,
            )


_DECISION_SYSTEM_PROMPT = (
    "You are the reasoning core of an autonomous actor negotiating a transaction inside a "
    "multi-actor simulation. You never invent business facts — you only decide, from the "
    "transaction state given to you, which of the three allowed strategic actions to take next. "
    "The runtime, not you, decides when the transaction is over -- it only asks you this question "
    "when it has already determined the transaction is not yet resolved. "
    "Always respond with exactly one JSON object and nothing else."
)


def _asdict_shallow(obj: Any) -> dict[str, Any]:
    """Shallow dataclass->dict, avoiding a dataclasses import dependency
    cycle concern for this module's few call sites."""
    import dataclasses

    return dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else dict(obj)
