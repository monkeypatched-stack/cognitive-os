"""
CognitiveOS — The operating system for a single autonomous cognitive actor.

Each actor has exactly one CognitiveOS instance. The OS provides the
complete cognitive infrastructure: world, messaging, planning, execution,
learning, transition models, and objective scoring.

Architecture:
    Actor ←→ CognitiveOS (one-to-one)
    Actor API: self.os.world(), self.os.send_message(), self.os.transition()

Five explicit responsibilities:

Actor Ownership
    actor          — the single owned actor (read-only)
    set_actor()    — bind actor to this OS (once)

Infrastructure Services
    world()        — read-only shared world
    send_message() — inter-agent messaging (trust-enforced)
    broadcast()    — broadcast to society peers (trust-enforced)
    get_messages() — pending messages (trust-filtered)
    transition()   — learned transition model

Trust Enforcement
    _check_trust()     — is communication allowed?
    _get_actor_trust() — current trust level
    _update_trust()    — evolve trust from outcomes

Reasoning
    evaluate_goals()   — which goals are achievable?
    match_capabilities() — which capabilities can achieve goals?
    check_resources()  — does actor have required resources?
    synthesize()       — combine ontology types into decisions

Cognitive Pipeline (delegated, not owned)
    BeliefFormation — Observe → Believe → Plan → Execute → Learn
    TransitionModel — learned world dynamics across ticks

Trust enforcement:
    Low-trust actors cannot communicate. Messages are filtered by trust
    threshold at send, broadcast, and receive time.
"""

from __future__ import annotations

import logging
from typing import Any

# Runtime Encapsulation Refactor follow-up: these dataclasses (and the
# evaluate_goals/match_capabilities/check_resources/synthesize logic that
# used them) now live in reasoning_runtime.py::DecisionEngine — a genuine
# Reasoning responsibility, not CognitiveOS's own. Re-exported here for
# backward-compatible imports (tests import these directly from this
# module) and because CognitiveOS.evaluate_goals() etc. remain as one-line
# delegating wrappers below.
from src.monkey_brain.kernel.cognitive_os.reasoning_runtime import (
    GoalEvaluation,
    CapabilityMatch,
    ResourceCheck,
    DecisionSynthesis,
    DecisionEngine,
)
from src.monkey_brain.kernel.cognitive_os.actor_kernel_context import ActorKernelContext

logger = logging.getLogger("agentos.cognitive_os")

TRUST_COMMUNICATION_THRESHOLD = 0.3


class ActorGraphExecutionState:
    """Per-Actor CognitiveOS Isolation refactor. The SHARED tenant world
    tensor (kernel/graph_manager.py's GraphManager, kernel/compile/
    world_tensor.py's get_world_tensor(tenant_id)) legitimately stays
    shared -- it's an immutable-per-tick graph DEFINITION, deliberately
    tenant-scoped ("World/Policy split": actions are masks over one
    shared world tensor, not per-actor graphs). What was genuinely
    missing was somewhere to hold the ACTOR's own mutable execution state
    over that shared definition — which node it's currently at, its own
    step-by-step execution history, which transitions it has live. One
    instance per actor, never shared."""

    def __init__(self, tenant_id: str = "default") -> None:
        self.tenant_id = tenant_id
        self.current_node: str | None = None
        self._history: list[dict[str, Any]] = []
        self.active_transitions: list[Any] = []

    def record_step(self, node: str, outcome: Any = None) -> None:
        self.current_node = node
        self._history.append({"node": node, "outcome": outcome})

    @property
    def execution_history(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._history)

    def world_tensor(self) -> Any:
        """Read access to the shared, tenant-scoped world tensor this
        actor's execution state is defined over — a reference, never a
        per-actor copy (duplicating it would violate the "don't
        needlessly duplicate immutable/tenant-shared infrastructure"
        rule this refactor is bound by)."""
        from src.monkey_brain.kernel.compile.world_tensor import get_world_tensor

        return get_world_tensor(self.tenant_id)


class ActorComparatorView:
    """Per-actor accessor over the shared ComparatorRuntime, always scoped
    by THIS actor's own current execution_id — never the process-wide
    "most recent, whoever it was for" default (that default remains, on
    ComparatorRuntime.get_last_comparison() itself, for the one real
    legitimate caller: the operator-facing GET /compare/history route)."""

    def __init__(self, kernel_context: ActorKernelContext) -> None:
        self._kernel_context = kernel_context

    @property
    def last_comparison(self) -> dict[str, Any] | None:
        execution_id = self._kernel_context.current_execution_id
        if not execution_id:
            return None
        from src.monkey_brain.kernel.comparator_runtime import get_comparator_runtime

        return get_comparator_runtime().get_last_comparison(execution_id)


class ActorSimulationView:
    """Per-actor cache of this actor's own most recent simulation result.
    SimulationRuntime itself (kernel/simulation_runtime.py) was audited
    and confirmed to hold no shared mutable per-call state of its own
    (unlike ComparatorRuntime.last_comparison) — .run(ir, mongo_client)
    returns its result directly to the caller with nothing cached on the
    shared singleton. This view exists so an actor's own last result has
    a genuine, actor-owned home instead of being discarded or (if some
    future caller cached it globally) risking the same class of leak
    already fixed on ComparatorRuntime."""

    def __init__(self) -> None:
        self.last_result: Any = None

    def record(self, result: Any) -> None:
        self.last_result = result


class CognitiveOS:
    """The operating system for a single actor.

    One-to-one: each actor has exactly one CognitiveOS.

    Five explicit responsibilities:

    Actor Ownership
        actor          — the single owned actor (read-only)
        set_actor()    — bind actor to this OS (once)

    Infrastructure Services
        world()        — read-only shared world
        send_message() — inter-agent messaging (trust-enforced)
        broadcast()    — broadcast to society peers (trust-enforced)
        get_messages() — pending messages (trust-filtered)
        transition()   — learned transition model

    Trust Enforcement
        _check_trust()     — is communication allowed?
        _get_actor_trust() — current trust level
        _update_trust()    — evolve trust from outcomes

    Reasoning
        evaluate_goals()    — which goals are achievable?
        match_capabilities() — which capabilities can achieve goals?
        check_resources()   — does actor have required resources?
        synthesize()        — combine ontology types into decisions

    Cognitive Pipeline
        BeliefFormation — Observe → Believe → Plan → Execute → Learn
        TransitionModel — learned world dynamics across ticks
    """

    def __init__(self, world: Any = None):
        self._world = world
        self._actor: Any = None
        self._engine = None
        self._transition_model = None
        self._message_bus: list[dict] = []
        self._society_runtime: Any = None
        self._decision_engine = DecisionEngine()
        # Per-Actor CognitiveOS Isolation refactor: real, actor-owned
        # execution state that previously had no home at all. Constructed
        # here (not lazily) so `os_a.graph_manager is not os_b.graph_manager`
        # etc. hold from the moment two CognitiveOS instances exist, even
        # before an actor is bound — set_actor() below re-keys
        # _kernel_context to the real actor_id once known.
        self._kernel_context = ActorKernelContext(actor_id="")
        self._graph_execution_state = ActorGraphExecutionState()
        self._comparator_view = ActorComparatorView(self._kernel_context)
        self._simulation_view = ActorSimulationView()

    # ── Actor Ownership ──────────────────────────────────────

    @property
    def actor(self) -> Any:
        return self._actor

    def set_actor(self, actor: Any) -> None:
        if self._actor is not None:
            raise RuntimeError(
                f"CognitiveOS already owns actor '{self._actor.entity_id}'. "
                f"One OS, one actor. Create a new CognitiveOS for a new actor."
            )
        self._actor = actor
        actor.os = self
        actor.set_world(self._world)
        self._decision_engine.bind_actor(actor)
        actor_id = getattr(actor, "entity_id", "") or ""
        tenant_id = getattr(actor, "tenant_id", None) or "default"
        self._kernel_context.actor_id = actor_id
        self._kernel_context.tenant_id = tenant_id
        self._graph_execution_state.tenant_id = tenant_id
        logger.info("CognitiveOS: bound to actor %s", getattr(actor, "entity_id", "?"))

    def set_society_runtime(self, runtime: Any) -> None:
        self._society_runtime = runtime

    # ── Infrastructure Services ──────────────────────────────

    def world(self) -> Any:
        return self._world

    # ── Trust Enforcement ────────────────────────────────────

    def _check_trust(self, target_actor_id: str) -> bool:
        if self._actor is None:
            return False
        affiliations = getattr(self._actor, "_affiliations", None)
        if affiliations is None:
            return True
        trust = affiliations.get_trust(target_actor_id)
        return trust >= TRUST_COMMUNICATION_THRESHOLD

    def _get_actor_trust(self, target_actor_id: str) -> float:
        if self._actor is None:
            return 0.0
        affiliations = getattr(self._actor, "_affiliations", None)
        if affiliations is None:
            return 0.5
        return affiliations.get_trust(target_actor_id)

    def _update_trust(self, target_actor_id: str, goal_achieved: bool) -> None:
        if self._actor is None:
            return
        affiliations = getattr(self._actor, "_affiliations", None)
        if affiliations is not None:
            affiliations.update_trust_from_outcome(
                target_actor_id,
                goal_achieved=goal_achieved,
            )

    # ── Messaging (trust-enforced) ───────────────────────────

    def send_message(self, to_actor: str, msg_type: str, payload: dict = None) -> bool:
        if not self._check_trust(to_actor):
            logger.warning(
                "Message BLOCKED: %s → %s (trust=%.2f < %.2f)",
                self._actor.entity_id if self._actor else "?",
                to_actor,
                self._get_actor_trust(to_actor),
                TRUST_COMMUNICATION_THRESHOLD,
            )
            return False

        if self._society_runtime is None:
            self._message_bus.append(
                {
                    "from": self._actor.entity_id if self._actor else "?",
                    "to": to_actor,
                    "type": msg_type,
                    "payload": payload or {},
                }
            )
            return True

        self._society_runtime.send_message(
            self._actor.entity_id,
            to_actor,
            msg_type,
            payload,
        )
        return True

    def broadcast(self, msg_type: str, payload: dict = None) -> int:
        if self._society_runtime is None:
            return 0

        sent = 0
        for target in self._society_runtime.active_actors():
            if target.actor_id != (self._actor.entity_id if self._actor else "?"):
                if self._check_trust(target.actor_id):
                    self._society_runtime.send_message(
                        self._actor.entity_id,
                        target.actor_id,
                        msg_type,
                        payload,
                    )
                    sent += 1
                else:
                    logger.debug(
                        "Broadcast BLOCKED to %s (trust=%.2f)",
                        target.actor_id,
                        self._get_actor_trust(target.actor_id),
                    )
        return sent

    def get_messages(self) -> list[dict]:
        if self._society_runtime is None:
            return list(self._message_bus)

        all_msgs = self._society_runtime.get_messages_for(
            self._actor.entity_id,
        )

        filtered = []
        for msg in all_msgs:
            sender = msg.get("from", "")
            if self._check_trust(sender):
                filtered.append(msg)
            else:
                logger.debug(
                    "Message FILTERED from %s (trust=%.2f)",
                    sender,
                    self._get_actor_trust(sender),
                )
        return filtered

    def transition(self) -> Any:
        return self._get_transition_model()

    # ── Per-Actor Execution Domain (Per-Actor CognitiveOS Isolation) ────
    # Real, actor-owned objects -- not aliases onto process-wide
    # singletons, and not empty wrappers with borrowed state. Each is
    # constructed fresh in __init__ above, one per CognitiveOS instance.

    @property
    def kernel(self) -> ActorKernelContext:
        """This actor's own execution-scoped kernel state (current
        execution_id, interrupts, run_id history). The Kernel CLASS
        itself (kernel/kernel.py) stays one process-wide singleton on
        purpose -- it owns genuinely-global boot-time infrastructure
        (provider registration, DB connections), which this refactor's
        own classification rule ("immutable/genuinely-global infra MAY
        be shared") does not require duplicating. ActorKernelContext.
        shared_kernel() gives read-only access to it when actor-facing
        code genuinely needs shared infrastructure."""
        return self._kernel_context

    @property
    def runtime(self) -> Any:
        """This actor's own ComparisonIntegratedPolicy (.reasoning/
        .execution) — already constructed fresh per actor by
        build_comparison_integrated_runtime() at registration time
        (kernel/society/runtime.py), never cached/shared across actors.
        This property just gives it the name this refactor's isolation
        tests expect."""
        return self._get_policy()

    @property
    def graph_manager(self) -> ActorGraphExecutionState:
        """This actor's own graph EXECUTION state (current_node,
        execution_history, active_transitions) — distinct from the
        shared, tenant-scoped world tensor definition it executes over
        (available read-only via .world_tensor())."""
        return self._graph_execution_state

    @property
    def execution_state(self) -> ActorGraphExecutionState:
        """Alias for graph_manager under the name this refactor's spec
        uses for the same concept."""
        return self._graph_execution_state

    @property
    def comparator(self) -> ActorComparatorView:
        """This actor's own view of its comparison results, always
        scoped by its own current execution_id (see ActorComparatorView
        and the ComparatorRuntime.last_comparison isolation fix)."""
        return self._comparator_view

    @property
    def simulation(self) -> ActorSimulationView:
        """This actor's own cached simulation result. The underlying
        SimulationRuntime service is shared (audited: it holds no
        cross-call mutable state of its own), so this is real actor-
        owned state layered on top of a stateless shared service, not a
        duplicate of the service itself."""
        return self._simulation_view

    @property
    def process(self) -> tuple[str, ...]:
        """This actor's own execution/run_id history — a filtered view
        onto the shared, run_id-keyed RunStore/ProcessManager scoped to
        only the run_ids THIS actor's own kernel context recorded via
        begin_execution(), never another actor's."""
        return self._kernel_context.run_ids

    # ── Actor-owned cognitive state (already correctly actor-scoped —
    #    exposed here under the os.X names this refactor's tests use) ──

    @property
    def beliefs(self) -> Any:
        return getattr(self._actor, "belief_state", None) or getattr(self._actor, "belief", None)

    @property
    def memory(self) -> Any:
        return getattr(self._actor, "memory", None)

    @property
    def learning_state(self) -> Any:
        return self._get_transition_model()

    # ── Reasoning (delegates to ReasoningRuntime.decision_engine) ────
    # Moved to reasoning_runtime.py::DecisionEngine — a genuine Reasoning
    # responsibility. self._decision_engine is bound to self._actor
    # directly at set_actor() time, independent of the tick-pipeline engine
    # (these methods never touched the pipeline; they only ever read
    # self._actor's goal_states/beliefs/capabilities/resources), so they
    # keep working exactly as before, including with no actor bound at all.

    def evaluate_goals(self) -> list[GoalEvaluation]:
        return self._decision_engine.evaluate_goals()

    def match_capabilities(self) -> list[CapabilityMatch]:
        return self._decision_engine.match_capabilities()

    def check_resources(self, required: dict[str, float] = None) -> list[ResourceCheck]:
        return self._decision_engine.check_resources(required)

    def synthesize(self) -> DecisionSynthesis:
        return self._decision_engine.synthesize()

    # ── Reasoning / Execution (the real, tick-pipeline-owned split) ──

    def _get_policy(self) -> Any:
        """The actor's ComparisonIntegratedPolicy — the object that
        actually owns .reasoning/.execution (built once in its configure()).
        Chain: actor._get_cognitive_engine() -> BeliefFormation
               -> ._get_engine() -> CognitiveRuntime -> ._policy."""
        formation = self._get_engine()
        if formation is None:
            return None
        get_inner = getattr(formation, "_get_engine", None)
        engine = get_inner() if callable(get_inner) else None
        return getattr(engine, "_policy", None)

    @property
    def reasoning(self) -> Any:
        """Observe->Believe->Plan->Predict — see reasoning_runtime.py.
        Returns the SAME ReasoningRuntime instance the tick engine holds
        (not a second, parallel copy), with this OS's bound actor attached
        so decision_engine/world_model resolve to real actor state."""
        policy = self._get_policy()
        if policy is None or not hasattr(policy, "reasoning"):
            raise RuntimeError(
                "CognitiveOS.reasoning requires a bound actor whose cognitive "
                "engine uses ComparisonIntegratedPolicy (the default production "
                "path via build_comparison_integrated_runtime())."
            )
        policy.reasoning.bind_actor(self._actor)
        return policy.reasoning

    @property
    def execution(self) -> Any:
        """Execute->ObserveOutcome->Compare->Learn->CompileΦ->Commit — see
        execution_runtime.py. Returns the SAME ExecutionRuntime instance
        the tick engine holds."""
        policy = self._get_policy()
        if policy is None or not hasattr(policy, "execution"):
            raise RuntimeError(
                "CognitiveOS.execution requires a bound actor whose cognitive "
                "engine uses ComparisonIntegratedPolicy (the default production "
                "path via build_comparison_integrated_runtime())."
            )
        return policy.execution

    # ── Cognitive Pipeline ───────────────────────────────────

    async def tick(self, prompt_request: Any = None) -> dict:
        actor = self._actor
        if actor is None:
            return {"error": "No actor bound to this CognitiveOS"}
        # CognitiveOS is the exclusive actor-facing cognitive boundary. The
        # canonical actor tick owns persistent pipeline state and delegates the
        # lifecycle to the existing CognitiveRuntime/BeliefFormation path.
        # Keeping this as a delegation removes CognitiveOS's former second
        # BeliefState, PipelineActor, and BeliefFormation construction path.
        tick = getattr(actor, "tick", None)
        if not callable(tick):
            return {"error": "Bound actor has no cognitive tick"}
        result = await tick(prompt_request)
        self._record_execution_state(result)
        return result

    def _record_execution_state(self, result: Any) -> None:
        """Feed this actor's own kernel_context/graph_execution_state from
        a real tick result (_CognitiveTickResult or an equivalent dict) —
        the only place CognitiveOS's actor-owned execution state actually
        gets populated, so it reflects real ticks, not fabricated data."""
        execution_id = (
            getattr(result, "execution_id", None) if not isinstance(result, dict) else result.get("execution_id")
        )
        if execution_id:
            self._kernel_context.begin_execution(execution_id)
        plan = getattr(result, "plan", None) if not isinstance(result, dict) else result.get("plan")
        steps = (plan or {}).get("steps") if isinstance(plan, dict) else None
        if steps:
            last_step = steps[-1]
            node = last_step.get("action") if isinstance(last_step, dict) else str(last_step)
            self._graph_execution_state.record_step(
                node,
                outcome=(
                    getattr(result, "actual_outcome", None)
                    if not isinstance(result, dict)
                    else result.get("actual_outcome")
                ),
            )

    # ── Lazy Services ────────────────────────────────────────

    def _get_engine(self):
        if self._actor is not None:
            get_engine = getattr(self._actor, "_get_cognitive_engine", None)
            if callable(get_engine):
                return get_engine()
        return self._engine

    def _get_transition_model(self):
        if self._transition_model is None:
            from src.monkey_brain.kernel.pipeline.prediction.transitions import (
                TransitionModel,
            )

            self._transition_model = TransitionModel()
        return self._transition_model
