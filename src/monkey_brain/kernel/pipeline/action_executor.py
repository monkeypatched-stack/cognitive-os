"""ActionExecutor — default execution through the CapabilityBus.

Plan steps → Actions → CapabilityBus → ActionOutcomes

The executor:
1. Converts plan steps to typed Actions
2. Discovers capabilities via the CapabilityBus
3. Invokes each capability
4. Collects ActionOutcomes
5. Produces an ExecutionResult

If no CapabilityBus is available, actions are simulated (pass-through).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable

from src.monkey_brain.kernel.pipeline.execution import (
    Action, ActionOutcome, ExecutionResult,
)
from src.monkey_brain.kernel.society.transition_gate import ProposedTransition, TransitionGate

logger = logging.getLogger("agentos.pipeline.action_executor")

_CAPABILITY_TIMEOUT_SECONDS = float(os.getenv("CAPABILITY_TIMEOUT_SECONDS", "120"))


class ActionExecutor:
    """Default execution engine — invokes capabilities through the CapabilityBus.

    The runtime depends only on the ExecutionEngine protocol.
    This is the default implementation that discovers and invokes capabilities.
    """

    def __init__(
        self,
        capability_bus: Any = None,
        failure_rate: float = 0.0,
        context_projector: Callable[[dict, dict], None] | None = None,
        context_stream: Any = None,
        domain_event_resolver: Callable[[str, bool, Any], str | None] | None = None,
        pre_execute_hook: Callable[[dict], None] | None = None,
        propose_transition: Callable[[Action, dict], ProposedTransition | None] | None = None,
        transition_gate: TransitionGate | None = None,
        connectivity_check: Callable[[str], tuple[bool, str, str]] | None = None,
        edge_governance: Any = None,
    ) -> None:
        self._capability_bus = capability_bus
        self._failure_rate = failure_rate
        # Edge-local state and governance layer: consulted ONLY when
        # connectivity_check above has already decided a capability
        # cannot proceed for lack of reachable central authority. A
        # kernel.edge.local_governance.LocalGovernanceEvaluator (or
        # anything with the same .evaluate() signature); None (the
        # default) preserves the exact prior behavior (unconditional
        # refusal when disconnected) for every existing caller.
        self._edge_governance = edge_governance
        # Cloud/Edge Actor Convergence, Section 11/31: offline-safety gate,
        # evaluated BEFORE the negotiation gate below (a capability this
        # node can't safely reach authority for is refused before it's
        # even worth asking whether it needs negotiation). None (the
        # default) preserves exactly the prior behavior for every existing
        # caller — this only takes effect for a caller that explicitly
        # opts in (kernel/pipeline/offline_safety.py::make_connectivity_check,
        # wired by the edge runtime). See offline_safety.py for the
        # capability classification and connectivity assessment; this
        # class only calls the hook and turns a refusal into a real
        # ActionOutcome — it has no offline-safety policy of its own.
        self._connectivity_check = connectivity_check
        # Pre-commit negotiation gate: same "vertical injects, executor
        # stays domain-agnostic" principle as pre_execute_hook/
        # context_projector below. A vertical that wants a class of
        # action gated opts in by supplying propose_transition (builds a
        # ProposedTransition from an action it recognizes, e.g.
        # OrderCreation/Payment, else None) + transition_gate (the shared
        # TransitionGate.evaluate() — always the SAME instance/logic, per
        # "one authoritative decision point", never a capability's own
        # negotiation check). None (the default) preserves exactly the
        # prior behavior — no gate, no pause — for any vertical that
        # doesn't wire one in.
        self._propose_transition = propose_transition
        self._transition_gate = transition_gate
        # Qualification Gap Closure, Phase 9: called once per execute()
        # call, before the main loop, given the real context dict --
        # domain-specific tick-scoped setup (e.g. grocery's real shared
        # budget entity, created from real request text) that every step
        # of THIS tick needs to see regardless of which step runs first.
        # Same "vertical injects, executor stays domain-agnostic"
        # principle as context_projector/domain_event_resolver below.
        self._pre_execute_hook = pre_execute_hook
        # Which result keys become which context keys is vertical-specific
        # knowledge (e.g. grocery's "selected" -> "selected_product"); a
        # domain-agnostic executor has no default mapping of its own.
        self._context_projector = context_projector
        # MB-3051 Context Propagation: every REAL action outcome gets
        # published here — the single chokepoint every capability call
        # already funnels through, so this covers every genuine business
        # event without grocery.py/commerce.py/logistics.py/finance.py/
        # supply_chain.py/support.py needing to know ContextStream exists
        # at all. None (the default) preserves the exact prior behavior
        # for every existing caller that hasn't wired one in.
        self._context_stream = context_stream
        # True Multi-Actor Coordination: (capability_name, success, result)
        # -> a real business event name to tag onto the published
        # ContextEvent's payload["domain_event"], so a Society's
        # subscribed_events can match on it. result is included (not just
        # success) because some capabilities' own success=True still needs
        # a different event depending on what actually happened — e.g.
        # OrderCreationCapability reports success=True even when every
        # item was backordered (MB-3031's existing partial-fulfillment
        # design), which is a real InventoryUnavailable, not OrderCreated.
        # Same "vertical-specific knowledge injected, never hardcoded
        # here" principle as context_projector above.
        self._domain_event_resolver = domain_event_resolver

    async def execute(
        self,
        actions: tuple[Action, ...],
        context: Any = None,
        *,
        execution_graph: Any = None,
    ) -> ExecutionResult:
        """Execute a sequence of actions.

        Step results are accumulated into the context dict so later
        steps can reference data from earlier steps.

        Multi-Actor Execution Handoff: async — see execution.py::
        ExecutionEngine Protocol's own docstring for why. Every existing
        (sync) capability keeps working unchanged; _execute_action only
        awaits a capability's own handle() when that capability itself is
        async (e.g. AskActorCapability's real NATS request/reply)."""
        if not actions:
            return ExecutionResult(goal_achieved=True)

        from src.monkey_brain.kernel.security_boundary import ensure_governed

        async def _run() -> ExecutionResult:
            return await self._execute_actions(actions, context, execution_graph=execution_graph)

        # NOT threading actions[0].correlation_id (execution_id) through as
        # operation_id here, despite it being the stable per-tick id: a
        # RESUMED execution (meta.resume_execution_id) deliberately reuses
        # that SAME execution_id and re-enters this exact execute() method
        # again, but SecurityOperation's ledger.create() (inside
        # run_governed_mutation) unconditionally rejects a repeated
        # operation_id with DuplicateSecurityOperation -- attempt #2+ of
        # the SAME operation_id is only ever sanctioned through
        # retry_execution_attempt(), which nothing here calls. Confirmed
        # live: threading execution_id in broke every resume/restart
        # scenario test (test_checkpoint_restart.py, test_human_approval.py,
        # test_compound_disruption.py) with "duplicate operation". Making
        # this batch-level call correctly idempotent across resumes would
        # mean this method detecting "operation_id already exists in the
        # ledger" and switching to retry_execution_attempt() itself --
        # a commitment/reconciliation-semantics change, out of scope here.
        return await ensure_governed(
            "action_executor.execute",
            "actions",
            _run,
        )

    async def _execute_actions(
        self,
        actions: tuple[Action, ...],
        context: Any = None,
        *,
        execution_graph: Any = None,
    ) -> ExecutionResult:
        """Inner loop — only reached from execute() after the commitment gate."""

        if self._pre_execute_hook is not None and isinstance(context, dict):
            try:
                self._pre_execute_hook(context)
            except Exception:
                logger.warning("[executor] pre_execute_hook raised, continuing without it", exc_info=True)

        from src.monkey_brain.kernel.compile import _obs

        _obs.start_span("execution", component="pipeline", action_count=len(actions))

        outcomes = []
        start_time = time.time()
        event_publish_ms = 0.0
        # Execution-time dependency gating (mirrors PlanStep.depends_on /
        # Action.depends_on -- see execution.py's own field docstring): a
        # step_index only ever enters this set once its own action has
        # genuinely succeeded, so an action whose depends_on references an
        # index that failed, was permission-denied (never reached this
        # loop at all), or hasn't run yet stays blocked. Deliberately
        # fail-closed (unlike RiskEngine._dependencies_satisfied's
        # optimistic treatment of an absent reference, which is only
        # estimating a probability) -- real side effects are at stake
        # here, not a probability estimate.
        succeeded_step_indices: set[int] = set()

        from src.monkey_brain.kernel.execute.graph import NodeState
        from src.monkey_brain.kernel.pipeline.graph_execution import (
            node_id_for_action,
            order_actions_by_graph,
            runnable_actions,
            apply_runtime_projections,
        )
        if execution_graph is not None:
            actions = order_actions_by_graph(actions, execution_graph)
            if isinstance(context, dict):
                context["_compiled_execution_graph"] = execution_graph

        # Checkpoint/restart (kernel/pipeline/execution_checkpoint_store.py):
        # every action in one execute() call shares the same tick's
        # execution_id (Action.correlation_id). A caller resuming a prior
        # attempt (meta.resume_execution_id, see cognitive_actor.py) reuses
        # that SAME execution_id, so any step this exact id already
        # completed shows up here and must not be re-dispatched to its
        # capability -- that would repeat a real, possibly irreversible,
        # side effect (charging a wallet, reserving stock a second time).
        # A fresh execution_id (the overwhelming common case) simply has
        # no checkpoint yet -- one Redis GET that returns nothing, then
        # this behaves exactly as it always has.
        execution_id = actions[0].correlation_id
        completed_steps: dict[int, dict[str, Any]] = {}
        if execution_id:
            from src.monkey_brain.kernel.pipeline.execution_checkpoint_store import load_execution_checkpoint
            checkpoint = load_execution_checkpoint(execution_id)
            if checkpoint is not None:
                completed_steps = {int(k): v for k, v in checkpoint.completed_steps.items()}
        if isinstance(context, dict) and execution_id:
            context["_execution_id"] = execution_id
        source_plan = context.get("_source_plan") if isinstance(context, dict) else None
        plan_dict = self._plan_dict_from_actions(
            actions, goal=context.get("question", "") if isinstance(context, dict) else "",
            source_plan=source_plan,
        )

        # Human approval / pause-resume (Qualification Gap Closure, Phase
        # 3): a resumed tick (meta.resume_execution_id) whose pending
        # approval has already been decided threads that real decision +
        # the EXACT proposed_action a capability originally put forward
        # into context, so a capability commits precisely what was shown
        # for approval — never a freshly recomputed candidate, even if the
        # world moved again between the pause and the decision. Nothing
        # here happens for the overwhelming common case (no execution_id,
        # or one with no pending approval) beyond one Redis GET.
        resolved_approval = None
        if execution_id:
            from src.monkey_brain.kernel.pipeline.approval_store import load_pending_approval
            pending = load_pending_approval(execution_id)
            if pending is not None and pending.decided is not None:
                resolved_approval = pending
        waiting_for_human = False

        # Pre-commit negotiation gate resume (mirrors resolved_approval
        # immediately above): a resumed tick whose pending negotiation has
        # already been decided threads that real decision — agreed or
        # rejected — into context so the gate check below lets an agreed
        # transition proceed to the capability for the first time (it was
        # never invoked while the negotiation was pending) or honors a
        # rejection without ever invoking it at all.
        resolved_negotiation = None
        if execution_id:
            from src.monkey_brain.kernel.pipeline.negotiation_store import load_pending_negotiation
            pending_negotiation = load_pending_negotiation(execution_id)
            if pending_negotiation is not None and pending_negotiation.decided is not None:
                resolved_negotiation = pending_negotiation
        waiting_for_negotiation = False

        # Payment confirmation pause-resume (mirrors resolved_approval/
        # resolved_negotiation immediately above): a real, two-phase
        # PaymentProvider (kernel/domains/payment_provider.py — e.g.
        # RazorpayUPIProvider) can't hold funds synchronously, since the
        # payer has to approve in their own UPI app first. A capability
        # that called reserve() and got back PENDING_AUTHORIZATION opts a
        # step into this pause by returning
        # {"requires_payment_confirmation": True, "reservation_id": ...,
        # "provider_name": ..., ...} in its own result dict, the same
        # capability-driven opt-in shape requires_approval already uses —
        # ActionExecutor never calls reserve()/capture() itself, same as
        # it never decides what "approval" or "negotiation" mean.
        resolved_payment = None
        if execution_id:
            from src.monkey_brain.kernel.pipeline.payment_store import load_pending_payment
            pending_payment = load_pending_payment(execution_id)
            if pending_payment is not None and pending_payment.decided is not None:
                resolved_payment = pending_payment
        waiting_for_payment = False

        scheduled_indices: set[int] = set()
        while True:
            if execution_graph is not None:
                pending = [a for a in actions if a.step_index not in scheduled_indices]
                if not pending:
                    break
                batch = runnable_actions(
                    tuple(pending), execution_graph, succeeded_step_indices,
                    completed_or_terminal=scheduled_indices,
                )
                next_actions = batch if batch else pending[:1]
            else:
                remaining = [a for a in actions if a.step_index not in scheduled_indices]
                if not remaining:
                    break
                next_actions = remaining[:1]

            for action in next_actions:
                scheduled_indices.add(action.step_index)
                node_id = node_id_for_action(action, execution_graph) if execution_graph else None
                if node_id:
                    execution_graph.mark_running(node_id)
                checkpointed = completed_steps.get(action.step_index) if action.step_index >= 0 else None
                missing = [dep for dep in action.depends_on if dep not in succeeded_step_indices] if action.depends_on else []
                if checkpointed is not None:
                    # Already completed in an earlier attempt at this same
                    # execution_id -- replay its real, stored outcome (never
                    # invoke the capability again).
                    outcome = ActionOutcome(
                        action_id=checkpointed.get("action_id", action.action_id),
                        success=bool(checkpointed.get("success", False)),
                        result=checkpointed.get("result"),
                        error=checkpointed.get("error", ""),
                        latency_ms=float(checkpointed.get("latency_ms", 0.0) or 0.0),
                        metadata={"resumed_from_checkpoint": True},
                    )
                    if outcome.success and action.step_index >= 0:
                        succeeded_step_indices.add(action.step_index)
                elif missing:
                    outcome = ActionOutcome(
                        action_id=action.action_id,
                        success=False,
                        result={"blocked_by_dependency": missing[0]},
                        error=f"blocked: dependency step {missing[0]} did not succeed",
                        latency_ms=0.0,
                    )
                    # This branch never reaches _execute_action/capability.
                    # handle() at all -- the capability is never invoked, so
                    # this is the one real "blocked" boundary, separate from
                    # the success/failed/rejected classification inside
                    # _execute_action below.
                    from src.monkey_brain.kernel.compile import _obs
                    _obs.counter("capability.calls.total", capability=action.capability, status="blocked")
                else:
                    if (
                        resolved_approval is not None
                        and action.step_index >= 0
                        and action.step_index == resolved_approval.step_index
                        and isinstance(context, dict)
                    ):
                        context["approval_decision"] = resolved_approval.decided
                        context["approval_pending_candidate"] = resolved_approval.proposed_action

                    if (
                        resolved_negotiation is not None
                        and action.step_index >= 0
                        and action.step_index == resolved_negotiation.step_index
                        and isinstance(context, dict)
                    ):
                        context["negotiation_decision"] = resolved_negotiation.decided

                    if (
                        resolved_payment is not None
                        and action.step_index >= 0
                        and action.step_index == resolved_payment.step_index
                        and isinstance(context, dict)
                    ):
                        # True = captured (webhook confirmed the payer
                        # approved and capture() succeeded), False = released/
                        # failed/expired -- a resuming capability reads this
                        # instead of calling reserve() again, same "commit
                        # exactly what was already decided, never a freshly
                        # recomputed candidate" contract approval_decision
                        # gives above.
                        context["payment_decision"] = resolved_payment.decided
                        context["payment_reservation_id"] = resolved_payment.reservation_id
                        context["payment_provider_name"] = resolved_payment.provider_name

                    # PROPOSE -> CHECK, before the capability is ever invoked
                    # (the actual architectural fix: no capability that
                    # mutates shared state runs until this gate has had a
                    # chance to require negotiation first). transition is
                    # None for any action the vertical doesn't recognize as
                    # shared-state-mutating, or when no gate is wired in at
                    # all -- behaves exactly as before for those.
                    gate_decision = None
                    gated_outcome = None
                    local_policy_decision = None
                    if self._connectivity_check is not None:
                        allowed, waiting_state, reason = self._connectivity_check(action.capability)
                        if not allowed:
                            from src.monkey_brain.kernel.compile import _obs
                            # Edge Local Governance: the blunt connectivity
                            # gate above only knows "can this node reach
                            # central authority" -- it has no notion of
                            # already-issued, still-valid authority cached
                            # locally. Before accepting its refusal as
                            # final, ask whether a signed, verified,
                            # fresh control-plane snapshot already covers
                            # this exact (principal, capability, resource)
                            # -- never a fresh trust decision made here,
                            # only a check of whether one was already made
                            # centrally and is still safe to use.
                            edge_outcome = None
                            if self._edge_governance is not None:
                                from src.monkey_brain.kernel.trusted_auth import get_trusted_auth
                                principal = get_trusted_auth().principal_id
                                # Edge gap-closure (Section 4): a raw,
                                # already-parsed delegation chain from the
                                # live message boundary (kernel/edge/
                                # delegation_message.py, via context
                                # ["delegation_chain"]) is handed to the
                                # SAME LocalGovernanceEvaluator.evaluate()
                                # every other edge decision uses -- it
                                # independently re-verifies the chain
                                # itself (never trusts that a message
                                # boundary already did so), exactly like
                                # every other consumer of a delegation
                                # chain in this codebase.
                                delegation_chain = (
                                    context.get("delegation_chain", ()) if isinstance(context, dict) else ()
                                )
                                edge_outcome = self._edge_governance.evaluate(
                                    principal=principal,
                                    action=f"capability.{action.capability}",
                                    resource=action.capability,
                                    authenticated_principal=principal,
                                    delegation_chain=delegation_chain,
                                )
                            if edge_outcome is not None and not edge_outcome.escalate:
                                from src.monkey_brain.kernel.edge.local_governance import to_policy_decision
                                _obs.counter(
                                    "edge.governance.decision", origin=edge_outcome.origin.value,
                                    allowed=str(edge_outcome.allowed), capability=action.capability,
                                )
                                if edge_outcome.allowed:
                                    local_policy_decision = to_policy_decision(edge_outcome)
                                else:
                                    gated_outcome = ActionOutcome(
                                        action_id=action.action_id, success=False,
                                        result={
                                            "waiting_state": "", "capability": action.capability,
                                            "governance_origin": edge_outcome.origin.value,
                                        },
                                        error=edge_outcome.reason, latency_ms=0.0,
                                    )
                            else:
                                # No edge governance wired in, or it could
                                # not establish a confident local decision
                                # (escalate=True) -- exactly the prior
                                # refusal behavior, unchanged.
                                if edge_outcome is not None:
                                    _obs.counter(
                                        "edge.governance.decision", origin=edge_outcome.origin.value,
                                        allowed="false", capability=action.capability,
                                    )
                                # Refused before the capability is ever invoked --
                                # same "never call handle() for a gated action"
                                # contract the negotiation gate below establishes.
                                # Never reached for TRANSITION_GATE gating below
                                # since gated_outcome is already set.
                                gated_outcome = ActionOutcome(
                                    action_id=action.action_id, success=False,
                                    result={"waiting_state": waiting_state, "capability": action.capability},
                                    error=reason, latency_ms=0.0,
                                )
                                _obs.counter("offline_safety.blocked.total", waiting_state=waiting_state, capability=action.capability)
                    if (
                        gated_outcome is None
                        and self._propose_transition is not None
                        and self._transition_gate is not None
                        and isinstance(context, dict)
                    ):
                        transition = self._propose_transition(action, context)
                        if transition is not None:
                            kg = context.get("knowledge_graph")
                            gate_decision = self._transition_gate.evaluate(transition, kg)
                            # Lemon metrics (previously zero telemetry on this
                            # gate — action_executor.py's own comment above
                            # already establishes this runs "before the
                            # capability is ever invoked"; the outcome tag is
                            # the real, just-computed GateDecision, never
                            # inferred after the fact).
                            from src.monkey_brain.kernel.compile import _obs
                            _obs.counter(
                                "transition_gate.evaluations.total",
                                allow=str(gate_decision.allow), requires_negotiation=str(gate_decision.requires_negotiation),
                            )
                            negotiation_decision = context.get("negotiation_decision")
                            if gate_decision.requires_negotiation and negotiation_decision is None:
                                # NEGOTIATE: pause here. capability.handle()
                                # is never called for this action -- this is
                                # what actually prevents "mutate first,
                                # negotiate after".
                                gated_outcome = ActionOutcome(
                                    action_id=action.action_id, success=False,
                                    result={
                                        "requires_negotiation": True,
                                        "proposed_transition": transition.to_dict(),
                                        "counterparties": list(gate_decision.counterparties),
                                        "reason": gate_decision.reason,
                                        "gate_decision": gate_decision.to_dict(),
                                    },
                                    error=f"negotiation required before commit: {gate_decision.reason}",
                                    latency_ms=0.0,
                                )
                            elif gate_decision.requires_negotiation and negotiation_decision is False:
                                # REJECT: negotiation concluded without
                                # agreement -- abort, never invoke the
                                # capability, no state mutation.
                                gated_outcome = ActionOutcome(
                                    action_id=action.action_id, success=False,
                                    result={
                                        "negotiation_rejected": True,
                                        "proposed_transition": transition.to_dict(),
                                        "counterparties": list(gate_decision.counterparties),
                                        "gate_decision": gate_decision.to_dict(),
                                    },
                                    error=f"negotiation rejected: {gate_decision.reason}",
                                    latency_ms=0.0,
                                )
                            # else: allow=True, or an accepted negotiation
                            # (negotiation_decision is True) -- fall through
                            # to COMMIT via the real capability below.

                    if gate_decision is not None:
                        # SECURITY (Doot audit P1-7): durable, execution_id-
                        # correlated record of the policy/consent/negotiation
                        # decision itself -- reuses the SAME DECISION timeline
                        # audit_trail.py already writes payment_completed/
                        # idempotency events through (no second logging
                        # system). Never chain-of-thought/private reasoning --
                        # just the gate's own structured decision + which
                        # capability/action it gated, so an auditor can answer
                        # "was consent required, was it granted, did it
                        # commit" without needing to re-derive it from raw
                        # application logs.
                        from src.monkey_brain.kernel.pipeline.audit_trail import record_decision_event
                        negotiation_decision = context.get("negotiation_decision") if isinstance(context, dict) else None
                        if gated_outcome is None:
                            security_outcome = "allowed"
                        elif negotiation_decision is False:
                            security_outcome = "negotiation_rejected"
                        else:
                            security_outcome = "paused_for_negotiation"
                        record_decision_event(
                            "transition_gate_decision",
                            actor_id=context.get("actor_id", "") if isinstance(context, dict) else "",
                            execution_id=action.correlation_id,
                            reason=gate_decision.reason,
                            metadata={
                                "capability": action.capability, "action_id": action.action_id,
                                "requires_negotiation": gate_decision.requires_negotiation,
                                "contention": gate_decision.contention,
                                "counterparties": list(gate_decision.counterparties),
                                "negotiation_decision": negotiation_decision,
                                "security_outcome": security_outcome,
                            },
                        )
                        # Lemon metrics (previously zero telemetry on World
                        # Commit — real values only: security_outcome is the
                        # gate's own just-computed verdict, the same value the
                        # audit record above carries. "allowed" means this
                        # transition is now eligible to proceed to the real
                        # capability call below, not that the capability itself
                        # has yet succeeded — grocery.py's own KG mutation
                        # (try_reserve etc.) has no counter of its own to
                        # confirm the literal write.
                        _obs.counter("world_commit.total", security_outcome=security_outcome)

                    if gated_outcome is not None:
                        outcome = gated_outcome
                        # gate_decision is None when this outcome came from the
                        # connectivity gate above (refused before the
                        # transition gate ever ran) -- that path never needs
                        # negotiation-store bookkeeping, only the transition
                        # gate's own requires_negotiation path does.
                        if gate_decision is not None and gate_decision.requires_negotiation and context.get("negotiation_decision") is None:
                            if not waiting_for_negotiation:
                                from src.monkey_brain.kernel.pipeline.negotiation_store import (
                                    PendingNegotiation, save_pending_negotiation,
                                )
                                proposing_actor = context.get("actor_id", "")
                                counterparties = list(outcome.result.get("counterparties", []) or [])
                                negotiation_execution_id = execution_id or action.action_id
                                save_pending_negotiation(PendingNegotiation(
                                    execution_id=negotiation_execution_id,
                                    actor_id=proposing_actor,
                                    step_index=action.step_index, capability=action.capability,
                                    action_id=action.action_id,
                                    proposed_transition=outcome.result.get("proposed_transition", {}) or {},
                                    counterparties=counterparties,
                                    reason=outcome.result.get("reason", ""),
                                    correlation_id=action.correlation_id, causation_id=action.causation_id,
                                    original_question=context.get("question", ""),
                                ))
                                # Surface the proposal itself as a real conversation
                                # message — previously the negotiation gate only ever
                                # wrote to negotiation_store.py's own Redis record,
                                # which the Conversations panel (society context
                                # stream, INTERACTION events) never reads, so a real
                                # negotiation was invisible there even though it
                                # blocked the execution. Same publish shape
                                # route_interaction already uses (runtime.py:638).
                                if self._context_stream is not None:
                                    from src.monkey_brain.kernel.society.context_stream import ContextEvent, ContextEventType
                                    try:
                                        self._context_stream.publish(ContextEvent(
                                            event_type=ContextEventType.INTERACTION,
                                            actor_id=proposing_actor,
                                            description=(
                                                f"{proposing_actor} proposes {action.capability} "
                                                f"to {', '.join(counterparties) or 'a counterparty'}"
                                            ),
                                            payload={
                                                "from_actor_id": proposing_actor,
                                                "to_actor_id": counterparties[0] if len(counterparties) == 1 else "",
                                                "participants": [proposing_actor, *counterparties],
                                                "thread_id": negotiation_execution_id,
                                                "interaction_id": negotiation_execution_id,
                                                "message": outcome.result.get("reason", "") or f"Proposing {action.capability}",
                                            },
                                            provenance="negotiation:proposal",
                                            correlation_id=negotiation_execution_id,
                                            causation_id=action.causation_id,
                                        ))
                                    except Exception:
                                        logger.warning("[executor] failed to publish negotiation proposal event for %s", action.capability)
                            waiting_for_negotiation = True
                        outcomes.append(outcome)
                        publish_started = time.perf_counter()
                        self._publish_action_event(action, outcome, context)
                        event_publish_ms += (time.perf_counter() - publish_started) * 1000
                        continue

                    outcome = await self._execute_action(action, context, local_policy_decision=local_policy_decision)
                    if gate_decision is not None and isinstance(outcome.result, dict):
                        outcome.result["gate_decision"] = gate_decision.to_dict()

                    # Same-tick cross-provider/cross-agent recovery
                    # (Qualification Gap Closure, Phase 4; domain-independent
                    # per "MAKE DOMAIN ISOLATION AIRTIGHT"): the SAME generic
                    # opt-in shape as the approval contract above -- a failed
                    # outcome whose own result carries {"recoverable": True}
                    # (a forced-failure test fault marked recoverable, see
                    # fault_injection.py, or a real capability's own reported
                    # failure) gets exactly ONE re-attempt: re-ground (refresh
                    # the KG against current world state) and re-invoke the
                    # SAME capability with a retry-flagged copy of its OWN
                    # original parameters, unmodified -- the executor never
                    # inspects what those parameters mean; the capability
                    # decides what "try again" means for itself. Bounded
                    # structurally (not by a counter) -- this branch only ever
                    # runs once per action because it is not itself inside a
                    # loop.
                    if not outcome.success and isinstance(outcome.result, dict) and outcome.result.get("recoverable"):
                        if isinstance(context, dict):
                            kg = context.get("knowledge_graph")
                            refresh = getattr(kg, "refresh", None)
                            if callable(refresh):
                                refresh()
                        retry_action = self._build_recovery_action(action)
                        outcome = await self._execute_action(retry_action, context)

                    this_step_requires_approval = isinstance(outcome.result, dict) and bool(outcome.result.get("requires_approval"))
                    if this_step_requires_approval:
                        # A real, live-only gap found by testing a multi-item
                        # approval prompt end to end (not by any executor-level
                        # unit test, which never exercises two INDEPENDENT
                        # steps in one plan): only the FIRST pause in a given
                        # tick is persisted as the resumable PendingApproval
                        # (approval_store.py's own record is keyed one-per-
                        # execution_id, a deliberate scope boundary -- not a
                        # silent loss, since every step's own real outcome,
                        # including a second requires_approval, still appears
                        # in outcomes/actions below regardless).
                        if not waiting_for_human:
                            from src.monkey_brain.kernel.pipeline.approval_store import PendingApproval, save_pending_approval
                            save_pending_approval(PendingApproval(
                                execution_id=execution_id or action.action_id,
                                actor_id=context.get("actor_id", "") if isinstance(context, dict) else "",
                                step_index=action.step_index, capability=action.capability,
                                action_id=action.action_id,
                                proposed_action=outcome.result.get("proposed_action", {}) or {},
                                reason=outcome.result.get("reason", ""),
                                correlation_id=action.correlation_id, causation_id=action.causation_id,
                                original_question=context.get("question", "") if isinstance(context, dict) else "",
                            ))
                        waiting_for_human = True

                    this_step_requires_payment_confirmation = (
                        isinstance(outcome.result, dict) and bool(outcome.result.get("requires_payment_confirmation"))
                    )
                    if this_step_requires_payment_confirmation:
                        # Same "only the FIRST pause in a given tick is
                        # persisted as the resumable pending record" scope
                        # boundary as requires_approval above (PendingPayment
                        # is keyed one-per-execution_id, same as PendingApproval/
                        # PendingNegotiation) -- every step's own real outcome
                        # still appears in outcomes/actions below regardless.
                        if not waiting_for_payment:
                            from src.monkey_brain.kernel.pipeline.payment_store import PendingPayment, save_pending_payment
                            result = outcome.result
                            save_pending_payment(PendingPayment(
                                execution_id=execution_id or action.action_id,
                                actor_id=context.get("actor_id", "") if isinstance(context, dict) else "",
                                step_index=action.step_index, capability=action.capability,
                                action_id=action.action_id,
                                provider_name=result.get("provider_name", ""),
                                reservation_id=result.get("reservation_id", ""),
                                payer_ref=result.get("payer_ref", ""),
                                amount=float(result.get("amount", 0.0) or 0.0),
                                reserve_idempotency_key=result.get("reserve_idempotency_key", ""),
                                status=result.get("status", "pending_authorization"),
                                reason=result.get("reason", ""),
                                correlation_id=action.correlation_id, causation_id=action.causation_id,
                                original_question=context.get("question", "") if isinstance(context, dict) else "",
                            ))
                        waiting_for_payment = True

                    if (
                        outcome.success
                        and action.step_index >= 0
                        and not this_step_requires_approval
                        and not this_step_requires_payment_confirmation
                    ):
                        succeeded_step_indices.add(action.step_index)
                        if execution_id:
                            completed_steps[action.step_index] = {
                                "action_id": outcome.action_id, "success": outcome.success,
                                "result": outcome.result, "error": outcome.error,
                                "latency_ms": outcome.latency_ms,
                            }
                            from src.monkey_brain.kernel.pipeline.execution_checkpoint_store import save_execution_checkpoint
                            save_execution_checkpoint(execution_id, plan_dict, completed_steps)
                    self._apply_test_mutation(action, context)
                outcomes.append(outcome)
                publish_started = time.perf_counter()
                self._publish_action_event(action, outcome, context)
                event_publish_ms += (time.perf_counter() - publish_started) * 1000

                # Vertical projector (grocery.project_action_result_to_context)
                # and compile-time runtime_projections encode the SAME
                # selected -> selected_product append. The live /prompt path
                # wires both (CapabilityRuntime projector + compile_plan's
                # execution_graph). Applying both doubled a one-item milk
                # selection into two identical order line items.
                # runtime_projections remain the recorded contract when no
                # projector is injected (plan_compiler's non-executing
                # metadata, executed here only as a fallback).
                projector_applied = False
                if (
                    outcome.success
                    and isinstance(context, dict)
                    and isinstance(outcome.result, dict)
                    and self._context_projector is not None
                ):
                    self._context_projector(outcome.result, context)
                    projector_applied = True
                if node_id and execution_graph is not None:
                    if outcome.success:
                        execution_graph.mark_complete(node_id, outcome.result)
                    else:
                        execution_graph.mark_failed(node_id, outcome.error or "failed")
                if (
                    not projector_applied
                    and execution_graph is not None
                    and node_id
                    and outcome.success
                    and isinstance(context, dict)
                    and isinstance(outcome.result, dict)
                ):
                    projections = (execution_graph.get_node(node_id).props or {}).get("runtime_projections") or []
                    if projections:
                        apply_runtime_projections(outcome.result, context, projections)

                # Generic execution state machine: a paused step does NOT stop
                # the whole tick anymore (Qualification Gap Closure -- a real,
                # live-only gap: a multi-item plan where only ONE item needed
                # approval used to never even attempt the other, independent
                # item). A step that genuinely depends on the paused one is
                # already correctly blocked by the ordinary missing-dependency
                # check above (the paused step never enters
                # succeeded_step_indices, since it isn't a success yet) --
                # nothing further is needed here to keep dependents honestly
                # unreached. An independent step keeps running normally.

        total_ms = (time.time() - start_time) * 1000
        success_count = sum(1 for o in outcomes if o.success)
        failure_count = sum(1 for o in outcomes if not o.success)

        goal_achieved = (
            failure_count == 0 and not waiting_for_human
            and not waiting_for_negotiation and not waiting_for_payment
        )

        paused_status = "waiting_for_human" if waiting_for_human else (
            "waiting_for_negotiation" if waiting_for_negotiation else (
                "waiting_for_payment_confirmation" if waiting_for_payment else None
            )
        )
        _obs.gauge("pipeline.execution_latency_ms", total_ms)
        _obs.finish_span(paused_status or ("ok" if failure_count == 0 else "error"))

        return ExecutionResult(
            actions=tuple(outcomes),
            success_count=success_count,
            failure_count=failure_count,
            total_latency_ms=round(total_ms, 2),
            goal_achieved=goal_achieved,
            event_publish_ms=round(event_publish_ms, 3),
            status=paused_status or "completed",
        )

    @staticmethod
    def _plan_dict_from_actions(actions: tuple[Action, ...], goal: str = "",
                                source_plan: Any = None) -> dict[str, Any]:
        """A checkpoint's stored "plan" only needs to round-trip through
        kernel/pipeline/planning/current_plan_store.py::plan_from_dict
        well enough to re-drive execution on resume -- action name,
        parameters, and depends_on are what actually matter for that.

        goal (Qualification Gap Closure, Phase 9 fix): a REAL, live-only
        bug found by actually resuming a paused approval end-to-end
        through the HTTP API, not by any executor-level unit test (which
        never exercises belief_runtime.py's own planning stage at all --
        every one of this session's own tests calls execute() directly).
        Leaving this "" (this function's own prior, honest-sounding
        design) meant plan_from_dict's reconstructed Plan.goal was ALSO
        empty on resume, and PlanValidator.validate() genuinely rejects
        any plan with no goal ("plan_has_no_goal") regardless of whether
        belief.goal (a completely separate field, checked earlier as
        has_goal) was ever set correctly -- confirmed live: has_goal=True
        yet the resumed plan was still rejected. The real question text
        IS available at every checkpoint-save call site (context is
        already threaded through execute()), so passing it through here
        is a genuine fix, not a new fabrication -- confidence/risk have
        no equivalent real source and stay at their honest 0.0 defaults."""
        if source_plan is not None:
            # Checkpoints must retain the original plan index space.  In
            # particular, permission-denied steps are absent from `actions`;
            # rebuilding from actions would shift later dependency indices.
            from src.monkey_brain.kernel.pipeline.planning.current_plan_store import plan_to_dict
            return plan_to_dict(source_plan)
        return {
            "goal": goal, "confidence": 0.0, "risk": 0.0, "planner": "resumed",
            "steps": [
                {
                    "action": a.capability, "description": a.expected_outcome,
                    "preconditions": list(a.preconditions), "expected_outcome": a.expected_outcome,
                    "cost": 0.0, "confidence": a.confidence, "required_permission": "",
                    "parameters": dict(a.parameters), "depends_on": list(a.depends_on),
                }
                for a in actions
            ],
        }

    @staticmethod
    def _build_recovery_action(action: Action) -> Action:
        """Same-tick cross-provider/cross-agent recovery (Phase 4; made
        domain-independent under "MAKE DOMAIN ISOLATION AIRTIGHT"): builds
        the ONE retry Action for a failure whose result marked itself
        {"recoverable": True} -- this IS the complete, generic recovery
        contract the OS deals in. `retry_after_failure=True` is a plain
        marker flag on a verbatim copy of the ORIGINAL action.parameters
        (whatever they were -- this function never reads, interprets, or
        derives anything from them). The retried capability receives its
        own original parameters back unchanged plus that one marker; what
        "try again" means -- exclude a product id, pick a different
        delegate, or simply re-run identically -- is entirely the
        capability's own business, decided from data it already owned
        before this retry (its own original parameters), never supplied
        by the executor. A capability that doesn't recognize the marker
        at all just re-runs unchanged, which is still a legitimate "try
        again now that the world may have moved on" retry (kg.refresh()
        already ran before this is called -- see execute())."""
        import dataclasses

        retry_parameters = dict(action.parameters)
        retry_parameters["retry_after_failure"] = True
        return dataclasses.replace(action, parameters=retry_parameters)

    @staticmethod
    def _apply_test_mutation(action: Action, context: Any) -> None:
        """World-mutation qualification tests (kernel/testing/
        mutation_hooks.py): a test registers "when this actor next executes
        an action matching X, mutate the world like Y" before sending its
        request. Checked once per real action attempt (never for the
        blocked-by-dependency branch above, since no action actually ran
        there) so a test can inject a change — inventory, price, provider
        availability — between two steps of the SAME plan and verify the
        rest of the tick (e.g. OrderConfirmationCapability's own freshness
        re-check) reacts to it, rather than executing blindly against
        stale grounding. A no-op single dict lookup for every actor with no
        registration, i.e. all real, non-test traffic."""
        if not isinstance(context, dict):
            return
        actor_id = context.get("actor_id")
        kg = context.get("knowledge_graph")
        if not actor_id or kg is None:
            return
        from src.monkey_brain.kernel.testing.mutation_hooks import consume_mutation
        mutate = consume_mutation(actor_id, action)
        if mutate is not None:
            mutate(kg)

    def _casefold_resolve(self, capability: str) -> str | None:
        """Case-insensitive fallback lookup against the wired capability
        bus's registered names -- the exact matching logic
        _execute_action has always used for a capability whose exact name
        didn't resolve. Extracted (not duplicated) so resolve_capability
        (a compile-time probe, see plan_compiler.py's caller in
        belief_runtime.py::_execute_plan) can never diverge from what
        _execute_action will actually do at dispatch time."""
        requested = str(capability).casefold()
        for name in self._capability_bus.names():
            if str(name).casefold() == requested:
                return name
        return None

    def resolve_capability(self, capability: str) -> str | None:
        """Compile-time probe: can `capability` be resolved against the
        wired capability bus? Returns the name that would actually be
        dispatched to (may differ from `capability` only in case), or
        None if it cannot be resolved at all.

        Never invokes `.handle()` or anything else -- a pure name-level
        check, safe to call for every plan step before any action
        executes (see plan_compiler.py::compile_plan's
        resolved_capabilities parameter).

        No capability bus wired at all -- mirrors _execute_action's own
        "simulate success" fallback (see execute()'s early branch below):
        nothing to validate against, so every name is trusted as-is
        rather than rejected for lacking a bus to check.
        """
        if self._capability_bus is None:
            return capability
        if self._capability_bus.discover(capability) is not None:
            return capability
        return self._casefold_resolve(capability)

    async def _execute_action(
        self, action: Action, context: Any = None, *,
        local_policy_decision: dict[str, Any] | None = None,
    ) -> ActionOutcome:
        """Execute a single action through the capability bus.

        local_policy_decision: computed by _execute_actions's own
        connectivity-gate block (kernel/edge/local_governance.py), never
        by this method itself, and never read back out of `context` --
        passed as an explicit parameter specifically so it can never leak
        across actions the way a shared context-dict key could (context
        is the SAME dict object for every action in a batch; a per-action
        decision stashed there without being cleared each iteration could
        silently apply to the wrong action)."""
        import inspect
        import random
        start_time = time.time()

        dispatch_reserved = False
        execution_id = context.get("_execution_id") if isinstance(context, dict) else None
        # Optional Redis claim on (execution_id, action_id) before handle().
        if execution_id and action.action_id:
            from src.monkey_brain.kernel.production_gates import capability_dispatch_dedup_enabled
            if capability_dispatch_dedup_enabled():
                from src.monkey_brain.kernel.pipeline.capability_dispatch_store import (
                    complete_dispatch,
                    load_cached_outcome,
                    release_dispatch,
                    reserve_dispatch,
                )
                cached = load_cached_outcome(execution_id, action.action_id)
                if cached is not None:
                    latency = (time.time() - start_time) * 1000
                    return ActionOutcome(
                        action_id=cached.get("action_id", action.action_id),
                        success=bool(cached.get("success", False)),
                        result=cached.get("result"),
                        error=cached.get("error", ""),
                        latency_ms=float(cached.get("latency_ms", latency) or latency),
                        metadata={"resumed_from_dispatch_cache": True},
                    )
                reserve_status = reserve_dispatch(execution_id, action.action_id)
                if reserve_status == "cached":
                    cached = load_cached_outcome(execution_id, action.action_id)
                    if cached is not None:
                        latency = (time.time() - start_time) * 1000
                        return ActionOutcome(
                            action_id=cached.get("action_id", action.action_id),
                            success=bool(cached.get("success", False)),
                            result=cached.get("result"),
                            error=cached.get("error", ""),
                            latency_ms=float(cached.get("latency_ms", latency) or latency),
                            metadata={"resumed_from_dispatch_cache": True},
                        )
                elif reserve_status == "in_progress":
                    latency = (time.time() - start_time) * 1000
                    return ActionOutcome(
                        action_id=action.action_id,
                        success=False,
                        error="duplicate capability dispatch in progress",
                        latency_ms=round(latency, 2),
                        result={"duplicate_dispatch": True, "capability": action.capability},
                    )
                elif reserve_status == "fresh":
                    dispatch_reserved = True

        def _done(outcome: ActionOutcome) -> ActionOutcome:
            """Persist or release the dispatch claim after handle() completes."""
            if dispatch_reserved and execution_id and action.action_id:
                from src.monkey_brain.kernel.pipeline.capability_dispatch_store import (
                    complete_dispatch,
                    release_dispatch,
                )
                payload = {
                    "action_id": outcome.action_id,
                    "success": outcome.success,
                    "result": outcome.result,
                    "error": outcome.error,
                    "latency_ms": outcome.latency_ms,
                }
                if outcome.success:
                    complete_dispatch(execution_id, action.action_id, payload)
                else:
                    release_dispatch(execution_id, action.action_id)
            return outcome

        try:
            # Deterministic fault injection (kernel/testing/fault_injection.py):
            # a test registered "the next time this actor attempts an action
            # matching X, fail it" — checked ahead of the stochastic branch
            # below since a deterministic test expectation must never be
            # subject to the random branch also firing (or not) on the same
            # call. The capability bus is never invoked — this is a genuine
            # "did not run" outcome, not a capability lying about its result.
            actor_id = context.get("actor_id") if isinstance(context, dict) else None
            if actor_id:
                from src.monkey_brain.kernel.testing.fault_injection import consume_forced_failure
                forced = consume_forced_failure(actor_id, action)
                if forced is not None:
                    forced_error, forced_recoverable = forced
                    latency = (time.time() - start_time) * 1000
                    return _done(ActionOutcome(
                        action_id=action.action_id,
                        success=False,
                        result={
                            "forced_failure": True, "capability": action.capability,
                            "recoverable": forced_recoverable,
                        },
                        error=forced_error,
                        latency_ms=round(latency, 2),
                    ))

            # Stochastic failure — simulates real-world unreliability
            if self._failure_rate > 0 and random.random() < self._failure_rate:
                latency = (time.time() - start_time) * 1000
                error_msgs = [
                    f"Transient failure: {action.capability} — retry needed",
                    f"Resource contention: {action.capability} — capacity exceeded",
                    f"Timeout: {action.capability} — exceeded deadline",
                    f"Dependency unavailable: {action.capability} — upstream delayed",
                    f"State conflict: {action.capability} — concurrent modification",
                ]
                return _done(ActionOutcome(
                    action_id=action.action_id,
                    success=False,
                    result={"simulated": True, "stochastic_failure": True, "capability": action.capability},
                    error=random.choice(error_msgs),
                    latency_ms=round(latency, 2),
                ))

            if self._capability_bus is None:
                from src.monkey_brain.kernel.operation_classification import is_security_critical
                from src.monkey_brain.kernel.production_gates import insecure_dev_mode
                if is_security_critical(action.capability) and not insecure_dev_mode():
                    logger.error(
                        "[executor] No capability bus — refusing ungoverned simulation of %s",
                        action.capability,
                    )
                    return _done(ActionOutcome(
                        action_id=action.action_id,
                        success=False,
                        error="ungoverned capability simulation forbidden",
                        result={"simulated": True, "governed": False, "capability": action.capability},
                        latency_ms=0.0,
                    ))
                logger.warning(
                    "[executor] No capability bus wired — simulating %s with NO governance "
                    "(no TransitionGate check, no real state mutation)", action.capability,
                )
                from src.monkey_brain.kernel.compile import _obs
                _obs.counter("capability.calls.total", capability=action.capability, status="ungoverned")
                return _done(ActionOutcome(
                    action_id=action.action_id,
                    success=True,
                    result={"simulated": True, "governed": False, "capability": action.capability},
                    latency_ms=0.0,
                ))

            # Discover the capability
            capability = self._capability_bus.discover(action.capability)
            if capability is None:
                # LLM planners occasionally vary only the casing of a
                # capability name (for example, ``respondToInquiry``).
                # Capability registration remains canonical; resolve this
                # harmless formatting drift at the execution boundary.
                name = self._casefold_resolve(action.capability)
                if name is not None:
                    capability = self._capability_bus.discover(name)
            if capability is None:
                logger.warning("[executor] Capability not found: %s", action.capability)
                return _done(ActionOutcome(
                    action_id=action.action_id,
                    success=False,
                    error=f"Capability not found: {action.capability}",
                    latency_ms=(time.time() - start_time) * 1000,
                ))

            # Invoke the capability. Conditional await: every existing
            # capability's handle() is a plain sync function (unaffected);
            # a capability that genuinely needs to await real I/O (e.g.
            # AskActorCapability's NATS request/reply) can be a real
            # async def handle() instead, and this is the one place that
            # has to know the difference.
            handle_args = {
                "action": action.capability,
                "parameters": action.parameters,
                "context": context,
            }

            async def _invoke_handle() -> Any:
                if inspect.iscoroutinefunction(capability.handle):
                    return await capability.handle(handle_args)
                return capability.handle(handle_args)

            # Live Capability Governance Closure: this call used to reach
            # capability.handle() directly -- the SAME per-capability
            # canonical boundary kernel/execute/capability_bus.py::
            # CapabilityBus.execute() already wraps every one of ITS
            # dispatches in (ensure_governed(f"capability.{name}", ...))
            # was never actually on THIS path, because the real grocery/
            # plan-execution flow goes through this class's direct
            # discover()+handle() instead of that other CapabilityBus.
            # Reusing the EXACT same action-naming convention here (not
            # inventing a second one) so both dispatch paths produce
            # identical OPA input shapes for the same capability. `extra`
            # carries the actual capability/parameters being requested
            # (Section 5: OPA evaluates THIS specific operation+scope, not
            # just "agent may execute") -- strip_untrusted_security_signals
            # (already invoked inside build_opa_input) drops any
            # agent-shaped security-signal keys from it before OPA sees it.
            #
            # operation_id deliberately NOT threaded here (left at
            # ensure_governed's default, a fresh id per call): an action
            # already fully completed on a prior attempt is intercepted
            # earlier by the dispatch-cache/checkpoint replay above and
            # never reaches this line again -- threading action.action_id
            # into SecurityOperation's ledger here would only matter for a
            # within-attempt retry of one action, which is exactly the
            # commitment/retry-semantics territory this task is explicitly
            # scoped OUT of touching (confirmed risk from prior session
            # work: a stable operation_id reused within an active
            # commitment collides with SecurityOperation.create()'s
            # duplicate-operation check).
            from src.monkey_brain.kernel.security_boundary import (
                HumanApprovalRequired, SecurityBoundaryDenied, ensure_governed,
            )

            async def _governed_invoke() -> Any:
                return await asyncio.wait_for(
                    _invoke_handle(), timeout=_CAPABILITY_TIMEOUT_SECONDS,
                )

            # Portable Delegation integration point: a caller that has
            # ALREADY run kernel/delegation.py::verify_delegation_chain
            # may stash the verified result via kernel/delegation.py::
            # to_opa_delegation_context under context["verified_delegation"].
            # Populated today by kernel/edge/delegation_message.py, called
            # from subscribe_actor_inbox's live message handler
            # (kernel/domains/grocery.py) for delegated_task messages that
            # carry a delegation_chain field.
            # Never read a caller-supplied "delegation" claim from
            # action.parameters -- that would be exactly the self-asserted-
            # authority path Section 21 forbids; this dict must already be
            # the output of a verified chain, not agent-claimed content.
            verified_delegation = (
                context.get("verified_delegation") if isinstance(context, dict) else None
            )

            try:
                result = await ensure_governed(
                    f"capability.{action.capability}",
                    action.capability,
                    _governed_invoke,
                    extra={"capability": action.capability, "parameters": action.parameters},
                    # execute()'s own outer ensure_governed("action_executor.
                    # execute", "actions", ...) call above already has
                    # commitment active by the time every _execute_action
                    # reaches this line -- without force_authorize=True this
                    # call would silently no-op straight to capability.handle()
                    # (see ensure_governed's/run_governed_mutation's own
                    # docstrings for exactly why nesting normally does that,
                    # and why this specific nesting needs to opt out of it).
                    force_authorize=True,
                    verified_delegation=verified_delegation,
                    local_policy_decision=local_policy_decision,
                )
            except asyncio.TimeoutError:
                latency = (time.time() - start_time) * 1000
                return _done(ActionOutcome(
                    action_id=action.action_id,
                    success=False,
                    error=f"Timeout: {action.capability} — exceeded {_CAPABILITY_TIMEOUT_SECONDS:.0f}s deadline",
                    latency_ms=round(latency, 2),
                    result={"timeout": True, "capability": action.capability},
                ))
            except HumanApprovalRequired as exc:
                # Governance decided this specific capability call requires
                # a human decision -- capability.handle() above never ran.
                # A clean ActionOutcome, not a raised exception escaping
                # this action, so independent sibling actions in the same
                # batch still get their own chance to run (the SAME "one
                # step pauses, unrelated steps still complete" contract the
                # existing capability-driven requires_approval convention
                # -- OrderConfirmationCapability et al. -- already
                # establishes; this is a second, policy-driven SOURCE of
                # that same pause, not a second pause mechanism).
                latency = (time.time() - start_time) * 1000
                logger.warning(
                    "[executor] capability %s requires human approval (approval_id=%s)",
                    action.capability, exc.approval_id,
                )
                return _done(ActionOutcome(
                    action_id=action.action_id,
                    success=False,
                    error=f"human approval required for {action.capability}",
                    latency_ms=round(latency, 2),
                    result={
                        "requires_approval": True,
                        "capability": action.capability,
                        "approval_id": exc.approval_id,
                        "operation_id": exc.operation_id,
                    },
                ))
            except SecurityBoundaryDenied as exc:
                # DENY, or a fail-closed AUTH/IDEMPOTENCY/AUDIT failure --
                # capability.handle() above never ran either way. Same
                # "clean outcome, not a crash" shape as the approval case.
                latency = (time.time() - start_time) * 1000
                logger.warning(
                    "[executor] capability %s denied by governance: %s",
                    action.capability, exc,
                )
                return _done(ActionOutcome(
                    action_id=action.action_id,
                    success=False,
                    error=f"governance denied {action.capability}: {exc}",
                    latency_ms=round(latency, 2),
                    result={"denied": True, "capability": action.capability, "stage": getattr(exc, "stage", "")},
                ))

            latency = (time.time() - start_time) * 1000

            # Check if the capability reported success
            success = True
            error = ""
            if isinstance(result, dict):
                success = result.get("success", True)
                error = result.get("error", "")

            # Minimal Lemon metrics layer: real capability invocation time
            # only (measured from just above the handle() call, not the
            # fault-injection/stochastic-failure/capability-not-found
            # early returns above, which never actually invoke a
            # capability). status classification reuses the exact error-
            # string prefixes this session's own live testing confirmed
            # action_executor.py produces ("blocked: dependency...").
            from src.monkey_brain.kernel.compile import _obs
            if success:
                cap_status = "success"
            elif (error or "").startswith("blocked: dependency"):
                cap_status = "blocked"
            elif (error or "").startswith("Capability not found"):
                cap_status = "rejected"
            else:
                cap_status = "failed"
            # gate_wired: real per-call visibility into whether this
            # capability's tick was even eligible for TransitionGate
            # governance (propose_transition/transition_gate are opt-in
            # per vertical — see __init__'s docstring). This doesn't make
            # gating structurally mandatory (that's a vertical's own
            # domain-knowledge decision the executor can't make for it),
            # but it turns "was this action ever considered for gating"
            # from an invisible constructor default into a queryable metric.
            gate_wired = self._propose_transition is not None and self._transition_gate is not None
            _obs.counter(
                "capability.calls.total", capability=action.capability,
                status=cap_status, gate_wired=str(gate_wired),
            )
            _obs.histogram("capability.duration_ms", latency, capability=action.capability)

            return _done(ActionOutcome(
                action_id=action.action_id,
                success=success,
                result=result,
                error=error,
                latency_ms=round(latency, 2),
            ))

        except Exception as e:
            latency = (time.time() - start_time) * 1000
            logger.error("[executor] Action %s failed: %s", action.action_id, e)
            from src.monkey_brain.kernel.compile import _obs
            _obs.counter("capability.calls.total", capability=action.capability, status="failed")
            _obs.histogram("capability.duration_ms", latency, capability=action.capability)
            return _done(ActionOutcome(
                action_id=action.action_id,
                success=False,
                error=str(e),
                latency_ms=round(latency, 2),
            ))

    def _publish_action_event(self, action: Action, outcome: ActionOutcome, context: Any) -> None:
        """MB-3051 Context Propagation: publish one real ContextEvent for
        this action outcome. SIMULATED outcomes (no real capability_bus
        wired, or the stochastic-failure-rate test path) never touched
        real state, so publishing them as an ordinary business ACTION
        event would be dishonest — they're either skipped (the synthetic
        test-failure path) or published as a distinct, unambiguously
        "UNGOVERNED" event (the no-capability-bus path) instead, never as
        a real outcome. See the governed-marker check below.

        Both prior caveats on this docstring are now closed: the live
        request pipeline (PlanetaryRuntime.execute_actor_request ->
        domains/vertical_router.py) wires a real capability_bus (MB-3060)
        AND, as of True Multi-Actor Coordination, a real context_stream —
        confirmed by tracing the exact call chain, not assumed. This is
        genuinely live in production now, not just for direct
        ActionExecutor/CapabilityRuntime callers.
        """
        if self._context_stream is None:
            return
        from src.monkey_brain.kernel.society.context_stream import ContextEvent, ContextEventType

        if isinstance(outcome.result, dict) and outcome.result.get("simulated"):
            # A genuinely ungoverned "no capability bus" outcome (see
            # _execute_action's "governed": False marker) must still be
            # observable/auditable, even though it never touched real
            # state — publishing it as a normal ACTION event would claim a
            # business outcome that didn't happen, so it gets its own,
            # unambiguously-labeled event instead. The stochastic-
            # failure-rate test path (result carries "simulated" but no
            # "governed" key) keeps the original skip — that's a
            # deliberately synthetic test outcome, not a real governance
            # gap, and was never dishonest to omit.
            if isinstance(outcome.result, dict) and outcome.result.get("governed") is False:
                actor_id = context.get("actor_id", "") if isinstance(context, dict) else ""
                self._context_stream.publish(ContextEvent(
                    event_type=ContextEventType.ACTION, actor_id=actor_id,
                    description=(
                        f"UNGOVERNED: {action.capability} reported success with no capability "
                        f"bus wired — no real state mutation occurred, no TransitionGate ran"
                    ),
                    payload={
                        "capability": action.capability, "action_id": outcome.action_id,
                        "governed": False, "simulated": True,
                    },
                    provenance="executor:ungoverned",
                ))
            return

        actor_id = context.get("actor_id", "") if isinstance(context, dict) else ""
        description = f"{action.capability} failed: {outcome.error}"
        if outcome.success:
            # Lazy import: belief_runtime.py imports CapabilityRuntime
            # (this class) from this module, so importing it back at
            # module load time would be circular — safe at call time,
            # once both modules are already fully loaded.
            from src.monkey_brain.kernel.pipeline.belief_runtime import _describe_single_change
            description = (
                (isinstance(outcome.result, dict) and _describe_single_change(action.capability, outcome.result))
                or f"{action.capability} succeeded"
            )
        payload = {
            "capability": action.capability,
            "action_id": outcome.action_id,
            "success": outcome.success,
            "result": outcome.result,
            # execution_id (== correlation_id, the same id every action in
            # this tick shares — execution_id = actions[0].correlation_id
            # above) — without this, _retrieve_context_stream (context_
            # engine.py) could only actor-filter ACTION events, not
            # execution-filter them, so an OLDER attempt's step-failure
            # events (e.g. a prior tick's "OrderConfirmation failed: no
            # order to confirm") stayed mixed into a LATER execution's
            # debugger alongside that execution's own real failures.
            "execution_id": action.correlation_id,
        }
        if self._domain_event_resolver is not None:
            domain_event = self._domain_event_resolver(action.capability, outcome.success, outcome.result)
            if domain_event:
                payload["domain_event"] = domain_event
        try:
            self._context_stream.publish(ContextEvent(
                event_type=ContextEventType.ACTION,
                actor_id=actor_id,
                description=description,
                payload=payload,
                provenance="ActionExecutor",
            ))
        except Exception:
            logger.warning("[executor] failed to publish context event for %s", action.capability)


# Architectural name for the existing capability-only executor. This alias
# deliberately reuses ActionExecutor rather than introducing a second bus or
# execution implementation.
CapabilityRuntime = ActionExecutor
