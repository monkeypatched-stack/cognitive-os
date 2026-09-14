"""CognitiveRuntime — owns cognition inside the pipeline.

Cognitive lifecycle (9 stages):
    CompiledRequest + RuntimeContext
        → Observe
        → Believe
        → Plan
        → Execute
        → Observe Outcome
        → Learn
        → Compile Φ
        → Predict
        → Commit
        → ExecutionResult

Every stage reads from and writes to BeliefState.
No stage manipulates raw dictionaries.

Ownership boundaries:
    Compiler      owns semantic compilation.
    Orchestrator  owns coordination.
    CognitiveRuntime owns cognition.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import re
import time
from typing import Any

from src.monkey_brain.kernel.pipeline.contracts import (
    CompiledRequest,
    RuntimeContext,
)
from src.monkey_brain.kernel.pipeline.execution_state import (
    CognitiveState,
    WorldSnapshot,
)
from src.monkey_brain.kernel.pipeline.belief_state import BeliefState
from src.monkey_brain.kernel.pipeline.actor import Actor
from src.monkey_brain.kernel.pipeline.observations import (
    ObservationProvider,
    WorldPollingProvider,
    BeliefFusion,
)
from src.monkey_brain.kernel.pipeline.cognitive_delta import CognitiveDelta
from src.monkey_brain.kernel.pipeline.cognitive_policy import CognitivePolicy
from src.monkey_brain.kernel.pipeline.planner import PlanningEngine
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
from src.monkey_brain.kernel.pipeline.plan_validator import (
    PlanValidator,
    ValidationResult,
)
from src.monkey_brain.kernel.pipeline.execution import (
    ExecutionEngine,
    Action,
    ActionOutcome,
    ExecutionResult,
)
from src.monkey_brain.kernel.pipeline.plan_compiler import (
    compile_plan,
    build_actions_from_compiled,
)
from src.monkey_brain.kernel.pipeline.action_executor import CapabilityRuntime

logger = logging.getLogger("agentos.pipeline.belief_runtime")

_ID_LIKE_TOKEN = re.compile(
    r"\b(?:product|store|order|rider|wallet|actor)_[0-9a-f]{6,}\b"
    r"|\b[0-9a-f]{16,}\b"
    r"|'[^']{6,}'",
    re.IGNORECASE,
)


def _sanitize_error_for_memory(error_text: str) -> str:
    """Strips raw entity-id-shaped tokens (product_.../store_.../a bare
    long hex string/anything quoted) out of a failure's error message
    before it's recorded as episodic memory — see
    _record_episodic_experience's own comment for the exact live loop
    this closes: a rejection's error text naming the specific bad id
    got fed back into a LATER attempt's own prompt as "relevant
    experience," which just handed the model the same bad id to copy
    again. Keeps the human-readable reason, drops the copyable value."""
    return _ID_LIKE_TOKEN.sub("[id]", error_text).strip()


_NO_PERMISSION_NEEDED_ACTIONS = frozenset(
    {
        "AskActor",
        "BroadcastToAffiliation",
        "RespondToInquiry",
        "EvaluateStrategy",
        "CompeteForResource",
        "RecordAgreement",
        "GetAgreements",
        "DelegationCheck",
        # kernel/domains/robot.py's PX4 mission capabilities: already really
        # governed at the ROS layer (ensure_governed(force_authorize=True)
        # inside run_ros_action_if_governed, kernel/edge/ros_integration.py)
        # -- this pre-execution required_permission gate is a second,
        # redundant layer on top that only exists because the LLM populates
        # it, never a real requirement these capabilities declare themselves.
        # Confirmed live: the exact same failure mode this frozenset was
        # already built for, on a different action set -- a small local
        # model tagged a plausible-looking invented "resource:drone_control"
        # onto its own "Arm"/"Takeoff"/"Waypoint"/"Land" steps despite the
        # system prompt's identical "leave required_permission empty"
        # instruction, and since no permission by that name has ever existed
        # anywhere in this codebase (confirmed: not a single reference), it
        # could never resolve for ANY actor -- a real drone mission would
        # fail 100% of the time on this alone, regardless of who's asking.
        "Heartbeat",
        "Arm",
        "Takeoff",
        "Waypoint",
        "Land",
    }
)
"""The exact action set llm_planner.py's own system prompt tells the
model to leave required_permission empty for. Confirmed live: a small
local model reliably follows the nearby "use these real parameters"
guidance for CompeteForResource but just as reliably ignores the "leave
required_permission empty" sentence a few paragraphs away, tagging a
plausible-looking made-up permission string instead — even after adding
a second, local reminder right next to CompeteForResource's own
guidance. Since these actions structurally never need a permission
(that's the system prompt's own claim, not a per-call judgment), the
robust fix is enforcing it here regardless of what the model wrote,
rather than continuing to hope a better-worded prompt will fix a real
instruction-following gap in the model itself."""

_MAX_CONSECUTIVE_PLAN_SKIPS = 3
"""Incremental scheduling safety valve (_generate_plan, below): even if
has_new_relevant_activity() keeps reporting "nothing changed" for an
actor, force a real replan at least this often. A skip-gate that can
never be wrong in practice still shouldn't be trusted to never be wrong
in theory -- this bounds how stale a reused plan can get regardless of
whether the change-detector has some gap this session didn't find."""


def _describe_single_change(capability: str, result: dict) -> str | None:
    """The per-capability formatting body of _describe_world_changes,
    factored out so ActionExecutor._publish_action_event (a lower-level
    module belief_runtime.py itself imports from, so it can't import
    back) can build the same rich, human-readable text for a single
    step's own ContextEvent description via a lazy import — instead of
    every successful action being flattened to the generic, low-
    information "{capability} succeeded" that used to be the only text
    a Context Events card ever had for a real purchase's own lifecycle."""
    if capability == "OrderCreation" and result.get("order_id"):
        items = result.get("items") or []
        # Real gap this closed: item count alone gives the model no way
        # to tell one order apart from another later in the same
        # conversation (e.g. "return the butter order" vs "the eggs
        # order") — confirmed live, a return request correctly named
        # the item but the model still picked an unrelated earlier
        # order, because "Order ORD-... created — 1 item(s), $7.38" is
        # identical-looking regardless of what was actually ordered.
        names = ", ".join(f"{i.get('qty', 1)}x {i.get('product', '?')}" for i in items) or "0 item(s)"
        return f"Order {result['order_id']} created — {names}, ${result.get('total', 0)}"
    if capability == "Payment" and result.get("payment_id"):
        before, after = result.get("wallet_balance_before"), result.get("wallet_balance_after")
        delta = f" (wallet ${before} → ${after})" if before is not None and after is not None else ""
        return f"Payment {result['payment_id']} captured — ${result.get('amount', 0)}{delta}"
    if capability == "Delivery" and result.get("delivery_id"):
        address = result.get("delivery_address", "")
        return f"Delivery {result['delivery_id']} arranged" + (f" to {address}" if address else "")
    if capability == "InventoryReserve" and result.get("order_id"):
        reserved = result.get("reserved") or []
        return f"Reserved {len(reserved)} item(s) for order {result['order_id']}"
    if capability == "OrderConfirmation" and result.get("status"):
        return f"Order confirmed — {result['status']}"
    if capability == "PaymentConfirmation" and result.get("order_id"):
        return f"Payment confirmed for order {result['order_id']} — ${result.get('total', 0)}"
    if capability == "ProductSelection" and result.get("selected"):
        names = ", ".join(
            f"{item.get('qty', 1)}x {item.get('name', item.get('id', ''))}" for item in result["selected"]
        )
        return f"Selected: {names}"
    if capability == "CompeteForResource" and result.get("won"):
        return (
            f"Won competition for {result.get('qty', 1)}x {result.get('resource_id', '')} — {result.get('reason', '')}"
        )
    if capability == "RecordAgreement" and result.get("agreement"):
        agreement = result["agreement"]
        return f"Agreement recorded for {result.get('entity_id', '')} with {agreement.get('with', '')} — {agreement.get('terms', '')}"
    return None


def _describe_world_changes(plan_steps: Any, action_outcomes: Any) -> list[str]:
    """World Changes refactor: the Cognitive Debugger persisted what an
    actor thought (beliefs/plans/decisions) but never what actually
    happened to the persistent world as a result — this closes that gap
    using ONLY real fields each capability's own .handle() result already
    returns (kernel/domains/grocery.py — verified by reading each handler
    directly, not guessed). A capability not covered below, or a real
    result missing an expected key, contributes nothing here — honest
    partial coverage, never a fabricated line."""
    changes: list[str] = []
    for step, outcome in zip(plan_steps, action_outcomes):
        if not outcome.success or not isinstance(outcome.result, dict):
            continue
        described = _describe_single_change(step.action, outcome.result)
        if described:
            changes.append(described)
    return changes


class CognitiveRuntime:
    """Core cognitive execution engine.

    Owns: execution lifecycle, belief state, planning, execution,
    observations, learning, prediction, Φ compilation.

    Stateless across requests — all per-request state lives in CognitiveState.
    """

    def __init__(
        self,
        observation_provider: ObservationProvider | None = None,
        belief_fusion: BeliefFusion | None = None,
        planning_engine: PlanningEngine | None = None,
        plan_validator: PlanValidator | None = None,
        execution_engine: ExecutionEngine | None = None,
        policy: CognitivePolicy | None = None,
        event_bus: Any = None,
        context_engine: Any = None,
        exploration_epsilon: float = 0.05,
    ) -> None:
        self._observation_provider = observation_provider or WorldPollingProvider()
        self._belief_fusion = belief_fusion or BeliefFusion()
        self._planning_engine = planning_engine or LLMPlanner()
        self._plan_validator = plan_validator or PlanValidator()
        self._context_engine = context_engine
        self._capability_runtime = execution_engine or CapabilityRuntime()
        self._execution_engine = self._capability_runtime
        self._policy = policy or CognitivePolicy()
        self._event_bus = event_bus
        # Learning recovery mechanism (Learning-hardening follow-up): without
        # this, a transition that once earned a genuine, Comparator-verified
        # negative observation is rejected by the decide-stage gate below
        # forever, even if the underlying capability/world condition that
        # caused the failure has since changed -- there was previously no
        # path back to fresh evidence for it (reported as "LEARNING RECOVERY
        # GAP" in the prior pass). This does NOT reset or overwrite any
        # learned probability -- it only occasionally lets a real negative-
        # evidence rejection through to execute anyway, so the existing,
        # unmodified `learn_from_execution()` EMA blend (prediction-owned,
        # not touched here) naturally incorporates whatever fresh evidence
        # results. Exploration is intentionally stochastic; `random.random()`
        # is the one place randomness enters (see _execute_plan below).
        self._exploration_epsilon = exploration_epsilon
        if not self._policy._stages:
            self._policy.configure(
                observe=lambda state: self._observe(state),
                believe=lambda state: self._update_beliefs(state),
                plan=lambda state: self._generate_plan(state),
                execute=lambda state: self._execute_plan(state),
                observe_outcome=lambda state: self._observe_outcome(state),
                learn=lambda state: self._learn(state),
                compile_phi=lambda state: self._compile_phi(state),
                predict=lambda state: self._predict(state),
                commit=lambda state: self._commit(state),
            )

    async def run(
        self,
        compiled: CompiledRequest,
        context: RuntimeContext,
        actor: Actor | None = None,
    ) -> dict[str, Any]:
        """Execute the complete cognitive lifecycle, starting from a FRESH
        CognitiveState (and therefore a fresh, empty BeliefState) every
        call — this is the pipeline's one-shot request/response contract.

        Args:
            compiled: Fully compiled request (from RequestCompiler)
            context: RuntimeContext with service handles
            actor: The actor performing this reasoning cycle.
                   If None, a default actor is created from the compiled request.

        Returns:
            ExecutionResult dict
        """
        # Resolve or create actor
        if actor is None:
            actor = Actor(
                actor_id=compiled.request.actor_id,
                tenant_id=compiled.request.tenant_id,
            )

        # Initialize CognitiveState — belief is fresh per-cycle (owned by runtime)
        state = CognitiveState(
            compiled=compiled,
            context=context,
            actor=actor,
            belief=BeliefState(
                actor_id=actor.actor_id,
                tenant_id=actor.tenant_id,
            ),
        )

        state = await self.tick(state)

        return self._build_result(state)

    async def tick(self, state: CognitiveState) -> CognitiveState:
        """Execute one cognitive cycle over an EXISTING CognitiveState.
        Callers own state construction and lifetime; this method assumes
        `state.actor` is already set (same invariant `run()` always
        upheld before this body was extracted from it).
        """
        lifecycle_start = time.time()

        # Geography/society hierarchy is NOT skipped here — verified by
        # tracing the call path (Gate 11 TODO audit): self._policy.execute()
        # below runs the "plan" stage (_generate_plan), which, when
        # self._context_engine is configured, builds a PlanningContext via
        # ContextConstructionEngine.build() — that already retrieves
        # current_location, current_society_context, current_team_context,
        # and active_memberships (with governance-resolved permissions, see
        # kernel/society/membership.py::resolve_permissions). Traversal of
        # the geography/society tree itself is correctly the CALLING
        # SocietyRuntime/GeographicEntityRuntime's job, not this per-actor
        # tick's — this method only needs to RECEIVE that context, which it
        # does. The two TODOs removed here pre-dated that integration.

        # Mark actor as reasoning
        state.actor.start_reasoning()

        # Execute lifecycle through the policy
        state = await self._policy.execute(state)

        # create the metrics
        state.metrics["total_ms"] = round((time.time() - lifecycle_start) * 1000, 2)
        from src.monkey_brain.kernel.compile import _obs

        _obs.gauge(
            "pipeline.cognitive_tick_total_ms",
            state.metrics["total_ms"],
            actor_id=state.actor.actor_id,
        )

        # Mark actor as idle
        state.actor.finish_reasoning()

        # publish the delta
        self._publish_delta(state)

        return state

    def _publish_delta(self, state: CognitiveState) -> None:
        """Publish this cycle's CognitiveDelta to the event bus, if one is configured.

        Non-fatal: a publish failure must not fail the request. The runtime never
        performs persistence itself — it only announces what changed; consumers
        (checkpointing, replication, world updates) subscribe to the event bus.
        """
        if self._event_bus is None or state.cognitive_delta is None:
            return
        try:
            from dataclasses import asdict

            self._event_bus.publish(
                "cognitive.delta",
                asdict(state.cognitive_delta),
                source="pipeline.cognitive_runtime",
            )
        except Exception as e:
            logger.warning("[belief_runtime] failed to publish cognitive delta: %s", e)

    # ── Stage 1: Observe ──────────────────────────────────────────────────

    async def _observe(self, state: CognitiveState) -> CognitiveState:
        """Acquire observations and capture a frozen world snapshot.

        The runtime does NOT observe the world directly.
        An ObservationProvider produces observations, and we consume them.

        ObservationProvider → ObservationSet → Observe() → Belief
        """
        from src.monkey_brain.kernel.compile import _obs

        _observe_start = time.time()

        belief = state.belief
        context = state.context
        # Cognitive Loop Verification: every real context_factory in this
        # codebase (api/routes/actors.py, kernel/society/runtime.py)
        # returns a plain dict — capabilities need dict-style
        # context.get("knowledge_graph") lookups — but getattr(dict,
        # "world", None) always misses (dicts have no attributes), so
        # this returned None unconditionally and Observe produced zero
        # observations for every actor capable of real execution.
        world = None
        if context is not None:
            world = getattr(context, "world", None)
            if world is None and isinstance(context, dict):
                world = context.get("world")

        # Capture world snapshot BEFORE observations
        # Assumes that the world is already loaded so load world must happen in kernet before this
        if world:
            try:
                if hasattr(world, "states"):
                    states = world.states()
                    state.world_snapshot = WorldSnapshot(
                        states=tuple(states),
                        state_count=len(states),
                        domain_count=(len(world.domains()) if hasattr(world, "domains") else 0),
                        transition_count=world.nnz() if hasattr(world, "nnz") else 0,
                    )
                elif hasattr(world, "entities"):
                    # SharedWorld compatibility — use entities as states
                    entities = list(world.entities())
                    state.world_snapshot = WorldSnapshot(
                        states=tuple(str(e) for e in entities),
                        state_count=len(entities),
                    )
            except Exception as e:
                logger.debug("[belief_runtime] world snapshot capture failed (non-fatal): %s", e)

        # Get observations from the provider
        obs_set = self._observation_provider.observe(belief.actor_id, world)
        state.pending_observations = obs_set

        # Lemon metrics (previously zero telemetry on this stage — real
        # values only: the observation count/snapshot size this call just
        # produced, never fabricated).
        _obs.counter("observe.total")
        _obs.gauge("observe.observations_acquired", float(len(obs_set.observations)))
        if state.world_snapshot is not None:
            _obs.gauge("observe.world_snapshot_states", float(state.world_snapshot.state_count))
        _obs.histogram("observe.duration_ms", (time.time() - _observe_start) * 1000.0)

        return state

    # ── Stage 2: Update belief ─────────────────────────────────────────────────────

    async def _update_beliefs(self, state: CognitiveState) -> CognitiveState:
        """Fuse observations into belief using BeliefFusion."""
        belief = state.belief

        # Fuse pending observations into belief
        obs_set = getattr(state, "pending_observations", None)
        if obs_set and not obs_set.is_empty():
            self._belief_fusion.update(belief, obs_set)

        # Set intent and goal from compiled request
        compiled = state.compiled
        if compiled:
            intent = getattr(compiled, "intent", {}) or {}
            belief.update_intent(
                intent_type=intent.get("intent", ""),
                confidence=intent.get("confidence", 0.0),
                metadata=intent,
            )

            # get the goal form the compiled goal
            goal = getattr(compiled, "goal", {}) or {}

            # get the goal ir form the compiled  goal
            goal_ir = getattr(compiled, "goal_ir", None)

            # get the goal name from the goal
            goal_name = goal.get("name", "") if isinstance(goal, dict) else ""

            # get the goal description from the goal
            goal_desc = goal.get("description", "") if isinstance(goal, dict) else ""

            # get the goal objective from the goal
            goal_objective = goal.get("optimization_objective", "") if isinstance(goal, dict) else ""
            if not goal_name and goal_ir:
                goal_name = getattr(goal_ir, "goal", "")

            # update the goal in the delief
            belief.update_goal(
                name=goal_name,
                description=goal_desc,
                optimization_objective=goal_objective,
            )

        # Update confidence from observation quality
        if belief.observations:
            obs_confidences = [o.confidence for o in belief.observations]
            avg_obs_conf = sum(obs_confidences) / len(obs_confidences)
            belief.uncertainty.confidence = round(0.7 * avg_obs_conf + 0.3 * belief.uncertainty.confidence, 4)

        return state

    # ── Stage 3: Plan ─────────────────────────────────────────────────────

    async def _generate_plan(self, state: CognitiveState) -> CognitiveState:
        """
        Generate an execution plan from belief + goal.

        Delegates to the PlanningEngine for plan generation.
        Validates the plan before storing it.
        """
        belief = state.belief
        goal = belief.goal

        # Attach transition model to belief for planner access
        transition_model = getattr(state, "transition_model", None)
        if transition_model:
            belief.metadata["transition_model"] = transition_model

        from src.monkey_brain.kernel.compile import _obs

        _obs.start_span("planner", component="pipeline", actor_id=belief.actor_id)
        _planner_start = time.time()
        # An actor with no goal always gets an empty Plan -- LLMPlanner.
        # plan()'s own first check already guarantees this (context_or_
        # belief with an empty/absent goal.name short-circuits before any
        # grounding/prompt/LLM work). Checking it here too, BEFORE
        # context_engine.build() runs, actually skips the real grounding
        # work for a plan that was always going to be empty regardless --
        # measured live at ~1.4-2.2s/actor wasted on exactly this case
        # (docs/adr/019-runtime-performance-audit.md's "6 actors with no
        # goal set" finding). Only changes which branch below runs, not
        # what either existing branch does.
        has_goal = goal is not None and bool(getattr(goal, "name", ""))
        from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal

        # Incremental scheduling: skip a fresh grounding+LLM replan when
        # (a) the actor already has a real, persisted Current Plan for
        # the SAME goal, and (b) nothing has happened since that plan was
        # computed that _retrieve_context_stream would actually surface
        # to grounding (has_new_relevant_activity, above, reuses that
        # exact relevance filter). Fails open on every axis: no context
        # engine, no persisted plan, a different goal, a hard
        # consecutive-skip cap, or any error in the check itself all
        # fall through to a real replan, unchanged from before this
        # existed. See docs/adr/019-runtime-performance-audit.md's
        # "incremental scheduling" recommendation.
        #
        # Goal-scoped (Cross-Goal Plan Contamination fix): current_record
        # is now looked up BY this tick's own goal_key, so "a different
        # goal" can never even reach the skip check below (a stale plan
        # for goal A cannot be mistaken for goal B's). The two skip
        # counters (_consecutive_plan_skips/_last_plan_context_version)
        # are goal-keyed dicts for the same reason: a goal switch starts
        # both at zero for the new goal rather than inheriting whatever
        # count the previous goal had accumulated.
        skip_replan = False
        reused_plan_dict: dict | None = None
        # Real regression this guards against: goal.name is now the
        # actor's stable standing goal ALONE (cognitive_actor.py's
        # name/description split, so BeliefState.update_goal()'s Goal
        # Timeline dedup stops treating every one-off triggering
        # question as a brand-new goal — see that change's own
        # comment). But THIS goal_key also gates whether a fresh
        # replan happens at all — keying it on name alone would mean
        # any new one-off question with the same standing goal
        # silently reuses whatever plan was cached for a PREVIOUS,
        # different question, never reaching the planner at all
        # (confirmed live: a real "Purchase 1 dozen large eggs"
        # request got the stale "Explain" plan a completely unrelated
        # earlier tick had cached). Folding description back in here
        # reconstructs the exact full-text goal_key this incremental-
        # scheduling check always used before that split, so a
        # genuinely different question still forces a real replan.
        goal_key = canonicalize_goal(f"{goal.name} {getattr(goal, 'description', '')}".strip()) if has_goal else ""

        # Checkpoint/restart: a caller resuming a prior attempt (via
        # meta.resume_execution_id -- see cognitive_actor.py's
        # _cognitive_tick) MUST re-run the exact same plan the earlier
        # attempt was executing, not a freshly re-planned one -- otherwise
        # ActionExecutor's step_index-keyed "already completed" check
        # (kernel/pipeline/action_executor.py) would compare against the
        # wrong step entirely. This takes priority over (and, when it
        # applies, skips entirely) the incremental-scheduling skip-gate
        # below -- a resume is a stronger, explicit signal than "nothing
        # relevant changed since the last plan."
        execution_id = state.metrics.get("execution_id", "") if isinstance(state.metrics, dict) else ""
        if has_goal and execution_id:
            from src.monkey_brain.kernel.pipeline.execution_checkpoint_store import (
                load_execution_checkpoint,
            )

            checkpoint = load_execution_checkpoint(execution_id)
            if checkpoint is not None and checkpoint.plan:
                skip_replan = True
                reused_plan_dict = checkpoint.plan
                state.metrics["plan_resumed_from_checkpoint"] = True

        if not skip_replan and has_goal and self._context_engine is not None:
            try:
                from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
                    load_current_plan,
                )

                current_record = load_current_plan(belief.actor_id, goal_key)
                if (
                    current_record is not None
                    and current_record.plan
                    and canonicalize_goal(current_record.goal) == goal_key
                ):
                    skip_counts = belief.metadata.setdefault("_consecutive_plan_skips", {})
                    version_by_goal = belief.metadata.setdefault("_last_plan_context_version", {})
                    consecutive_skips = int(skip_counts.get(goal_key, 0))
                    last_plan_version = int(version_by_goal.get(goal_key, -1))
                    # Plan invalidation / stale-world revalidation: a
                    # Current Plan cached against world state at t0 must
                    # not be silently replayed at t2 once the world has
                    # moved on at t1 (confirmed live: a product patched to
                    # quantity=0 was still selected, ordered, and paid for
                    # by a reused plan). check_plan_staleness re-checks
                    # every entity the plan's steps reference against
                    # KnowledgeGraph's own real per-entity version counter
                    # (kg.version_of) — the same primitive
                    # OrderConfirmation's belief-divergence check already
                    # uses, not a new parallel versioning system. kg comes
                    # from state.context, the same plain dict every
                    # capability already reads "knowledge_graph" from.
                    from src.monkey_brain.kernel.pipeline.planning.plan_staleness import (
                        check_plan_staleness,
                    )

                    kg = state.context.get("knowledge_graph") if isinstance(state.context, dict) else None
                    staleness = check_plan_staleness(kg, current_record)
                    if staleness.is_stale:
                        state.metrics["plan_stale"] = staleness.to_dict()
                        state.metrics["plan_stale"]["plan_id"] = current_record.plan_id
                        from src.monkey_brain.kernel.pipeline.audit_trail import (
                            record_plan_event,
                        )

                        record_plan_event(
                            "invalidated",
                            plan_id=current_record.plan_id,
                            actor_id=belief.actor_id,
                            execution_id=execution_id,
                            goal=current_record.goal,
                            steps=current_record.steps,
                            step_descriptions=current_record.step_descriptions,
                            result="; ".join(r["reason"] for r in staleness.to_dict()["affected_assumptions"]),
                            metadata={"affected_assumptions": staleness.to_dict()["affected_assumptions"]},
                        )
                    if (
                        consecutive_skips < _MAX_CONSECUTIVE_PLAN_SKIPS
                        and not current_record.last_execution_failed
                        and not staleness.is_stale
                        and not self._context_engine.has_new_relevant_activity(belief.actor_id, last_plan_version)
                    ):
                        skip_replan = True
                        reused_plan_dict = current_record.plan
                    # A Current Plan whose last real execution failed, or
                    # whose world-state assumptions are now stale, must
                    # never be silently reused here — this incremental-
                    # scheduling gate existed to skip a wasted replan when
                    # nothing relevant changed, not to keep re-executing a
                    # plan that's already demonstrated it's broken or is
                    # now known to reference a world that no longer
                    # exists. Excluding either forces a real LLM replan
                    # below, which _run_decide (comparison/integration.py)
                    # then also knows to accept unconditionally over the
                    # failed/stale Current Plan (see its own
                    # last_execution_failed/staleness check) rather than
                    # requiring it to clear the normal hysteresis margin.
                    # Confirmed live: without the failure half of this, a
                    # plan missing OrderCreation replayed identically
                    # forever — this gate reused it directly, never even
                    # reaching the planner or Decide's own failure check.
            except Exception:
                logger.debug(
                    "_generate_plan: incremental-scheduling check failed for %r (non-fatal -- replanning as usual)",
                    belief.actor_id,
                    exc_info=True,
                )
                skip_replan = False

        try:
            if not has_goal:
                from src.monkey_brain.kernel.pipeline.belief_state import Plan

                plan = Plan(goal="", confidence=0.0, planner="llm")
            elif skip_replan:
                from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
                    plan_from_dict,
                )

                plan = plan_from_dict(reused_plan_dict)
                resumed = bool(state.metrics.get("plan_resumed_from_checkpoint"))
                if not resumed:
                    # Only the incremental-scheduling skip-gate counts
                    # against the consecutive-skip cap -- a checkpoint
                    # resume is an explicit, one-time caller action, not
                    # an ongoing "nothing changed" pattern that cap exists
                    # to bound.
                    skip_counts = belief.metadata.setdefault("_consecutive_plan_skips", {})
                    skip_counts[goal_key] = int(skip_counts.get(goal_key, 0)) + 1
                state.metrics.setdefault("stage_timings_ms", {})["grounding_ms"] = 0.0
                state.metrics["plan_skipped_replan"] = True
                state.metrics["plan_skip_reason"] = (
                    "resuming from a checkpointed execution"
                    if resumed
                    else "goal unchanged, no new relevant activity since last plan"
                )
                # Real gap this closes: when grounding is skipped, this
                # execution's own ContextSnapshot never got saved at all
                # — GET .../planning-context and .../semantic-memory
                # 404'd for a real, successfully-executed tick (found
                # live: "No planning context '<id>' found for actor
                # '<id>'" for an execution whose order genuinely went
                # through). The plan WAS genuinely grounded — just by the
                # PREVIOUS tick's real retrieval, reused rather than
                # re-run, which is exactly what skip_replan means. Saving
                # a copy of that real, previous snapshot under THIS
                # execution_id (no new retrieval, no fabricated content)
                # makes that fact honestly visible instead of a 404, with
                # a real, empty diff (nothing changed, which is why
                # grounding was skipped in the first place).
                #
                # Goal-scoped, not actor-wide: the "previous snapshot"
                # here MUST be the one that grounded the goal_key we're
                # skipping-and-reusing, never just whatever this actor's
                # most recent tick was for. An actor-wide "latest" here
                # would silently stamp an unrelated goal's retrieval onto
                # this execution (confirmed live: a coffee-purchase
                # execution's debugger showed egg/milk/bread experiences
                # retrieved by an interleaved, unrelated grocery tick).
                execution_id = state.metrics.get("execution_id", "")
                if execution_id:
                    from src.monkey_brain.kernel.pipeline.planning.context_snapshot_store import (
                        save_context_snapshot,
                        load_latest_context_snapshot_for_goal,
                        diff_snapshots,
                    )
                    import dataclasses as _dc

                    previous_snapshot = load_latest_context_snapshot_for_goal(belief.actor_id, goal_key)
                    if previous_snapshot is not None:
                        reused_snapshot = _dc.replace(
                            previous_snapshot,
                            execution_id=execution_id,
                            created_at=time.time(),
                            diff_from_previous=None,
                        )
                        reused_snapshot.diff_from_previous = diff_snapshots(previous_snapshot, reused_snapshot)
                        save_context_snapshot(reused_snapshot)
                        state.metrics["context_diff"] = reused_snapshot.diff_from_previous
                    # else: no snapshot was ever recorded for this exact
                    # goal_key (e.g. Redis TTL/eviction between the
                    # original build and this skip) — leave this
                    # execution's ContextSnapshot unsaved rather than
                    # fabricate one from an unrelated goal. The debugger
                    # then honestly reports zero items retrieved instead
                    # of showing another execution's grounding.
            elif self._context_engine is not None:
                # Context Grounding: build() now also queries ContextStream
                # (ContextConstructionEngine._retrieve_context_stream) —
                # this used to be a known, explicit gap (recent live
                # events published elsewhere this same cycle weren't
                # available to planning); fixed by feeding recent
                # INTERACTION/WORLD_UPDATE/OBSERVATION events into
                # planning_context.relevant_context_events, ranked
                # newest-first alongside the other retrieval sources.
                _context_build_start = time.perf_counter()
                planning_context = await self._context_engine.build_async(
                    belief.actor_id,
                    goal,
                    execution_id=state.metrics.get("execution_id", ""),
                )
                context_build_latency_ms = (time.perf_counter() - _context_build_start) * 1000
                # Performance analysis instrumentation only (measurement,
                # not a behavior change): the "Grounding" stage of the
                # per-actor timing breakdown.
                state.metrics.setdefault("stage_timings_ms", {})["grounding_ms"] = round(context_build_latency_ms, 3)
                planning_context.metadata["_legacy_belief"] = belief

                # Cognitive State Quality Pass: relevant_knowledge is the
                # real, KG-grounded facts (id=..., real attributes/price)
                # the planner is about to reason over and reference in
                # its plan's parameters — previously built fresh here and
                # discarded the moment this call returns, same category
                # of loss Intent/Plan had before the first sprint.
                # Stashed on belief.metadata (already a real dict field,
                # same pattern as _resolved_permissions two lines below)
                # so _record_cognitive_artifacts (cognitive_actor.py) can
                # persist it as real Beliefs instead of nothing.
                belief.metadata["relevant_knowledge"] = planning_context.relevant_knowledge

                # Real gap this closes: ProductSelectionCapability
                # (grocery.py) only ever checked that a planner-selected
                # id was SOME real entity anywhere in the whole
                # knowledge graph (kg.get_entity(product_id) is not
                # None) — never that it was actually one of THIS tick's
                # offered candidates. Confirmed live: asked to "buy 1L
                # milk", with grounding correctly scoped to milk
                # products only, the model still selected an unrelated,
                # ungrounded id (Cage-Free Eggs) and the purchase went
                # through silently — a real product substituted for the
                # requested one with no error at all. Threaded through
                # state.context (the same dict every capability call
                # this tick already receives) so ProductSelection can
                # reject a selection that was never actually offered,
                # instead of accepting anything that merely exists
                # somewhere in the catalog.
                if isinstance(state.context, dict):
                    state.context["_relevant_knowledge_ids"] = {
                        item.evidence_ids[0] for item in planning_context.relevant_knowledge if item.evidence_ids
                    }

                # Context Grounding: persist the FULL PlanningContext this
                # tick actually built (not just relevant_knowledge above),
                # keyed by execution_id, so a developer can later see
                # exactly what grounded this plan — see
                # kernel/pipeline/planning/context_snapshot_store.py's own
                # module docstring for why this used to be entirely
                # discarded. Reads the actor's previous snapshot BEFORE
                # saving the new one — that's what makes the diff a real
                # "what changed since the last planning cycle," not a
                # comparison against itself.
                execution_id = state.metrics.get("execution_id", "")
                if execution_id:
                    from src.monkey_brain.kernel.pipeline.planning.context_snapshot_store import (
                        ContextSnapshot,
                        save_context_snapshot,
                        load_latest_context_snapshot_for_goal,
                        diff_snapshots,
                    )

                    # Goal-scoped, not actor-wide: comparing against
                    # whatever this actor's most recent tick happened to
                    # be (any goal) produced a fabricated-looking diff —
                    # e.g. "+5/-5 context events" that were really just
                    # the difference between an unrelated earlier goal's
                    # grounding and this one, not a genuine "what changed
                    # since I last planned THIS request." Comparing
                    # against the same goal_key's own last snapshot means
                    # a goal's first-ever tick honestly reports
                    # is_first_context=True (no previous grounding to
                    # compare against) instead of a diff against noise.
                    previous_snapshot = load_latest_context_snapshot_for_goal(belief.actor_id, goal_key)
                    snapshot = ContextSnapshot.from_planning_context(
                        execution_id,
                        belief.actor_id,
                        planning_context,
                        goal_key=goal_key,
                    )
                    snapshot.diff_from_previous = diff_snapshots(previous_snapshot, snapshot)
                    save_context_snapshot(snapshot)
                    state.metrics["context_diff"] = snapshot.diff_from_previous

                    from src.monkey_brain.kernel.compile import _obs

                    _obs.gauge(
                        "context.entities_retrieved",
                        float(snapshot.summary.get("entity_count", 0)),
                    )
                    _obs.gauge(
                        "context.relationships_traversed",
                        float(snapshot.summary.get("relationship_count", 0)),
                    )
                    _obs.gauge(
                        "context.context_events_consumed",
                        float(snapshot.summary.get("context_event_count", 0)),
                    )
                    _obs.gauge(
                        "context.semantic_memories_retrieved",
                        float(len(snapshot.experiences)),
                    )
                    _obs.gauge(
                        "context.episodic_memories_retrieved",
                        float(len(snapshot.executions) + len(snapshot.conversations)),
                    )
                    _obs.gauge("context.construction_latency_ms", context_build_latency_ms)
                    context_size = sum(
                        len(getattr(snapshot, s))
                        for s in (
                            "knowledge",
                            "relationships",
                            "context_events",
                            "experiences",
                            "conversations",
                            "executions",
                        )
                    )
                    _obs.gauge("context.size", float(context_size))
                    diff_size = sum(len(v) for v in snapshot.diff_from_previous["added"].values()) + sum(
                        len(v) for v in snapshot.diff_from_previous["removed"].values()
                    )
                    _obs.gauge("context.diff_size", float(diff_size))

                # Permissions ARE already policy/governance-based here —
                # verified by tracing the call path (Gate 11 TODO audit):
                # active_memberships (below) comes from
                # ContextConstructionEngine._retrieve_active_memberships(),
                # which calls membership_registry.resolve_permissions(...,
                # governance=governance) — that combines the membership's
                # granted permissions with governance.permissions_for(actor_id)
                # (kernel/society/membership.py). The two TODOs this replaced
                # pre-dated that integration.
                memberships = planning_context.metadata.get("active_memberships") or ()
                # tuple, not frozenset: belief.metadata round-trips through
                # json.dumps (checkpoint_actor_belief) — a frozenset silently
                # broke every checkpoint for any actor whose plan ever
                # resolved permissions (confirmed live: "Object of type
                # frozenset is not JSON serializable", non-fatal but meant
                # belief never actually persisted). Only ever read via `in`
                # membership checks below, so a tuple is behaviorally
                # identical.
                belief.metadata["_resolved_permissions"] = tuple(
                    sorted({p for m in memberships for p in m.get("permissions", ())})
                )

                # generate the plans
                #
                # LLMPlanner.plan() is async (a genuinely cancellable
                # awaited Ollama call -- see model_backend.py's module
                # docstring); other PlanningEngine implementations may
                # still be plain sync functions, so await directly only
                # when the injected engine is actually async, falling back
                # to the to_thread wrapper otherwise -- same outcome as
                # before for a sync engine, real cancellation for an async
                # one, no assumption about which kind is injected.
                plan_fn = self._planning_engine.plan
                if inspect.iscoroutinefunction(plan_fn):
                    plan = await plan_fn(planning_context)
                else:
                    plan = await asyncio.to_thread(plan_fn, planning_context)
                # Performance analysis instrumentation only (measurement,
                # not a behavior change): LLMPlanner.plan() stashed its own
                # prompt-build/llm-call/response-parse sub-timings on this
                # same PlanningContext.metadata (see llm_planner.py) --
                # pull them into this tick's stage_timings_ms too.
                state.metrics["stage_timings_ms"].update(planning_context.metadata.get("_stage_timings_ms", {}) or {})
                # Incremental scheduling: a real replan just happened --
                # reset THIS GOAL's skip counter and record the context
                # version this plan was grounded against, so the NEXT
                # tick's skip-gate check for this same goal (above) has a
                # fresh baseline. Other goals' counters are untouched.
                belief.metadata.setdefault("_consecutive_plan_skips", {})[goal_key] = 0
                belief.metadata.setdefault("_last_plan_context_version", {})[goal_key] = (
                    self._context_engine.context_stream_version()
                )
                state.metrics["plan_skipped_replan"] = False
                # LLMPlanner records the exact rendered prompt on the same
                # PlanningContext. Update the already-diffed snapshot so the
                # persisted artifact is the complete planner input, including
                # prompt text/token count, without comparing the context to
                # itself a second time.
                if execution_id:
                    snapshot.planner_prompt = str(planning_context.metadata.get("_planner_prompt", "") or "")
                    snapshot.prompt_tokens = int(planning_context.metadata.get("_prompt_tokens", 0) or 0)
                    save_context_snapshot(snapshot)
            else:
                plan_fn = self._planning_engine.plan
                if inspect.iscoroutinefunction(plan_fn):
                    plan = await plan_fn(belief, goal, state.context)
                else:
                    plan = await asyncio.to_thread(plan_fn, belief, goal, state.context)
        except Exception:
            _obs.gauge("pipeline.planner_latency_ms", (time.time() - _planner_start) * 1000)
            _obs.finish_span("error")
            raise
        _planner_total_ms = (time.time() - _planner_start) * 1000
        _obs.gauge("pipeline.planner_latency_ms", _planner_total_ms)
        # Performance analysis instrumentation only: total planner-call time
        # (grounding + prompt + LLM + parse combined) as a cross-check
        # against the sum of the finer stage_timings_ms entries above.
        state.metrics.setdefault("stage_timings_ms", {})["planning_total_ms"] = round(_planner_total_ms, 3)
        _obs.finish_span("ok")

        # Validate the plan — skipped for a plan resumed from an execution
        # checkpoint. _plan_dict_from_actions's own docstring
        # (action_executor.py) is explicit that confidence/risk on a
        # checkpoint's stored plan "have no equivalent real source and
        # stay at their honest 0.0 defaults" — it was only ever meant to
        # round-trip enough to re-drive execution, never to be a faithful,
        # re-validatable Plan. Validating that stub against thresholds
        # calibrated for a freshly LLM-generated plan (PlanValidator's
        # default min_confidence=0.1) wrongly re-rejects a plan that
        # ALREADY passed real validation during its ORIGINAL tick — that's
        # the only reason it was executing and reached a pause at all.
        # Confirmed live: this unconditionally rejected every resume
        # through meta.resume_execution_id (approval/negotiation/payment
        # webhook alike) with confidence_below_threshold:0.00<0.1, before
        # ActionExecutor ever got a chance to replay the checkpoint and
        # reach the paused step. Distinct from the OTHER skip_replan
        # source (current_plan_store's Deja Vu / incremental-scheduling
        # reuse, load_current_plan above) — that path's CurrentPlanRecord
        # carries the original plan's REAL confidence/risk from the LLM
        # planner and is still genuinely re-validated here.
        if state.metrics.get("plan_resumed_from_checkpoint"):
            validation = ValidationResult(valid=True, score=1.0)
        else:
            validation = self._plan_validator.validate(plan, belief)

        if not validation.valid:
            state.record_warning(
                "plan",
                f"Plan validation failed: {', '.join(validation.violations)}",
            )
        # Cognitive Loop Verification: validation.valid was computed and
        # then discarded — belief.plan/state.plan were set unconditionally
        # right below regardless, so a rejected plan executed exactly like
        # an accepted one, with only a log line marking the difference.
        # state.metrics is the existing plain dict this function already
        # uses for scalar bookkeeping (no new field) — _execute_plan reads
        # this to actually enforce the rejection.
        state.metrics["plan_valid"] = validation.valid
        state.metrics["plan_violations"] = list(validation.violations)

        belief.plan = plan
        state.plan = plan

        # Record plan in state trace
        trace_detail = f"steps={len(plan.steps)}, confidence={plan.confidence:.2f}, valid={validation.valid}"
        state.add_trace("plan", "plan_generated", trace_detail)
        # add_trace above only appends to state.execution_trace (in-memory,
        # dies with the request) — _obs.event is the actual Lemon sink, same
        # one this function already uses for pipeline.planner_latency_ms
        # above, so this is real telemetry, not just local bookkeeping.
        _obs.event(
            "pipeline.plan_generated",
            actor_id=belief.actor_id,
            steps=len(plan.steps),
            confidence=plan.confidence,
            valid=validation.valid,
        )

        return state

    # ── Stage 4: Execute ──────────────────────────────────────────────────

    def _reject_plan(self, state: CognitiveState, plan: Any, reason: str) -> CognitiveState:
        """Real, honest failure — no capability actually invoked. Same
        idiom the permission-denied path below already uses (one
        ActionOutcome per step, not a new mechanism), reused by both
        PlanValidator rejection and Decide rejection (below) so a
        rejected plan is treated identically regardless of which stage
        rejected it."""
        actor_id = state.actor.actor_id if state.actor else "unknown"
        rejected_actions = tuple(
            # not_attempted=True: real gap this closes — Comparator/
            # _learn_transitions (comparison/integration.py) previously
            # couldn't tell a step that genuinely ran and failed apart
            # from one rejected before any capability was ever invoked
            # (Decision/PlanValidator/compile rejection all funnel
            # through here). Confirmed live: a Decision-rejected plan's
            # never-attempted PaymentConfirmation got learned as a real
            # "failure (p=0.15)" transition, which then dragged down and
            # rejected a LATER, genuinely different attempt's otherwise-
            # viable plan — a self-reinforcing failure loop with no real
            # evidence behind it. _execution_to_graph already excludes a
            # dependency-blocked step from the comparison graph entirely
            # via result["blocked_by_dependency"] for the identical
            # reason; this reuses that same "no real observation, don't
            # learn from it" exclusion for every OTHER never-attempted
            # case instead of adding a third, subtly different mechanism.
            ActionOutcome(
                action_id=f"{actor_id}_step_{i}",
                success=False,
                error=reason,
                result={"not_attempted": True},
            )
            for i in range(len(plan.steps))
        )
        state.actions = list(rejected_actions)
        rejected_result = ExecutionResult(
            actions=rejected_actions,
            success_count=0,
            failure_count=len(rejected_actions),
            total_latency_ms=0.0,
            goal_achieved=False,
        )
        state.execution_result = rejected_result
        # A rejection must be a real, visible ExecutionRecord (Execution
        # History / world_changes), not silently vanish the way an
        # empty-plan tick does. execution_id (set unconditionally at the
        # start of the tick, cognitive_actor.py's _cognitive_tick) must
        # carry through even on a rejected plan — a DECIDE-stage rejection
        # is still a real execution, not an execution without an identity.
        self._record_execution(
            state.belief,
            plan,
            rejected_result,
            state.metrics.get("execution_id", ""),
            grounding_performed=not state.metrics.get("plan_skipped_replan", False),
            grounding_skip_reason=state.metrics.get("plan_skip_reason", ""),
        )
        return state

    async def _execute_plan(self, state: CognitiveState) -> CognitiveState:
        """Execute the plan through the ExecutionEngine.

        Converts plan steps to typed Actions, executes them through
        the capability bus, and collects ActionOutcomes.
        """
        plan = state.belief.plan

        if not plan.steps:
            state.actions = []
            # Real gap this closes (confirmed live via comparison/
            # integration.py::_run_comparison's own diagnostic logging,
            # added alongside this fix): an empty plan (a no-op
            # autonomous tick with genuinely nothing to do) used to
            # return here without ever setting state.execution_result,
            # so Compare's `execution is None` guard silently skipped it
            # -- indistinguishable, from outside the process, from a
            # real failure to execute. A real, empty ExecutionResult
            # (goal_achieved=True: zero actions were required and zero
            # failed) lets Compare correctly classify "genuinely nothing
            # to do" instead of treating every no-op tick as
            # unmeasurable.
            state.execution_result = ExecutionResult(
                actions=(),
                success_count=0,
                failure_count=0,
                total_latency_ms=0.0,
                goal_achieved=True,
            )
            return state

        # Cognitive Loop Verification: a plan the validator rejected
        # (_generate_plan, above) previously executed anyway — only a
        # warning was ever logged, nothing downstream checked it again.
        if not state.metrics.get("plan_valid", True):
            violations = state.metrics.get("plan_violations", [])
            reason = f"Plan rejected by validator: {'; '.join(violations)}"
            return self._reject_plan(state, plan, reason)

        # Cognitive Loop Verification (round 2): Decide can also reject
        # every candidate (state.prediction_result["selected"] is None,
        # e.g. "No viable scenario -- do not execute") — nothing
        # downstream enforced THIS rejection either; confirmed live, a
        # plan whose own Decision said not to execute executed anyway.
        #
        # But a naive "never execute a rejected decision" breaks the
        # system's ability to ever learn anything: _unknown_transition()
        # (prediction/transitions.py) reports 0.5 probability for any
        # action with zero learned history, so a fresh actor's first
        # multi-step plan — every step unknown — compounds well below
        # the rejection threshold by construction, not because anything
        # actually predicts failure. Blocking that means a new actor can
        # never execute anything for the first time, and therefore can
        # never learn something real to predict from next time.
        #
        # The fix: only ENFORCE a rejection built from real (non-
        # "unknown") negative evidence — a genuine predicted failure.
        # A rejection built entirely from "no knowledge yet" is exactly
        # the "explicit override" case: honest absence of information,
        # not a prediction of failure, so execution proceeds — flagged
        # via state.metrics so the persisted Decision (cognitive_actor.py)
        # can stay honest about what actually happened, instead of still
        # reading "do not execute" next to an Execution History that did.
        # Same exemption as the PlanValidator skip above, and for the
        # identical reason: a checkpoint-resumed plan isn't a fresh
        # candidate being decided on for the first time -- it's an
        # ALREADY-committed execution continuing after a real pause
        # (approval/negotiation/payment). Re-running "should we execute
        # this at all" against CURRENT world/prediction state punishes a
        # plan for evidence that accumulated AFTER it already started
        # (e.g. this exact resumed tick's own earlier partial run just
        # taught the TransitionModel real negative evidence about one of
        # its own steps) -- ActionExecutor's own checkpoint-replay
        # (action_executor.py) is what correctly re-uses already-
        # completed steps and only genuinely re-executes the paused one;
        # this Decide-stage gate has no equivalent per-step awareness and
        # rejects the WHOLE plan before ActionExecutor ever gets to run
        # it. Confirmed live: a real UPI Reserve Pay payment resume was
        # rejected with "Decision rejected: All scenarios rejected:
        # Baseline 9% (rejected)" even though PlanValidator's own
        # rejection was already fixed to exempt this exact case.
        prediction = state.prediction_result or {}
        candidates = prediction.get("candidates") or []
        if candidates and prediction.get("selected") is None and not state.metrics.get("plan_resumed_from_checkpoint"):
            best = candidates[0]
            outcomes = (best.get("prediction") or {}).get("predicted_outcomes") or []
            all_unknown = bool(outcomes) and all((o.get("metadata") or {}).get("kind") == "unknown" for o in outcomes)
            if not all_unknown:
                # Learning recovery: real negative evidence exists, so the
                # default is still to reject -- but a fixed, low-probability
                # exploration roll occasionally lets execution proceed
                # anyway, so a stale or since-changed negative transition
                # isn't rejected forever with no way to gather new evidence.
                # `random.random()` is the single source of randomness this
                # introduces; see the exploration_epsilon docstring above.
                if random.random() < self._exploration_epsilon:
                    state.metrics["decision_override_reason"] = (
                        f"epsilon-exploration ({self._exploration_epsilon:.0%} chance) — "
                        "re-attempting a transition with learned negative evidence "
                        "to refresh potentially-stale history instead of rejecting it permanently"
                    )
                else:
                    reason = f"Decision rejected: {prediction.get('rationale') or 'no viable scenario'}"
                    return self._reject_plan(state, plan, reason)
            else:
                state.metrics["decision_override_reason"] = "no prior data for this action — executing to learn"

        # Compilation-hardening pass: validated plan -> executable
        # representation boundary. Runs after both existing rejection
        # guards above (a plan already rejected keeps its exact existing
        # path/reason) and before the permission-check loop below, so
        # every plan that already executes successfully today is
        # completely unaffected -- this only ever rejects a plan that
        # would otherwise have proceeded to execution.
        #
        # plan_id: the REAL, persisted plan_id from plan-hysteresis's
        # CurrentPlanRecord when one exists (state.metrics["decide_new_
        # plan_id"] on "replace", ["decide_current_plan_id"] on "keep" --
        # both set by _run_decide, comparison/integration.py), falling
        # back to this tick's execution_id only when neither is in scope.
        # This is also now the correct value for each Action's
        # causation_id below, finally honoring that field's own
        # pre-existing docstring ("plan_id when available, else the
        # tick's execution_id") instead of always using execution_id.
        plan_id = (
            state.metrics.get("decide_new_plan_id")
            or state.metrics.get("decide_current_plan_id")
            or state.metrics.get("execution_id", "")
        )

        # Populated in _generate_plan() from governance-resolved permissions
        # (see the comment there) — verified associated, not just assumed.
        # Read here (moved above the per-step permission-check loop below,
        # a pure stateless lookup) so compile-time capability resolution,
        # next, can see which steps are permission-denied.
        resolved_permissions = state.belief.metadata.get("_resolved_permissions", ())

        # resolve_capability is an OPTIONAL capability of the wired
        # execution engine (ActionExecutor has it; test/integration
        # engines like MockExecutor/IntegratedExecutionEngine do not) --
        # when absent, resolved_capabilities stays None and compile_plan
        # skips that violation axis entirely, exactly matching those
        # engines' pre-existing behavior (no compile-time capability
        # validation). Never invokes a capability, only probes names.
        #
        # A permission-denied step is skipped here (trusted as-is,
        # exactly like the no-bus fallback) rather than probed: it will
        # never reach ActionExecutor regardless of whether its capability
        # exists (the permission-check loop below denies it unconditionally),
        # so requiring bus registration for it would reject an otherwise
        # valid, already-correctly-handled plan for the wrong reason —
        # governance/permission and capability-existence are different
        # concerns, and compilation must not conflate them.
        resolver = getattr(self._execution_engine, "resolve_capability", None)
        resolved_capabilities = None
        if resolver is not None:
            resolved_capabilities = {}
            for step in plan.steps:
                denied = (
                    bool(step.required_permission)
                    and step.action not in _NO_PERMISSION_NEEDED_ACTIONS
                    and step.required_permission not in resolved_permissions
                )
                resolved_capabilities[step.action] = step.action if denied else resolver(step.action)

        compile_outcome = compile_plan(
            plan,
            plan_id=plan_id,
            actor_id=state.actor_id,
            goal_id=((getattr(plan, "metadata", {}) or {}).get("goal_id") or state.metrics.get("execution_id", "")),
            resolved_capabilities=resolved_capabilities,
        )
        if not compile_outcome.ok:
            state.metrics["compile_violations"] = list(compile_outcome.violations)
            reason = "Plan rejected at compile: " + "; ".join(compile_outcome.violations)
            return self._reject_plan(state, plan, reason)
        state.compiled_plan_graph = compile_outcome.graph
        state.compiled_execution_graph = compile_outcome.execution_graph

        actions, denied = build_actions_from_compiled(
            compile_outcome.graph,
            plan_id=plan_id,
            actor_id=state.actor.actor_id if state.actor else "",
            execution_id=state.metrics.get("execution_id", ""),
            resolved_permissions=resolved_permissions,
            no_permission_actions=_NO_PERMISSION_NEEDED_ACTIONS,
        )
        action_ids = [a.action_id for a in actions]

        # Real gap this closes: no capability had any way to know this
        # tick's execution_id — it lived only in state.metrics, never in
        # the context dict capabilities actually receive. PaymentCapability's
        # audit-trail write (kernel/domains/grocery.py) needs it to
        # correlate a PAYMENT_COMPLETED event to the execution it belongs
        # to; without this it silently wrote an uncorrelated event
        # (confirmed live: query_audit_timeline missed 3 real payments).
        # Same injection pattern _relevant_knowledge_ids already uses on
        # this exact dict at Plan time (see above).
        if isinstance(state.context, dict):
            state.context["execution_id"] = state.metrics.get("execution_id", "")
            # Preserve the complete plan for execution checkpoints.  The
            # Action tuple intentionally omits denied steps, but dependency
            # indices remain anchored to the original plan.steps array.
            state.context["_source_plan"] = plan

        # Execute through the execution engine. execution.active is a
        # plain instance counter (not a new class/collector — GIL makes
        # a simple int increment/decrement safe across the asyncio await
        # points below, and this object already lives for the runtime's
        # whole lifetime, shared across every actor's ticks).
        from src.monkey_brain.kernel.compile import _obs

        self._active_executions = getattr(self, "_active_executions", 0) + 1
        _obs.gauge("execution.active", self._active_executions)
        try:
            exec_result = await self._execution_engine.execute(
                actions,
                state.context,
                execution_graph=compile_outcome.execution_graph,
            )
        finally:
            self._active_executions -= 1
            _obs.gauge("execution.active", self._active_executions)

        if denied:
            permitted_indices = [i for i in range(len(plan.steps)) if i not in denied]
            by_index = dict(zip(permitted_indices, exec_result.actions))
            by_index.update(denied)
            merged = tuple(by_index[i] for i in sorted(by_index))
            success_count = sum(1 for o in merged if o.success)
            exec_result = ExecutionResult(
                actions=merged,
                success_count=success_count,
                failure_count=len(merged) - success_count,
                total_latency_ms=exec_result.total_latency_ms,
                goal_achieved=success_count == len(merged),
                event_publish_ms=exec_result.event_publish_ms,
            )

        # Performance analysis instrumentation only (measurement, not a
        # behavior change): "Act" (total Execute-stage time, already
        # separately captured in state.stage_durations["execute"] by
        # run_stages()) vs. "Perturbation Publication" (the
        # _publish_action_event sub-slice of it) as two distinct entries.
        state.metrics.setdefault("stage_timings_ms", {})["perturbation_publish_ms"] = exec_result.event_publish_ms

        state.actions = list(exec_result.actions)
        state.execution_result = exec_result

        self._record_execution(
            state.belief,
            plan,
            exec_result,
            state.metrics.get("execution_id", ""),
            grounding_performed=not state.metrics.get("plan_skipped_replan", False),
            grounding_skip_reason=state.metrics.get("plan_skip_reason", ""),
        )

        return state

    def _record_execution(
        self,
        belief: BeliefState,
        plan: Any,
        exec_result: Any,
        execution_id: str = "",
        grounding_performed: bool = True,
        grounding_skip_reason: str = "",
    ) -> None:
        from src.monkey_brain.kernel.timeline.entry import TimelineKind
        from src.monkey_brain.kernel.timeline.store import TimelineStore

        if exec_result.failure_count == 0:
            outcome = "success"
        elif exec_result.success_count == 0:
            outcome = "failure"
        else:
            outcome = "partial"
        failed = next((a for a in exec_result.actions if not a.success), None)
        # exec_result.actions is 1:1 with plan.steps by index (the denied-
        # permission merge above already reconstructs full alignment when
        # any step was denied; when nothing was denied the actions list
        # was built by iterating plan.steps in order, skipping none) — so
        # step.action (the real capability name, e.g. "OrderConfirmation")
        # is what a human needs here, not action_outcome.action_id (an
        # opaque "{actor_id}_step_{i}" string with no capability name in
        # it at all — found live: threading action_id through here first
        # produced "82ea856b...4_step_1: no order to confirm...", useless
        # for telling an operator WHICH step failed).
        all_failures = tuple(
            f"{step.action}: {action_outcome.error}"
            for step, action_outcome in zip(plan.steps, exec_result.actions)
            if not action_outcome.success
        )

        # Minimal Lemon metrics layer: execution/step counters emitted
        # from this exact boundary (not reconstructed later by querying
        # the TimelineStore record below) — outcome/all_failures/
        # exec_result are already computed here for the durable record,
        # this just also counts them. success -> completed; failure/
        # partial -> failed (the runtime has no tick-level cancelled/
        # timed_out signal to report honestly, so those label values are
        # reserved, never emitted, rather than fabricated).
        from src.monkey_brain.kernel.compile import _obs

        _obs.counter("execution.total", status="completed" if outcome == "success" else "failed")
        _obs.histogram("execution.duration_ms", exec_result.total_latency_ms)
        for step, action_outcome in zip(plan.steps, exec_result.actions):
            if action_outcome.success:
                step_status = "succeeded"
            elif (action_outcome.error or "").startswith("blocked: dependency"):
                step_status = "blocked"
            else:
                step_status = "failed"
            _obs.counter("execution.step.total", status=step_status)
            _obs.histogram("execution.step.duration_ms", action_outcome.latency_ms)

        plan_summary = tuple(step.action for step in plan.steps)
        capabilities_used = tuple(dict.fromkeys(plan_summary))
        # belief.goal.name is now the actor's real standing goal ALONE
        # (cognitive_actor.py::_cognitive_tick no longer folds the one-off
        # triggering text into it, to stop Goal Timeline pollution — see
        # that change's own comment). Reconstructing the fuller text here
        # (name + this tick's own description, when there is one) keeps
        # this EXECUTION record and the episodic text below exactly as
        # specific as before ("buy groceries efficiently Purchase 1 dozen
        # large eggs"), even though the Goal Timeline itself now only
        # ever sees the clean standing goal.
        full_goal_text = (
            f"{belief.goal.name} {belief.goal.description}".strip() if belief.goal.description else belief.goal.name
        )
        TimelineStore().record(
            TimelineKind.EXECUTION,
            actor_id=belief.actor_id,
            goal=full_goal_text,
            plan_summary=plan_summary,
            outcome=outcome,
            failure_reason=all_failures[0] if all_failures else "",
            step_failures=all_failures,
            capabilities_used=capabilities_used,
            # execution_id already self-correlates this tick — reuse it
            # rather than minting a second id for the same operation.
            correlation_id=execution_id,
            # Cognitive State refactor: exec_result.total_latency_ms was
            # already computed here and simply dropped before — real
            # per-tick duration, not derived/estimated. world_changes:
            # see _describe_world_changes's own docstring.
            metadata={
                "duration_ms": exec_result.total_latency_ms,
                "world_changes": _describe_world_changes(plan.steps, exec_result.actions),
                "execution_id": execution_id,
                # Grounding Graph execution-selector rule: an execution
                # with no ContextSnapshot must be able to say so honestly
                # ("reused the prior plan, grounding not performed") rather
                # than the frontend guessing from the snapshot's absence
                # alone — a snapshot can also be missing because save
                # failed, which is a different, real fact.
                "grounding_performed": grounding_performed,
                "grounding_skip_reason": grounding_skip_reason,
            },
        )
        self._record_episodic_experience(belief, plan, exec_result, execution_id, outcome, failed, full_goal_text)

    def _record_episodic_experience(
        self,
        belief: BeliefState,
        plan: Any,
        exec_result: Any,
        execution_id: str,
        outcome: str,
        failed: Any,
        full_goal_text: str,
    ) -> None:
        """Real gap this closes (confirmed live: an actor's Experiences AND
        Executions Referenced retrieval — ContextConstructionEngine.
        _search_memory's kind="experience"/kind="execution" buckets — were
        permanently empty after every real execution, for every actor,
        because nothing in the tick pipeline ever called MemoryManager.
        record_experience(); the only real caller anywhere in the kernel
        was membership.py's join/leave events. This uses the exact same
        real outcome data _record_execution just wrote to the Timeline,
        recorded a second (and third) time into CognitiveMemory so it
        becomes genuinely retrievable on a later tick — not a new,
        parallel capture mechanism."""
        if not plan.steps:
            return  # a no-op tick has nothing memorable to record
        memory_manager = (
            getattr(self._context_engine, "_memory_manager", None) if self._context_engine is not None else None
        )
        if memory_manager is None:
            return
        if outcome == "success":
            text = f"Completed goal: {full_goal_text}"
            confidence = 0.9
        elif outcome == "partial":
            text = f"Partially completed goal: {full_goal_text} ({exec_result.success_count}/{len(exec_result.actions)} steps succeeded)"
            confidence = 0.5
        else:
            # Real gap this closes: a failure's raw error text (e.g. "planner
            # selected 'product_473002...', which was never offered...")
            # got recorded verbatim into episodic memory, which is fed
            # straight back into a LATER attempt's own prompt as "Relevant
            # experiences" — confirmed live, a self-perpetuating loop: the
            # rejection's own error text handed the model the exact bad id
            # to copy again, reproducing the identical rejection every
            # retry. _sanitize_error_for_memory strips anything that looks
            # like a raw entity id (product_.../store_.../order_..., or a
            # bare id-shaped token) while keeping the human-readable reason
            # ("was never offered in this execution's grounding") — enough
            # for the actor to know THIS KIND of thing failed before,
            # without reintroducing the exact specific value that caused it.
            error_text = _sanitize_error_for_memory(failed.error) if failed and failed.error else ""
            text = f"Failed to complete goal: {full_goal_text}" + (f" — {error_text}" if error_text else "")
            confidence = 0.2
        timestamp = time.time()
        try:
            memory_manager.record_experience(
                belief.actor_id,
                kind="experience",
                text=text,
                metadata={
                    "timestamp": timestamp,
                    "execution_id": execution_id,
                    "outcome": outcome,
                    "confidence": confidence,
                },
            )
        except Exception:
            logger.exception(
                "record_experience(kind=experience) failed for actor %s execution %s — Timeline record above already succeeded, so the tick itself is unaffected",
                belief.actor_id,
                execution_id,
            )
        # Same real gap, same real data, for the "Executions Referenced" /
        # prior-executions bucket (_bucket_memory_nodes's kind="execution"
        # list) — a distinct retrieval bucket from "experience" above, so
        # it needs its own record_experience() call, not a rename of the
        # one above. A short reference label (goal + real outcome), not
        # the narrative sentence — kernel/pipeline/planning/domain.py's
        # RetrievedItem has no dedicated `outcome` field, so the real
        # outcome is folded into the text itself rather than a field the
        # frontend couldn't read anyway.
        try:
            memory_manager.record_experience(
                belief.actor_id,
                kind="execution",
                text=f"{full_goal_text} — {outcome}",
                metadata={
                    "timestamp": timestamp,
                    "execution_id": execution_id,
                    "outcome": outcome,
                    "confidence": confidence,
                },
            )
        except Exception:
            logger.exception(
                "record_experience(kind=execution) failed for actor %s execution %s — Timeline record above already succeeded, so the tick itself is unaffected",
                belief.actor_id,
                execution_id,
            )

    async def _observe_outcome(self, state: CognitiveState) -> CognitiveState:
        """Capture the results of execution."""
        from src.monkey_brain.kernel.compile import _obs

        exec_result = state.execution_result
        if exec_result:
            state.outcome = {
                "actions_executed": exec_result.success_count + exec_result.failure_count,
                "success_count": exec_result.success_count,
                "failure_count": exec_result.failure_count,
                "goal_achieved": exec_result.goal_achieved,
                "total_latency_ms": exec_result.total_latency_ms,
            }
        else:
            state.outcome = {
                "actions_executed": 0,
                "success_count": 0,
                "failure_count": 0,
                "goal_achieved": False,
            }

        # Lemon metrics (previously zero telemetry on this stage — real
        # values only, off the outcome dict this call just built).
        _obs.counter("observe_outcome.total", goal_achieved=str(state.outcome["goal_achieved"]))
        _obs.gauge("observe_outcome.actions_executed", float(state.outcome["actions_executed"]))
        return state

    # ── Stage 5: Learn ────────────────────────────────────────────────────
    # this should happen after step 6 not here.
    # here it will learn nothing epistemic loss will be empty so will the execution graph losss
    async def _learn(self, state: CognitiveState) -> CognitiveState:
        """Update internal knowledge from the observed outcome.

        Generates specific, actionable hypotheses from:
        - Transition model probabilities (risk identification)
        - Fact threshold breaches (capacity, temperature, fuel)
        - Comparison losses (prediction accuracy feedback)
        - Execution outcomes (success/failure patterns)
        """
        belief = state.belief
        outcome = state.outcome
        comparison = getattr(state, "comparison_result", None)
        transition_model = getattr(state, "transition_model", None)

        # === Transition model risk hypotheses ===
        if transition_model and transition_model.known_transitions:
            # known_transitions is keyed (goal_key, action_key) (Cross-Goal
            # Plan Contamination fix) -- unpack both parts rather than
            # binding the whole tuple to one name, which previously
            # rendered hypothesis text as e.g. "HIGH RISK: ('buy
            # groceries', 'Payment') has only 15%..." instead of a
            # readable action label. Same transition, just a legible one.
            for (
                goal_key,
                action_key,
            ), transitions in transition_model.known_transitions.items():
                if not transitions:
                    continue
                t = transitions[0]
                label = f"{action_key} (goal: {goal_key})" if goal_key else action_key
                if t.probability < 0.5:
                    belief.add_hypothesis(
                        claim=f"HIGH RISK: {label} has only {t.probability:.0%} success rate — consider alternative or add redundancy",
                        confidence=1.0 - t.probability,
                        evidence=[
                            f"transition:{action_key}:p={t.probability:.3f}",
                            f"kind:{t.kind.value}",
                        ],
                    )
                elif t.probability < 0.7:
                    belief.add_hypothesis(
                        claim=f"MONITOR: {label} reliability at {t.probability:.0%} — schedule preventive action",
                        confidence=0.7,
                        evidence=[f"transition:{action_key}:p={t.probability:.3f}"],
                    )

        # === Fact threshold warnings ===
        for fact in belief.facts:
            entity = fact.entity
            attr = fact.attribute
            val = fact.value

            # Capacity warnings (numeric values approaching limits)
            if isinstance(val, (int, float)):
                if attr == "current_load" and isinstance(val, (int, float)) and val > 70:
                    belief.add_hypothesis(
                        claim=f"{entity}.{attr} at {val:.0f}% capacity — risk of overflow, redistribute load",
                        confidence=0.8,
                        evidence=[f"fact:{entity}.{attr}={val}"],
                    )
                if attr == "fuel_level" and isinstance(val, float) and val < 0.5:
                    belief.add_hypothesis(
                        claim=f"{entity} fuel at {val:.0%} — refuel before next dispatch or switch to {entity.replace('2', '1')}",
                        confidence=0.9,
                        evidence=[f"fact:{entity}.{attr}={val}"],
                    )
                if attr == "maintenance_due_km" and isinstance(val, (int, float)) and val < 100:
                    belief.add_hypothesis(
                        claim=f"{entity} maintenance overdue ({val:.0f} km remaining) — ground vehicle until serviced",
                        confidence=0.95,
                        evidence=[f"fact:{entity}.{attr}={val}"],
                    )
                if attr == "temperature" and isinstance(val, float) and val > 4.0:
                    belief.add_hypothesis(
                        claim=f"{entity} temperature {val}°C above optimal — check compressor, risk of spoilage",
                        confidence=0.7,
                        evidence=[f"fact:{entity}.{attr}={val}"],
                    )
                if attr == "current_queue" and isinstance(val, (int, float)) and val > 3:
                    belief.add_hypothesis(
                        claim=f"{entity} queue at {val:.0f} — bottleneck, add capacity or prioritize critical batches",
                        confidence=0.75,
                        evidence=[f"fact:{entity}.{attr}={val}"],
                    )
                if attr == "current_temperature" and isinstance(val, (int, float)) and val > 50:
                    belief.add_hypothesis(
                        claim=f"{entity} at {val}°C — approaching max, reduce throughput or add cooling",
                        confidence=0.8,
                        evidence=[f"fact:{entity}.{attr}={val}"],
                    )

            # Reliability warnings
            if attr == "reliability" and isinstance(val, float) and val < 0.8:
                belief.add_hypothesis(
                    claim=f"{entity} reliability at {val:.0%} — consider secondary supplier or safety stock",
                    confidence=0.8,
                    evidence=[f"fact:{entity}.{attr}={val}"],
                )

        # === Comparison loss hypotheses ===
        if comparison:
            actor_loss = comparison.get("actor_loss", 0)
            world_loss = comparison.get("world_loss", 0)

            if actor_loss > 0.6:
                belief.add_hypothesis(
                    claim=f"Prediction accuracy poor (actor_loss={actor_loss:.2f}) — world model needs more observation cycles",
                    confidence=0.8,
                    evidence=[
                        f"comparison:actor_loss={actor_loss:.3f}",
                        f"comparison:world_loss={world_loss:.3f}",
                    ],
                )
            if world_loss > 0.8:
                belief.add_hypothesis(
                    claim=f"World model mismatch (world_loss={world_loss:.2f}) — entity states diverging from predictions, refresh observations",
                    confidence=0.85,
                    evidence=[f"comparison:world_loss={world_loss:.3f}"],
                )

        # === Execution outcome hypothesis ===
        success_count = outcome.get("success_count", 0)
        failure_count = outcome.get("failure_count", 0)
        actions_executed = success_count + failure_count
        if actions_executed > 0:
            success_rate = success_count / actions_executed
            if failure_count > 0:
                belief.add_hypothesis(
                    claim=f"Execution: {failure_count}/{actions_executed} actions failed ({1 - success_rate:.0%} failure rate) — identify root causes from transition model",
                    confidence=0.8,
                    evidence=[
                        f"outcome:success={success_count}",
                        f"outcome:fail={failure_count}",
                    ],
                )

        # Record what was learned
        new_hypotheses = len(belief.hypotheses)
        if new_hypotheses > 0:
            belief.record_learning(
                what=f"Generated {new_hypotheses} actionable hypotheses from {len(belief.facts)} facts + transition model",
                evidence=[h.claim for h in belief.hypotheses[:5]],
                confidence=0.7,
            )

        state.learning = {
            "updated": len(belief.hypotheses) > 0,
            "hypotheses_generated": len(belief.hypotheses),
        }
        return state

    # ── Stage 6: Compile Φ ────────────────────────────────────────────────

    async def _compile_phi(self, state: CognitiveState) -> CognitiveState:
        """Compile learned knowledge into executable policy.

        Placeholder where Q-values become transition weights.
        """
        state.phi = None
        return state

    # ── Stage 7: Predict ──────────────────────────────────────────────────

    async def _predict(self, state: CognitiveState) -> CognitiveState:
        """Generate specific predictions from the transition model and plan.

        Each plan step gets a prediction with:
        - Expected success probability from learned transitions
        - Risk factors if probability is low
        - Projected world state delta
        """
        belief = state.belief
        plan = belief.plan
        transition_model = getattr(state, "transition_model", None)
        goal = belief.goal

        if not plan.steps:
            belief.record_prediction(
                description=f"No plan generated for '{goal.name}' — cannot predict outcome",
                confidence=0.0,
                based_on=[],
            )
            return state

        # Build step-by-step predictions from transition model
        step_predictions = []
        cumulative_success = 1.0
        risk_factors = []

        # known_transitions is keyed (goal_key, action_key) (Cross-Goal Plan
        # Contamination fix) -- a bare action_key membership check against
        # it can never match (str != tuple), which silently made this
        # branch permanently dead: every step fell to the "unknown" case
        # below regardless of real learned history. goal_key is derived
        # identically to the Predict-side formula elsewhere in this module
        # (prediction/simulation.py::simulate(), no belief.goal fallback)
        # so this lookup uses the same key Learning actually wrote under.
        from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal

        # Widened to match _run_decide's own goal_key fix (plan_hysteresis
        # was silently reusing an unrelated request's stale plan because
        # `plan.goal` only ever carries the standing goal NAME, never the
        # one-off triggering description). Predict/Learn's goal_key must
        # widen the SAME way, in lockstep across every read/write site
        # (this file, prediction/simulation.py, prediction/
        # counterfactuals.py, comparison/integration.py's Learn stage) --
        # confirmed live: an actor with ANY standing goal whose one-off
        # requests keep landing on the SAME narrow key accumulates learned
        # failures from one request onto every unrelated later request
        # sharing that standing goal, eventually driving scenario
        # probability to 0% regardless of what's actually being planned.
        goal_key = canonicalize_goal(f"{plan.goal} {goal.description}".strip())

        for i, step in enumerate(plan.steps):
            action_key = step.action

            if transition_model and (goal_key, action_key) in transition_model.known_transitions:
                transitions = transition_model.known_transitions[(goal_key, action_key)]
                t = transitions[0] if transitions else None
                if t:
                    p_success = t.probability
                    cumulative_success *= p_success
                    step_predictions.append(f"Step {i + 1} '{action_key}': {p_success:.0%} success")
                    if p_success < 0.5:
                        risk_factors.append(f"Step {i + 1} '{action_key}' is unreliable ({p_success:.0%})")
                    elif p_success < 0.7:
                        risk_factors.append(f"Step {i + 1} '{action_key}' needs monitoring ({p_success:.0%})")
            else:
                step_predictions.append(f"Step {i + 1} '{action_key}': unknown (no learned transitions)")
                cumulative_success *= 0.5  # conservative estimate

        # Overall prediction
        if risk_factors:
            description = (
                f"Plan for '{goal.name}': {len(plan.steps)} steps, "
                f"overall {cumulative_success:.0%} success probability. "
                f"RISKS: {'; '.join(risk_factors[:3])}"
            )
        else:
            description = (
                f"Plan for '{goal.name}': {len(plan.steps)} steps, "
                f"overall {cumulative_success:.0%} success probability. "
                f"Steps: {', '.join(s.action for s in plan.steps[:3])}"
            )

        belief.record_prediction(
            description=description,
            confidence=cumulative_success,
            based_on=[s.action for s in plan.steps],
        )

        # Also record individual step predictions for traceability
        for pred_text in step_predictions:
            belief.record_prediction(
                description=pred_text,
                confidence=cumulative_success,
                based_on=[],
            )

        return state

    # ── Stage 8: Commit ───────────────────────────────────────────────────

    async def _commit(self, state: CognitiveState) -> CognitiveState:
        """Commit: produce a CognitiveDelta capturing what changed.

        The delta is purely cognitive — it describes what changed.
        Infrastructure (checkpoint, world update, replication) consumes
        the delta but the runtime never performs those side effects.
        """
        belief = state.belief
        actor = state.actor

        belief.decay_and_prune()

        state.cognitive_delta = CognitiveDelta(
            actor_id=actor.actor_id if actor else "",
            tenant_id=actor.tenant_id if actor else "",
            new_facts=tuple(
                {
                    "entity": f.entity,
                    "attribute": f.attribute,
                    "value": f.value,
                    "confidence": f.confidence,
                }
                for f in belief.facts
            ),
            updated_beliefs=belief.summary(),
            actions=tuple(state.actions),
            predictions=tuple(
                {
                    "description": p.description,
                    "confidence": p.confidence,
                }
                for p in belief.predictions
            ),
            learning_updates=tuple(
                {
                    "what": l.what,
                    "confidence": l.confidence,
                }
                for l in belief.learned_updates
            ),
            events=(),
            metrics=state.metrics,
            cycle_number=actor.cycle_count if actor else 0,
        )
        return state

    # ── Result Building ───────────────────────────────────────────────────

    def _build_result(self, state: CognitiveState) -> dict[str, Any]:
        """Build the execution result from cognitive state."""
        from dataclasses import asdict

        belief = state.belief
        question = state.compiled.request.question if state.compiled else ""

        answer = ""
        if state.actions:
            # Find the last successful action's result
            for outcome in reversed(state.actions):
                if outcome.success and outcome.result:
                    if isinstance(outcome.result, dict):
                        answer = outcome.result.get("answer", str(outcome.result))
                    else:
                        answer = str(outcome.result)
                    break
        if not answer:
            answer = f"Processed: {question}"

        return {
            "answer": answer,
            "status": "success" if not state.has_errors() else "partial",
            "belief": belief.summary(),
            "plan": {
                "goal": belief.plan.goal,
                "steps": [
                    {
                        "action": s.action,
                        "description": s.description,
                        "preconditions": (list(s.preconditions) if hasattr(s, "preconditions") else []),
                        "expected_outcome": (s.expected_outcome if hasattr(s, "expected_outcome") else ""),
                        "cost": s.cost if hasattr(s, "cost") else 0.0,
                        "confidence": s.confidence if hasattr(s, "confidence") else 0.0,
                    }
                    for s in belief.plan.steps
                ],
                "preconditions": (list(belief.plan.preconditions) if hasattr(belief.plan, "preconditions") else []),
                "expected_outcomes": (
                    list(belief.plan.expected_outcomes) if hasattr(belief.plan, "expected_outcomes") else []
                ),
                "confidence": belief.plan.confidence,
                "cost": belief.plan.cost if hasattr(belief.plan, "cost") else 0.0,
                "risk": belief.plan.risk if hasattr(belief.plan, "risk") else 0.0,
                "planner": (belief.plan.planner if hasattr(belief.plan, "planner") else ""),
            },
            "actions": [
                {
                    "action_id": a.action_id,
                    "success": a.success,
                    "error": a.error,
                    "latency_ms": a.latency_ms,
                }
                for a in state.actions
            ],
            "outcome": state.outcome,
            "phi": state.phi,
            "predictions": len(belief.predictions),
            "metrics": state.metrics,
            "errors": state.errors,
            "stage_durations": state.stage_durations,
            "cognitive_delta": (asdict(state.cognitive_delta) if state.cognitive_delta else None),
        }


CognitiveEngine = CognitiveRuntime
