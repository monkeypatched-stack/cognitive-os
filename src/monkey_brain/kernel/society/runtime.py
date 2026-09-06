"""Society Runtime (Step 12.7) — coordinates many actors without performing
cognition.

Responsibilities:
    register actors, activate actors, route interactions,
    maintain shared world, coordinate execution, schedule society ticks.

Each actor still owns:
    Observe → Believe → Plan → Execute → Learn → Compile Φ → Predict → Commit

SocietyRuntime coordinates actors.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from uuid import uuid4

from src.monkey_brain.kernel.society.domain import Society, ActorProfile, ActorStatus, Team
from src.monkey_brain.kernel.society.world import SharedWorld, WorldEvent, EventType
from src.monkey_brain.kernel.society.observation import ObservationProvider, ActorObservation
from src.monkey_brain.kernel.society.belief import BeliefState, BeliefFusion
from src.monkey_brain.kernel.society.interaction import InteractionManager, Interaction, InteractionType
from src.monkey_brain.kernel.society.coordination import CoordinationEngine
from src.monkey_brain.kernel.society.game_theory import GameTheoryRuntime
from src.monkey_brain.kernel.society.communication import AffiliationCommunicationRouter
from src.monkey_brain.kernel.society.context_stream import SocietyContextStream, ContextEvent, ContextEventType
from src.monkey_brain.kernel.society.learning import (
    CollectiveLearningEngine, SharedExperience, CollectiveLearningResult,
)
from src.monkey_brain.kernel.society.governance import SocietyGovernanceEngine
from src.monkey_brain.kernel.society.actor_protocol import ActorProtocol
from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.common.correlation import new_correlation_id

logger = logging.getLogger("agentos.society.runtime")

StageFn = Callable[[Any], Awaitable[Any]]


@dataclass
class ActorRuntimeState:
    """Runtime state for one actor in the society."""
    actor_id: str = ""
    profile: ActorProfile = field(default_factory=ActorProfile)
    status: ActorStatus = ActorStatus.REGISTERED
    belief_state: BeliefState | None = None
    last_cycle: float = 0.0
    cycle_count: int = 0
    is_active: bool = True
    actor: Any = None
    actor_runtime: Any = None
    last_tick_result: Any = None
    last_lease_fence: int = 0
    """Monotonic lease fence from the most recent tick — used to reject stale belief checkpoints."""
    cell: Any = None
    """The formal ActorCell boundary object (kernel/society/actor_cell.py),
    when one was successfully constructed for this actor -- identity
    credential, this same `actor`/`self` pairing, and (for a robot node
    class) a bound ROS adapter. None is a legitimate value (e.g. this
    SocietyRuntime is standalone/unit-test usage with no identity issuer
    available) -- nothing in the existing tick/execution path requires
    this field, it exists for the Cell isolation tests and any future
    caller that wants the formal boundary rather than reaching into
    `actor`/`actor_runtime` directly."""
    """A reference to the actual Actor Runtime object (duck-typed — anything
    exposing an async `tick()`, per the corrected hierarchy's Actor Runtime
    abstraction; current implementations: CognitiveActor/ActorSystem).
    Step 12.3: this is how SocietyRuntime coordinates an actor's COMPLETE
    cognitive lifecycle, not just one cherry-picked stage."""
    cognitive_stages: dict[str, StageFn] = field(default_factory=dict)
    """Backward-compatible fallback: individual named stage callbacks, used
    only when no `actor` reference was registered. Predates Step 12.2's
    discovery that a cognitive cycle isn't cleanly separable into
    independently-callable stages once delegation to the canonical engine
    replaces per-stage stubs — kept so existing callers that only supply
    this still tick without error, not because it's still the preferred
    coordination mechanism."""


@dataclass(frozen=True)
class SocietyTickResult:
    """Result of one society tick (one round of actor coordination)."""
    tick_id: str = field(default_factory=lambda: uuid4().hex)
    tick_number: int = 0
    actors_ticked: int = 0
    interactions_routed: int = 0
    world_version: int = 0
    duration_ms: float = 0.0
    actor_execution_result: Any = None
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class TeamTickResult:
    """Result of one Team tick — every member actor of a Team ticked once,
    via the same coordinated tick_one_actor() path SocietyRuntime.tick()
    uses for every active actor (belief fusion, context publication,
    world-event commit, status transitions)."""
    tick_id: str = field(default_factory=lambda: uuid4().hex)
    team_id: str = ""
    actors_ticked: tuple[str, ...] = ()
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


class SocietyRuntime:
    """Coordinates multiple actors over a shared world.

    The runtime does NOT perform cognition — each actor owns its own
    cognitive lifecycle. SocietyRuntime only coordinates: scheduling,
    interaction routing, shared world maintenance, and observation
    distribution.
    """

    def __init__(self, society: Society | None = None,
                 strategic_runtime: GameTheoryRuntime | None = None) -> None:
        self._society = society or Society(name="Default Society")
        # Set by PlanetaryRuntime._attach_society for a society it manages
        # (None for a standalone/test SocietyRuntime) — the only source of
        # context["planetary_runtime"] the context_factory below provides,
        # which AskActorCapability needs to resolve a target actor across
        # every society, not just this one.
        self._planetary_runtime: Any = None
        self._world = SharedWorld()
        self._actors: dict[str, ActorRuntimeState] = {}
        # register_actor/unregister_actor/add_temporary_participant/
        # remove_temporary_participant mutate _actors directly; PlanetaryRuntime's
        # own _tick_lock (kernel/society/integration.py) guards planetary-tick
        # entry but does NOT cover this dict, so a request thread mutating
        # membership can race a tick reading it. threading.Lock (not
        # asyncio.Lock) because these are plain sync methods that may be
        # invoked from a threadpool as well as the event loop's own thread.
        self._actors_lock = threading.Lock()
        self._is_active = True
        self._observation_provider = ObservationProvider(self._world)
        self._belief_fusion = BeliefFusion()
        self._interaction_manager = InteractionManager()
        self._game_theory = strategic_runtime or GameTheoryRuntime()
        self._coordination_engine = CoordinationEngine(self._game_theory)
        self._communication_router = AffiliationCommunicationRouter(
            self.get_actor, self.active_actors, self.society.society_id,
        )
        self._collective_learning = CollectiveLearningEngine(society_id=self._society.society_id)
        """Step 12.8: collective learning (shared experiences, reputation,
        capability improvements, world/policy refinement) is society-level
        collective state, same as shared_goals/coordination_history (12.3)
        — owned here, not only at the Planetary layer above. PlanetaryRuntime
        delegates to this instance rather than constructing its own, per the
        duplicate-state-holder lesson from Step 12.7's context_stream/
        interaction_manager fix."""
        self._governance = SocietyGovernanceEngine()
        from src.monkey_brain.kernel.integrations import SharedResourceInventoryIntegration
        self._inventory_integration = SharedResourceInventoryIntegration(
            self.shared_resources, self.update_shared_resources, self.record_coordination,
        )
        """Step 12.10: same placement fix as collective_learning above —
        this was PlanetaryRuntime-only despite governing individual actors'
        permissions/trust/safety, which is society-level state (point 3:
        "Society Runtime should own... society policies"). Distinct from
        `Society.policies` (12.3's plain string list, coarse-grained,
        Society-domain-object-level) and from `kernel/governance.py`'s
        differently-scoped, differently-purposed, LIVE-in-production
        `SocietyGovernanceEngine` (runtime charter/jurisdiction evaluation for
        the Gateway's /plan /execute /predict /query routes) — three
        distinct concepts, only two of which happen to share this class
        name. See governance.py's module docstring for the full
        disambiguation."""
        self._tick_count = 0
        self._context_events: list[WorldEvent] = []
        self._context_stream = SocietyContextStream()
        # Every event published by this canonical stream is delivered to the
        # managed actor runtimes. Delivery is passive; actors consume the
        # inbox during their next cognitive tick rather than recursively
        # executing cognition from inside publish().
        self._context_stream.subscribe(self._deliver_context_event)
        from src.monkey_brain.kernel.affiliations.trust import TrustEngine
        self._trust_network = TrustEngine()
        """Step 12.6: the canonical Context Stream — every Commit stage
        produces an append-only event here, closing the world's feedback
        loop. Distinct from `_context_events` (a plain WorldEvent log kept
        for `to_dict()`'s summary count) and from SharedWorld's own
        `record_event()` (world-state events, not cognition-lifecycle
        events) — three different concerns that happen to share the word
        "event"."""
        self._message_queue: list[dict[str, Any]] = []
        """Inter-agent message queue. Messages are collected during a tick,
        then distributed to recipients before the next tick. Enables
        agents to share warnings, recommendations, and status updates."""
        self._messages_this_tick: list[dict[str, Any]] = []
        self._membership_registry: Any = None
        """Society as Organizational Context refactor: reattached by
        PlanetaryRuntime._attach_society() to the shared
        SocietyMembershipRegistry, the real source of truth for which
        societies an actor belongs to (independent of which SocietyRuntime
        owns its cognition). None for a standalone SocietyRuntime not
        managed by any PlanetaryRuntime — add_actor_to_team then falls back
        to its pre-refactor, home-registration-only precondition."""
        self._society_activation: Any = None
        """Same reattachment pattern — the shared SocietyActivationEngine,
        threaded down to new actors' cognitive engines in register_actor()."""
        self._context_engine: Any = None
        """Context-Aware Personalized Planning refactor: the shared
        ContextConstructionEngine, threaded the same way — see
        register_actor() below."""
        self._execution_engine: Any = None
        """World Changes refactor: the shared, ONE real capability-bus
        execution engine (see PlanetaryRuntime._attach_society) — reused,
        never rebuilt per actor. build_default_capability_bus() registers
        capabilities into a process-global registry validated for
        duplicates at boot; constructing a fresh one per actor crashes
        boot the moment more than one actor is registered through this
        path. None for a standalone SocietyRuntime, same as the two
        fields above."""
        self._knowledge_graph: Any = None
        """World Changes refactor: same sharing pattern — real capability
        .handle() calls all require context["knowledge_graph"] (a real,
        dict-style lookup) to do anything; register_actor() below builds a
        matching context_factory for actors that don't supply their own,
        mirroring api/routes/actors.py's POST /actors construction."""
        self._teams: dict[str, Team] = {}
        """Runtime Encapsulation Refactor follow-up: Team is the tier
        beneath Society (Planet->Country->City->Society->Team->Actor) — a
        subset of this society's own actors. SocietyRuntime-owned, no
        tick()/cycle() of its own; membership is strict (an actor belongs to
        at most one team here), mirroring Country->City's exclusivity."""

    @property
    def society(self) -> Society:
        return self._society

    @property
    def society_id(self) -> str:
        """Stable public identity for the Society runtime contract."""
        return self._society.society_id

    @property
    def context_stream(self) -> SocietyContextStream:
        return self._context_stream

    @property
    def world(self) -> SharedWorld:
        return self._world

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def is_active(self) -> bool:
        return self._is_active

    def activate(self) -> None:
        """Activate this society so it participates in planetary cycles."""
        self._is_active = True

    def deactivate(self) -> None:
        """Deactivate this society so it's skipped during planetary cycles."""
        self._is_active = False

    # ── Actor Registration ───────────────────────────────────────────────

    def register_actor(self, profile: ActorProfile,
                       cognitive_stages: dict[str, StageFn] | None = None,
                       actor: Any = None) -> ActorRuntimeState:
        """Register an actor with the society through ActorRuntime.

        - No actor provided: ActorRuntime creates the implementation actor
        - Actor provided + valid: ActorRuntime attaches the existing actor
        - Invalid object: rejected immediately

        Registration Entry Points (Governance/Membership/Registration Model
        refactor): this method has NO geography or PresenceTimeline
        awareness — by design, SocietyRuntime must stay usable standalone,
        with no PlanetaryRuntime, for callers that don't need physical
        presence at all (most of this file's own unit tests). It therefore
        CANNOT enforce "the Society has an associated Space" or "the Actor
        has exactly one current Space." Any caller creating a NEW Actor
        within a PlanetaryRuntime-managed world — REST routes, CLI,
        importers — must go through PlanetaryRuntime.register_actor()
        instead, the single canonical workflow that does enforce those
        invariants. Calling this method directly in that context silently
        bypasses them."""
        if actor is None:
            # New actor → create the implementation actor.  Its public
            # runtime wrapper is created below; cognition is not constructed
            # in SocietyRuntime.
            entity_id = profile.identity.actor_id
            from src.monkey_brain.kernel.compile.cognitive_actor import CognitiveActor
            engine = None
            if self._society_activation is not None or self._context_engine is not None or self._execution_engine is not None:
                # Society as Organizational Context refactor: thread the
                # shared SocietyActivationEngine into this actor's cognitive
                # engine so its ReasoningRuntime can select relevant
                # societies per goal. Context-Aware Personalized Planning
                # refactor: same pattern for ContextConstructionEngine, so
                # _generate_plan builds a real PlanningContext instead of
                # calling the planner with a bare belief/goal. Omitted
                # (falls through to CognitiveActor's own lazy bare-engine
                # default) when this SocietyRuntime isn't managed by any
                # PlanetaryRuntime — standalone/unit-test usage is unaffected.
                from src.monkey_brain.kernel.pipeline.comparison.integration import build_comparison_integrated_runtime
                # World Changes refactor: this branch previously left
                # execution_engine unset, which defaults all the way down to
                # ActionExecutor(capability_bus=None) — every action this
                # actor ever executes silently "succeeds" via a simulated
                # stub (action_executor.py's "No capability bus — simulate
                # success" fallback) regardless of whether a real product/
                # order/payment exists. self._execution_engine is built ONCE
                # (see _attach_society, mirroring _context_engine/
                # _society_activation's own share-not-rebuild pattern) —
                # build_default_capability_bus() registers capabilities into
                # a process-global registry validated for duplicates at boot
                # (kernel.py::Kernel.validate_architecture), so calling
                # build_execution_engine("grocery", ...) fresh PER ACTOR
                # here (tried first) crashed boot outright the moment more
                # than one actor was reconstructed: "duplicate capability
                # registration: DelegationCheck". A shared instance is also
                # simply correct — one real capability bus for the whole
                # PlanetaryRuntime, not a new one per actor.
                from src.monkey_brain.kernel.pipeline.prediction.persistence import (
                    load_transition_model, save_actor_meta,
                )
                prior_transition_model = load_transition_model(entity_id)
                # No eager Current Plan preload: it was a single actor-wide
                # record, which is exactly the cross-goal contamination bug
                # this fix removes (kernel/pipeline/planning/
                # current_plan_store.py). Current Plans are now goal-scoped
                # and lazily loaded per goal_key by _run_decide itself, the
                # first time each goal is actually ticked — there's no
                # single record to guess and preload here anymore.
                save_actor_meta(entity_id, profile.identity.name)
                engine = build_comparison_integrated_runtime(
                    society_activation=self._society_activation, context_engine=self._context_engine,
                    execution_engine=self._execution_engine,
                    transition_model=prior_transition_model,
                )
            # World Changes refactor: real capability .handle() calls all
            # do dict-style context.get("knowledge_graph")/context["actor_id"]
            # lookups (kernel/domains/grocery.py) — CognitiveActor's own
            # default context is a typed RuntimeContext object with no
            # .get(), so every real capability call raised
            # "'RuntimeContext' object has no attribute 'get'" the moment
            # engine (above) started actually invoking them. Same fix
            # POST /actors already applies for actors created there
            # (api/routes/actors.py's context_factory=lambda: {...}).
            # "world": self._world (real SharedWorld, set in
            # _attach_society) fixes the Observe stage — WorldPollingProvider
            # needs SOMETHING to poll. Deliberately the public/geography
            # SharedWorld, not the raw KnowledgeGraph: the KG mixes every
            # actor's private episodic traces together with no scoping of
            # its own (see _explore_knowledge's _may_explore_entity guard,
            # which exists precisely because of that) — wiring it here
            # would leak one actor's private commerce facts into another's
            # beliefs via plain world-polling.
            context_factory = (
                (lambda question: {
                    "knowledge_graph": self._knowledge_graph, "actor_id": entity_id,
                    # Actor Cell Architecture (docs/ACTOR_CELL_ARCHITECTURE.md):
                    # CognitiveActor already constructs its OWN per-actor
                    # KnowledgeGraph (self._knowledge_graph on that class,
                    # KnowledgeGraph(person_id=entity_id)) but nothing ever
                    # read it — every capability only ever saw the shared
                    # graph above. Additive key: existing capabilities that
                    # only read context["knowledge_graph"] are unaffected;
                    # `actor` is resolved at CALL time (this lambda only
                    # runs on a later tick), by which point the assignment
                    # below has already completed.
                    "actor_local_knowledge_graph": actor._knowledge_graph,
                    "world": self._world, "question": question,
                    "planetary_runtime": self._planetary_runtime,
                    # HeartbeatCapability (kernel/domains/robot.py) reads
                    # this -- None for every actor that isn't a robot
                    # deployment (the normal case); `state` (defined below)
                    # is resolved at CALL time, same as `actor` above.
                    "ros_adapter": (state.cell.ros_adapter if getattr(state, "cell", None) is not None else None),
                })
                if self._knowledge_graph is not None else None
            )
            actor = CognitiveActor(
                entity_id=entity_id,
                objective=profile.objective,
                goals=list(profile.goals),
                engine=engine,
                context_factory=context_factory,
                name=profile.identity.name,
            )
        elif not isinstance(actor, ActorProtocol):
            # Invalid → rejected immediately
            raise TypeError(
                f"Cannot register {type(actor).__name__} for '{profile.identity.actor_id}' "
                f"— does not satisfy ActorProtocol"
            )
        else:
            if profile.objective and hasattr(actor, '_objective'):
                actor._objective = profile.objective
            if profile.goals and hasattr(actor, 'set_goal'):
                actor.set_goal(profile.goals[0])

        # ActorRuntime is the sole owner of cognition and its supporting
        # services.  This import/construction is intentionally below the
        # SocietyRuntime boundary.
        #
        # Step 14 — Architecture Consolidation (14.5): local_belief must be
        # passed here. Without it, ActorRuntime.__init__ creates a brand-new
        # empty SparseTransitionTensor and its own `self._actor.world =
        # self.belief` (a property setter) silently reassigns actor.belief
        # to that new tensor — discarding whatever tensor ActorSystem.__init__
        # had already shared via its own Step 13.5 fix (local_belief=self.belief
        # there). That left two ActorRuntime instances per actor disagreeing
        # about which tensor is real. getattr(...) degrades to None (existing
        # ActorRuntime.__init__ fallback: local_belief or SparseTransitionTensor())
        # for the rare non-CognitiveActor-family `actor`.
        from src.monkey_brain.kernel.compile.actor_runtime import ActorRuntime
        actor_runtime = ActorRuntime(
            profile.identity.actor_id,
            existing_actor=actor,
            local_belief=getattr(actor, "belief", None),
            world_view=self._world,
        )
        actor_runtime.set_society_runtime(self)

        state = ActorRuntimeState(
            actor_id=profile.identity.actor_id,
            profile=profile,
            cognitive_stages=cognitive_stages or {},
            actor=actor,
            actor_runtime=actor_runtime,
        )
        with self._actors_lock:
            self._actors[profile.identity.actor_id] = state

        # Actor Cell (docs/ACTOR_CELL_ARCHITECTURE.md Section B/O): a
        # best-effort formal boundary object -- never gates registration
        # itself (an actor that already exists must still register even if
        # minting fails for some environmental reason). The REAL,
        # fail-closed identity enforcement happens where an actor actually
        # answers a request (actor_runtime.py's per-Pod /execute,
        # grocery.py's NATS inbox handler), via kernel/actor_identity.py.
        try:
            from src.monkey_brain.kernel.actor_identity import mint_actor_cell_identity
            from src.monkey_brain.kernel.society.actor_cell import ActorCell
            from src.monkey_brain.kernel.trusted_auth import get_trusted_auth

            issuer = get_trusted_auth().principal_id or "society-runtime"
            credential = mint_actor_cell_identity(profile.identity.actor_id, issuer=issuer)
            state.cell = ActorCell(
                actor_id=profile.identity.actor_id, identity=credential,
                actor=actor, runtime_state=state,
            )
        except Exception:
            logger.debug(
                "register_actor: ActorCell construction skipped for %s (non-fatal)",
                profile.identity.actor_id, exc_info=True,
            )

        if self._membership_registry is not None:
            # Membership as a First-Class Runtime Resource refactor: this
            # registration IS a real Membership too — "Membership is the
            # canonical relationship between Actor and Society" must hold
            # for every registration path (direct SocietyRuntime.
            # register_actor(), not only PlanetaryRuntime.register_actor()/
            # join_society()). Idempotent (add() no-ops if one already
            # exists), so double-registration from both call sites is safe.
            self._membership_registry.add(profile.identity.actor_id, self.society.society_id, role="member")

        logger.info("Registered actor: %s (%s) objective=%s (one OS per actor)",
                     profile.identity.name, profile.identity.actor_type.value,
                     profile.objective or "default")
        return state

    def unregister_actor(self, actor_id: str) -> bool:
        with self._actors_lock:
            if actor_id in self._actors:
                del self._actors[actor_id]
            else:
                return False
        logger.info("Unregistered actor: %s", actor_id)
        return True

    def add_temporary_participant(self, actor_state: ActorRuntimeState) -> None:
        """Coordination boundary refactor: "Societies coordinate the
        behavior and interactions of all actors currently participating in
        the society, whether through permanent membership or temporary
        membership derived from physical presence." Registers an Actor's
        EXISTING runtime state (owned by its home Society — the same
        ActorRuntimeState/ActorRuntime/cognition object, never a second
        one) as a coordination participant here too: eligible for this
        Society's tick() (cognition still runs exactly once per cycle —
        see kernel/geography/runtime.py's cross-Society dedup),
        broadcast_message(), and active_actors()-driven governance/
        observation checks.

        Does NOT create a Membership — MembershipGovernor already recorded
        the temporary Membership that triggers this call (kernel/society/
        integration.py's on_temporary_granted wiring); this is purely the
        coordination-participant side of that same event."""
        with self._actors_lock:
            self._actors[actor_state.actor_id] = actor_state

    def remove_temporary_participant(self, actor_id: str) -> None:
        """Undo add_temporary_participant when the temporary Membership
        that justified it is revoked (actor left the Space, or the
        Membership was superseded by a new permanent one — the caller is
        responsible for not calling this when actor_id now holds an
        actual PERMANENT membership here instead, see PlanetaryRuntime's
        on_temporary_revoked wiring)."""
        with self._actors_lock:
            self._actors.pop(actor_id, None)

    def activate_actor(self, actor_id: str) -> bool:
        state = self._actors.get(actor_id)
        if state is None:
            return False
        state.is_active = True
        state.status = ActorStatus.ACTIVE
        return True

    def deactivate_actor(self, actor_id: str) -> bool:
        state = self._actors.get(actor_id)
        if state is None:
            return False
        state.is_active = False
        state.status = ActorStatus.IDLE
        return True

    def get_actor(self, actor_id: str) -> ActorRuntimeState | None:
        return self._actors.get(actor_id)

    def active_actors(self) -> tuple[ActorRuntimeState, ...]:
        return tuple(a for a in self._actors.values() if a.is_active)

    def all_actors(self) -> tuple[ActorRuntimeState, ...]:
        return tuple(self._actors.values())

    def get_actor_runtime(self, actor_id: str) -> Any | None:
        """Return the public ActorRuntime boundary for an actor."""
        state = self._actors.get(actor_id)
        return state.actor_runtime if state is not None else None

    def _deliver_context_event(self, event: ContextEvent) -> None:
        """Forward every context event to every managed actor runtime."""
        for actor_state in self._actors.values():
            runtime = actor_state.actor_runtime
            receiver = getattr(runtime, "receive_context_event", None)
            if callable(receiver):
                try:
                    receiver(event)
                except Exception as exc:
                    logger.error(
                        "Context event delivery failed for actor %s: %s",
                        actor_state.actor_id,
                        exc,
                    )

    def actor_runtimes(self) -> tuple[Any, ...]:
        """Return managed ActorRuntime objects, never cognition internals."""
        return tuple(state.actor_runtime for state in self._actors.values()
                     if state.actor_runtime is not None)

    # ── Teams ────────────────────────────────────────────────────────────
    # Planet -> Country -> City -> Society -> Team -> Actor. Team is a
    # containment object only (no tick()/cycle()); mutation flows through
    # dataclasses.replace(), same pattern Society/Federation/Country/City use.

    def create_team(self, name: str, description: str = "") -> Team:
        team = Team(name=name, description=description)
        self._teams[team.team_id] = team
        return team

    def get_team(self, team_id: str) -> Team | None:
        return self._teams.get(team_id)

    def all_teams(self) -> tuple[Team, ...]:
        return tuple(self._teams.values())

    def team_for_actor(self, actor_id: str) -> Team | None:
        return next((t for t in self._teams.values() if t.has_member(actor_id)), None)

    def add_actor_to_team(self, team_id: str, actor_id: str) -> Team | None:
        """Add an actor to a team. Requires the actor be a registered
        (cognition-resident) member of this society OR — Society as
        Organizational Context refactor — a mere organizational member via
        the shared SocietyMembershipRegistry (an actor whose cognition
        lives in a different "home" SocietyRuntime can still join a team
        here). Strict membership: first removed from any other team in
        this society, so an actor belongs to at most one team PER society
        (it may belong to one team in each of several societies)."""
        is_home_registered = self.get_actor(actor_id) is not None
        is_org_member = (
            self._membership_registry is not None
            and self._membership_registry.is_member(actor_id, self.society.society_id)
        )
        if not is_home_registered and not is_org_member:
            return None
        team = self._teams.get(team_id)
        if team is None:
            return None
        prior = self.team_for_actor(actor_id)
        if prior is not None and prior.team_id != team_id:
            self.remove_actor_from_team(prior.team_id, actor_id)
            team = self._teams[team_id]
        if team.has_member(actor_id):
            return team
        updated = dataclasses.replace(team, member_actor_ids=team.member_actor_ids + (actor_id,))
        self._teams[team_id] = updated
        return updated

    def remove_actor_from_team(self, team_id: str, actor_id: str) -> Team | None:
        team = self._teams.get(team_id)
        if team is None:
            return None
        updated = dataclasses.replace(
            team, member_actor_ids=tuple(a for a in team.member_actor_ids if a != actor_id),
        )
        self._teams[team_id] = updated
        return updated

    async def tick_team(self, team_id: str) -> TeamTickResult:
        """Tick every member actor of a team, via the same tick_one_actor()
        coordination path SocietyRuntime.tick() uses for the whole society —
        the tier-consistent completion of Planet->Country->City tick
        cascading down to Team, one level above Actor.

        Concurrency (swarm-readiness audit, blocker 3): members tick via
        asyncio.gather, not a sequential for-loop -- a 3-actor Team (the
        audit's own drone-swarm scenario) previously always ran fully
        sequentially even though tick_one_actor already serializes safely
        per actor via its own Redis actor lease. Same safety reasoning as
        SocietyRuntime.tick()'s own concurrent sweep above."""
        start = time.time()
        team = self._teams.get(team_id)
        if team is None:
            return TeamTickResult(team_id=team_id)

        results = await asyncio.gather(
            *(self.tick_one_actor(actor_id) for actor_id in team.member_actor_ids),
            return_exceptions=True,
        )

        ticked: list[str] = []
        for actor_id, result in zip(team.member_actor_ids, results):
            if isinstance(result, BaseException):
                logger.error("Actor %s tick failed in team %s: %s", actor_id, team_id, result)
                continue
            if result:
                ticked.append(actor_id)

        return TeamTickResult(
            team_id=team_id,
            actors_ticked=tuple(ticked),
            duration_ms=(time.time() - start) * 1000,
        )

    # ── World Operations ─────────────────────────────────────────────────

    def publish_world_event(self, event: WorldEvent) -> None:
        self._world.record_event(event)
        self._context_events.append(event)

    def get_observation(self, actor_id: str) -> ActorObservation:
        return self._observation_provider.observe(actor_id)

    # ── Interaction Routing ──────────────────────────────────────────────

    def route_interaction(self, interaction_type: InteractionType,
                          initiator_id: str, participant_ids: tuple[str, ...],
                          topic: str = "", proposal: Any = None) -> Interaction:
        """Step 12.7 bugfix: this previously referenced an undefined name
        `Event` (there is no such class in this module — WorldEvent's type
        enum is EventType, imported above) and raised NameError on every
        call; confirmed via grep it had zero callers anywhere, live or
        test, so the break was 100% latent. Also previously only appended
        to the plain `_context_events` log and never published to the
        canonical Context Stream or recorded coordination, despite this
        method's own tick() docstring claiming otherwise. Fixed on both
        counts."""
        interaction = self._interaction_manager.create_interaction(
            interaction_type=interaction_type,
            initiator_id=initiator_id,
            participant_ids=participant_ids,
            topic=topic,
            proposal=proposal,
        )
        event = WorldEvent(
            event_type=EventType.INTERACTION,
            entity_id=interaction.interaction_id,
            description=f"Interaction: {interaction_type.value} from {initiator_id}",
            source_actor_id=initiator_id,
        )
        self._context_events.append(event)
        # topic/proposal are the real message content the caller passed
        # (POST /planet/interactions body) — previously computed and
        # stored on the Interaction object but never included in the
        # published event, so nothing could ever show what was actually
        # said, only that an interaction of some type occurred.
        self._context_stream.publish(ContextEvent(
            event_type=ContextEventType.INTERACTION, actor_id=initiator_id,
            description=(
                f"{initiator_id}: {topic}" if topic
                else f"Interaction: {interaction_type.value} from {initiator_id}"
            ),
            payload={
                "interaction_id": interaction.interaction_id, "participants": list(participant_ids),
                "topic": topic, "proposal": proposal,
            },
            provenance="society:interaction",
        ))
        self.record_coordination(f"interaction routed: {interaction_type.value} from {initiator_id}")
        return interaction

    def respond_to_interaction(self, interaction_id: str, actor_id: str,
                               accept: bool, message: str = "") -> Interaction | None:
        """Step 12.7: the interaction lifecycle previously stopped at
        creation — InteractionManager.respond()/cast_vote()/complete()
        already existed but SocietyRuntime never exposed them, so an
        interaction could be routed but never actually responded to,
        voted on, or completed through the coordinator."""
        interaction = self._interaction_manager.respond(interaction_id, actor_id, accept, message)
        if interaction is not None:
            self._context_stream.publish(ContextEvent(
                event_type=ContextEventType.INTERACTION, actor_id=actor_id,
                description=f"Interaction {interaction_id} {'accepted' if accept else 'rejected'} by {actor_id}",
                payload={"interaction_id": interaction_id, "accept": accept},
                provenance="society:interaction",
            ))
            self.record_coordination(f"interaction {interaction_id} responded to by {actor_id}")
        return interaction

    def cast_vote(self, interaction_id: str, actor_id: str, vote: bool) -> Interaction | None:
        interaction = self._interaction_manager.cast_vote(interaction_id, actor_id, vote)
        if interaction is not None:
            self._context_stream.publish(ContextEvent(
                event_type=ContextEventType.INTERACTION, actor_id=actor_id,
                description=f"Vote cast on {interaction_id} by {actor_id}",
                payload={"interaction_id": interaction_id, "vote": vote},
                provenance="society:interaction",
            ))
            self.record_coordination(f"vote cast on {interaction_id} by {actor_id}")
        return interaction

    def complete_interaction(self, interaction_id: str) -> Interaction | None:
        interaction = self._interaction_manager.complete(interaction_id)
        if interaction is not None:
            self._context_stream.publish(ContextEvent(
                event_type=ContextEventType.INTERACTION,
                description=f"Interaction {interaction_id} completed",
                payload={"interaction_id": interaction_id},
                provenance="society:interaction",
            ))
            self.record_coordination(f"interaction {interaction_id} completed")
        return interaction

    def all_interactions(self) -> tuple[Interaction, ...]:
        return self._interaction_manager.all_interactions()

    # ── Coordination ─────────────────────────────────────────────────────

    def coordinate(self) -> CoordinationEngine:
        return self._coordination_engine

    @property
    def game_theory(self) -> GameTheoryRuntime:
        """Strategic stage shared with this society's coordination engine."""
        return self._game_theory

    # ── Collective Learning ──────────────────────────────────────────────

    @property
    def collective_learning(self) -> CollectiveLearningEngine:
        return self._collective_learning

    def share_experience(self, experience: SharedExperience) -> CollectiveLearningResult:
        result = self._collective_learning.share_experience(experience)
        self._context_stream.publish(ContextEvent(
            event_type=ContextEventType.LEARNING, actor_id=experience.actor_id,
            description=f"Experience shared: {experience.description or experience.learning_type.value}",
            payload={"experience_id": experience.experience_id, "outcome": experience.outcome},
            provenance="society:learning",
        ))
        self.record_coordination(f"experience shared by {experience.actor_id}: {experience.outcome}")
        return result

    # ── Governance ────────────────────────────────────────────────────────

    @property
    def governance(self) -> SocietyGovernanceEngine:
        return self._governance

    def check_permission(self, actor_id: str, resource: str, action: str) -> bool:
        return self._governance.check_permission(actor_id, resource, action)

    # ── Collective State (Step 12.3: Society Runtime owns society state,
    #    not just scheduling — society policies, shared goals, membership,
    #    coordination history) ───────────────────────────────────────────

    def add_shared_goal(self, goal: str) -> None:
        """Add a goal the society as a whole is pursuing, distinct from any
        single actor's own goal. Society is frozen (immutable domain
        object) — replaced, not mutated in place."""
        self._society = dataclasses.replace(
            self._society, shared_goals=self._society.shared_goals + (goal,),
        )

    def shared_resources(self) -> dict[str, Any]:
        """Return the household's shared resource snapshot.

        Resource ownership stays with the Society, while the planner only
        receives a read-only snapshot.  The conventional keys are
        ``budget``, ``pantry``, and ``shopping_list``; metadata remains
        extensible for other household resources.
        """
        resources = self._society.metadata.get("shared_resources", {})
        return dict(resources) if isinstance(resources, dict) else {}

    def update_shared_resources(self, **resources: Any) -> None:
        """Atomically update multiple shared household resources."""
        merged = {**self.shared_resources(), **resources}
        metadata = {**self._society.metadata, "shared_resources": merged}
        self._society = dataclasses.replace(self._society, metadata=metadata)

    def inventory_replenishment(self, item: str) -> dict[str, Any]:
        """Inspect store inventory against its configured minimum level."""
        return self._inventory_integration.observe(item)

    def replenish_inventory(self, item: str, quantity: float,
                            supplier: str = "") -> dict[str, Any]:
        """Apply a completed supplier replenishment to shared store stock."""
        return self._inventory_integration.apply_replenishment(item, quantity, supplier)

    def add_policy(self, policy: str) -> None:
        self._society = dataclasses.replace(
            self._society, policies=self._society.policies + (policy,),
        )

    def record_coordination(self, description: str) -> None:
        """Append a coordination event to the society's history — called by
        tick() (per-actor coordination) and route_interaction() alike, so
        the history reflects everything SocietyRuntime actually did."""
        self._society = dataclasses.replace(
            self._society,
            coordination_history=self._society.coordination_history + (description,),
        )

    # ── Society Tick ─────────────────────────────────────────────────────

    async def tick_one_actor(self, actor_id: str, prompt_request: Any = None) -> bool:
        """Coordinate exactly one actor's complete cognitive cycle: observe,
        fuse belief, then _coordinate_actor() (tick/publish/commit).

        Extracted from tick()'s per-actor loop so a single-actor coordination
        (e.g. POST /actors/{id}/tick) goes through the identical belief-fusion
        and event-publication path as a full society tick, instead of a
        second, hand-rolled belief-construction implementation living in the
        API route. Returns True if the actor was found, active, and
        coordinated (even if its own tick() raised — the failure is still
        logged and reflected in the actor's state); False if the actor
        doesn't exist or isn't active.
        """
        actor_state = self._actors.get(actor_id)
        if actor_state is None or not actor_state.is_active:
            return None

        # Actor Registry / ownership lease (Deployment Architecture,
        # Section 7): two processes can both have `actor_id` loaded into
        # their own _actors dict (e.g. both reconciled it from the shared
        # Redis actor registry) with nothing previously stopping both from
        # ticking it concurrently -- a split-brain risk on one identity's
        # belief, not just a discovery gap. Acquire a short-lived,
        # per-actor Redis lease before running any real cognition; if
        # another node already holds it, skip this tick entirely rather
        # than race it. Deliberately a no-op (lease_token stays None, no
        # Redis round-trip at all) for a standalone SocietyRuntime with no
        # PlanetaryRuntime attached -- there is no cross-process risk to
        # guard against there, and this must not add overhead or a new
        # failure mode to the extensive existing standalone/unit-test path.
        planetary = self._planetary_runtime
        lease_token: str | None = None
        if planetary is not None:
            lease_token = planetary.acquire_actor_lease(actor_id)
            if lease_token is None:
                logger.info(
                    "Actor %s is currently leased by another node — skipping this tick",
                    actor_id,
                )
                return None

        try:
            # get revious observations
            observation = self.get_observation(actor_id)
            try:
                # fuse with current
                actor_state.belief_state = self._belief_fusion.fuse(
                    actor_id, observation, actor_state.belief_state,
                )
                logger.info("Belief fusion for %s: %d beliefs from %d entities",
                           actor_id,
                           len(actor_state.belief_state.beliefs) if actor_state.belief_state else 0,
                           len(observation.entities))
            except Exception as e:
                logger.error("Belief fusion failed for %s: %s", actor_id, e)
                return None
            actor_state.last_cycle = time.time()
            actor_state.cycle_count += 1

            try:
                await asyncio.wait_for(
                    self._coordinate_actor(actor_state, observation, prompt_request),
                    # A dialogue turn may include AskActor's real HTTP request
                    # and the colleague's own LLM answer. Keep this above the
                    # capability's 90-second network budget so nested dialogue
                    # is not discarded as an apparent actor failure. Also kept
                    # above belief_formation.from_state's own 240s timeout
                    # (cognitive_actor.py) — this must stay an outer safety net,
                    # never the one that fires first: a TimeoutError caught HERE
                    # skips the actor with no result at all, while the inner
                    # one degrades gracefully to an honest FormationResult
                    # failure the caller can still report.
                    timeout=300.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Actor %s tick timed out after 300s — skipping", actor_id)
                return None
            except Exception as e:
                logger.error("Actor %s tick failed: %s", actor_id, e)
                return None
            return True
        finally:
            # Release as soon as this tick actually finishes (success,
            # failure, or timeout alike) rather than holding the lease for
            # its full TTL -- the next tick, on any node, should not have
            # to wait out the lease window unnecessarily.
            if planetary is not None and lease_token is not None:
                fence = planetary.get_actor_lease_fence(actor_id)
                if fence is not None:
                    actor_state.last_lease_fence = fence
            if planetary is not None and lease_token is not None:
                planetary.release_actor_lease(actor_id, lease_token)

    # ── Inter-Agent Messaging ─────────────────────────────────────────────

    def send_message(self, from_actor: str, to_actor: str, msg_type: str,
                     payload: dict[str, Any] | None = None,
                     *, correlation_id: str = "") -> bool:
        """Queue only affiliation/society-permitted actor communication."""
        decision = self._communication_router.resolve(
            from_actor, to_actor, correlation_id=correlation_id,
        )
        if not decision.allowed:
            logger.warning("Communication denied %s -> %s: %s", from_actor, to_actor, decision.reason)
            return False
        message = {
            "from": from_actor, "to": to_actor,
            "type": msg_type, "payload": payload or {},
            "routing": {
                "affiliation_id": decision.affiliation_id,
                "society_id": decision.society_id,
                "reason": decision.reason,
            },
            "correlation_id": decision.correlation_id,
            "causation_id": decision.decision_id,
        }
        # Actor Messaging (Deployment Architecture, Section 8): route
        # through the recipient's durable, cross-process inbox whenever a
        # PlanetaryRuntime/Redis is available, so the message reaches
        # to_actor regardless of which process is currently ticking it —
        # previously this queue could only ever be drained by the SAME
        # process that appended to it. Falls back to the process-local
        # list (this method's entire prior behavior) when there's no
        # PlanetaryRuntime attached (standalone/unit-test SocietyRuntime)
        # or Redis is genuinely unreachable.
        planetary = self._planetary_runtime
        if planetary is None or not planetary.push_actor_message(to_actor, message):
            self._message_queue.append(message)
        _obs.counter("communication.messages_routed")
        return True

    def broadcast_message(self, from_actor: str, msg_type: str,
                          payload: dict[str, Any] | None = None,
                          affiliation_id: str = "",
                          *, correlation_id: str = "") -> int:
        """Queue a message only for eligible affiliation participants."""
        # One broadcast() call is one logical operation — mint a single
        # correlation_id upfront (if the caller didn't supply one) so every
        # resulting recipient message shares it, rather than each
        # per-recipient resolve() self-minting its own.
        correlation_id = correlation_id or new_correlation_id()
        recipients = self._communication_router.eligible_recipients(
            from_actor, affiliation_id=affiliation_id, correlation_id=correlation_id,
        )
        sent = sum(
            self.send_message(from_actor, recipient, msg_type, payload, correlation_id=correlation_id)
            for recipient in recipients
        )
        if sent:
            _obs.counter("communication.broadcast_deliveries", sent)
        return sent

    def eligible_recipients(self, actor_id: str, affiliation_id: str = "") -> tuple[str, ...]:
        return self._communication_router.eligible_recipients(
            actor_id, affiliation_id=affiliation_id,
        )

    def communication_audit(self) -> tuple[Any, ...]:
        return tuple(self._communication_router.audit)

    def get_messages_for(self, actor_id: str) -> list[dict[str, Any]]:
        """Get pending messages for a specific actor — a non-destructive
        peek (never drains), merging the durable cross-process inbox with
        this process's own in-memory fallback queue."""
        planetary = self._planetary_runtime
        durable = planetary.peek_actor_inbox(actor_id) if planetary is not None else []
        local = [m for m in self._message_queue if m["to"] == actor_id]
        return durable + local

    def _affiliations_for(self, actor_id: str) -> Any | None:
        """The actor's own AffiliationManager. Affiliation.trust_level (not
        this runtime's legacy _trust_network dict) is the single source of
        truth for in-society, actor-to-actor trust -- see
        kernel/affiliations/manager.py. _trust_network is kept only as a
        fallback for actor states that don't expose an AffiliationManager
        (e.g. lightweight test doubles)."""
        state = self._actors.get(actor_id)
        return getattr(state.actor_runtime, "affiliations", None) if state else None

    def get_trust(self, from_actor: str, to_actor: str) -> float:
        """Get trust level that from_actor has in to_actor."""
        affiliations = self._affiliations_for(from_actor)
        if affiliations is not None:
            return affiliations.get_trust(to_actor)
        return self._trust_network.get_trust(from_actor, to_actor)

    def update_trust(self, from_actor: str, to_actor: str, success: bool) -> float:
        """Update trust based on recommendation outcome. Returns new trust level."""
        affiliations = self._affiliations_for(from_actor)
        if affiliations is not None:
            affiliations.update_trust_from_outcome(to_actor, goal_achieved=success)
            new_trust = affiliations.get_trust(to_actor)
        else:
            self._trust_network.update_from_outcome(
                from_actor, to_actor, goal_achieved=success,
            )
            new_trust = self._trust_network.get_trust(from_actor, to_actor)
        logger.debug("Trust updated: %s → %s: %.3f (%s)",
                     from_actor, to_actor, new_trust,
                     "success" if success else "failure")
        return new_trust

    def _deliver_messages(self) -> int:
        """Deliver all queued messages, trust-weighted. Returns count.

        Drains two sources: each resident actor's durable, cross-process
        inbox (PlanetaryRuntime.drain_actor_inbox — messages ANY process
        sent, addressed to an actor THIS process currently has resident
        and is about to tick) and this process's own in-memory fallback
        queue (only ever populated by send_message when no PlanetaryRuntime/
        Redis was available at send time). Without the durable inbox, a
        message sent by a different process's SocietyRuntime instance for
        the same actor was structurally invisible here, even though the
        recipient was genuinely about to be ticked by this process."""
        from src.monkey_brain.kernel.society.belief import BeliefEntry, BeliefHypothesis

        delivered = 0
        delivered_this_tick: list[dict[str, Any]] = []

        def _apply(msg: dict[str, Any]) -> None:
            nonlocal delivered
            target = self._actors.get(msg["to"])
            if not (target and target.belief_state):
                return
            sender = msg.get("from", "")
            trust = self.get_trust(sender, msg["to"])
            # Only deliver if trust >= 0.3 (filter out very low-trust noise)
            if trust < 0.3:
                return
            claim = f"{sender}: {msg['type']} — {msg['payload'].get('message', str(msg['payload']))}"
            # BeliefState (society/belief.py) is a frozen dataclass — no
            # add_hypothesis() method (that only exists on the differently
            # -scoped kernel/pipeline/belief_state.py::BeliefState). Append
            # via dataclasses.replace(), matching how every other belief
            # write in this codebase mutates an immutable BeliefState.
            new_entry = BeliefEntry(
                subject=claim,
                hypotheses=(BeliefHypothesis(
                    subject=claim, predicate="claims", confidence=trust,
                    correlation_id=msg.get("correlation_id", ""),
                    causation_id=msg.get("causation_id", ""),
                ),),
            )
            target.belief_state = dataclasses.replace(
                target.belief_state,
                beliefs=target.belief_state.beliefs + (new_entry,),
            )
            delivered += 1

        planetary = self._planetary_runtime
        if planetary is not None:
            for actor_id in list(self._actors):
                for msg in planetary.drain_actor_inbox(actor_id):
                    _apply(msg)
                    delivered_this_tick.append(msg)

        for msg in self._message_queue:
            _apply(msg)
        delivered_this_tick.extend(self._message_queue)
        self._messages_this_tick = delivered_this_tick
        self._message_queue.clear()
        return delivered

    async def tick(self, *, target_actor_id: str | None = None,
                   prompt_request: Any = None,
                   broadcast_context: Any = None,
                   exclude_actor_ids: frozenset[str] | None = None,
                   single_actor_only: bool = False) -> SocietyTickResult:
        """Execute one society tick: observe, update beliefs, deliver messages,
        and coordinate each active actor's complete execution.

        exclude_actor_ids (kernel/geography/runtime.py::GeographicEntityRuntime)
        skips actors already ticked elsewhere THIS SAME planetary cycle —
        closes the gap where an Actor physically present at a Space visited
        earlier in a geographic traversal than its home Society's own
        hosting node would otherwise get ticked a second time here, since
        this loop (unlike the geography traversal) has no visibility into
        physical presence on its own and would otherwise tick every active
        actor unconditionally.

        broadcast_context (True Multi-Actor Coordination): for an
        UNTARGETED tick (target_actor_id=None, e.g. PlanetaryRuntime.
        _propagate_coordination reacting to a real domain event), every
        non-excluded active actor gets this as their own prompt_request
        instead of None. Found live: an untargeted reactive tick
        previously surfaced ZERO facts about why the actor woke up (no
        target means no prompt_request reaches anyone), so a self-
        directed actor's planning fell back entirely on its own static
        goal text with no way to disambiguate between two similarly-
        scoped capabilities (e.g. InventoryReserve vs InventoryRelease)
        depending on which real event actually fired. _cognitive_tick()
        (cognitive_actor.py) already prefers prompt_request["question"]
        over the actor's own goal when present — this is what actually
        makes that override reach every reacting actor, not just a
        specific target. None (the default) preserves exact prior
        behavior for every existing caller that doesn't pass one (e.g.
        the full planetary cycle's own untargeted tick() calls).

        single_actor_only: opt-in (default False, every existing caller
        unaffected) escape hatch from True Multi-Actor Coordination's
        real cost — every OTHER active actor in target_actor_id's society
        genuinely ticks too on a plain targeted request, each with its
        own real LLM call. Fine in production; made isolating one
        actor's own planning output (e.g. under the LLM_DEV_BRIDGE dev
        harness, where each of those calls is a manual round-trip) far
        more expensive than necessary. When true, every non-target actor
        is skipped exactly like exclude_actor_ids already skips one."""

        start = time.time()
        self._tick_count += 1
        actor_execution_result = None

        # Deliver queued messages from previous tick
        self._deliver_messages()

        # Concurrency (swarm-readiness audit, blocker 3 -- "in-process tick
        # concurrency doesn't exist"): tick_one_actor already serializes
        # correctly PER ACTOR via its own Redis actor lease (acquire_
        # actor_lease/release_actor_lease, above) -- nothing ever required
        # DIFFERENT actors' ticks to run one at a time in wall-clock terms,
        # only that this loop happened to await them sequentially. Building
        # the target list first, then gathering, lets actor A's real
        # in-flight I/O (an LLM call inside managed.tick(), deep inside
        # _coordinate_actor) overlap with actor B's instead of blocking it.
        # Safe: every per-actor mutation this ends up doing
        # (_commit_world_events/_publish_tick_events/record_coordination,
        # all inside _coordinate_actor) is plain synchronous code with no
        # `await` of its own, so asyncio can never interleave two of them
        # mid-call -- only BETWEEN actors, exactly like the old sequential
        # loop already allowed between iterations.
        targets: list[tuple[str, Any]] = []
        for actor_state in self.active_actors():
            if exclude_actor_ids and actor_state.actor_id in exclude_actor_ids:
                continue
            if single_actor_only and actor_state.actor_id != target_actor_id:
                continue
            actor_prompt_request = prompt_request if actor_state.actor_id == target_actor_id else broadcast_context
            targets.append((actor_state.actor_id, actor_prompt_request))

        results = await asyncio.gather(
            *(self.tick_one_actor(actor_id, actor_prompt_request) for actor_id, actor_prompt_request in targets),
            return_exceptions=True,
        )

        actors_ticked = 0
        for (actor_id, _), ticked in zip(targets, results):
            if isinstance(ticked, BaseException):
                # tick_one_actor already catches its own real failures and
                # returns None -- reaching this means something even more
                # unexpected escaped it. Same posture as its own internal
                # except blocks: log and continue, never let one actor's
                # failure take down the rest of this tick.
                logger.error("Actor %s tick raised during society tick: %s", actor_id, ticked)
                continue
            if ticked:
                actors_ticked += 1
                if actor_id == target_actor_id:
                    target_state = self._actors.get(actor_id)
                    actor_execution_result = target_state.last_tick_result if target_state is not None else None

        # get the interation results for each actor
        interactions = self._interaction_manager.active_interactions()
        interactions_routed = len(interactions)

        return SocietyTickResult(
            tick_number=self._tick_count,
            actors_ticked=actors_ticked,
            interactions_routed=interactions_routed,
            world_version=self._world.version,
            duration_ms=(time.time() - start) * 1000,
            actor_execution_result=actor_execution_result,
        )

    async def _coordinate_actor(self, actor_state: ActorRuntimeState,
                                observation: ActorObservation,
                                prompt_request: Any = None) -> None:
        """Coordinate one actor's complete cognitive cycle: tick() and publish/commit.  
        Called by tick_one_actor() after belief fusion."""
        if actor_state.actor_runtime is not None:
            try:
                # The actor runtime is the only public execution boundary.
                # Its CognitiveOS (if any) is private implementation detail.
                managed = actor_state.actor_runtime

                # tick the actor runtime with the prompt request if provided
                result = await managed.tick(prompt_request)
                actor_state.last_tick_result = result

                # manage the actor state status transition from REGISTERED to INITIALIZED on first tick
                if actor_state.status == ActorStatus.REGISTERED:
                    actor_state.status = ActorStatus.INITIALIZED
                
                # publish the tick events to the context stream and commit the experience and world events
                self.record_coordination(f"actor {actor_state.actor_id} ticked")

                # publish the tick events to the context stream and commit the experience and world events
                self._publish_tick_events(actor_state.actor_id, result)

                # commit the experience and world events to the collective learning and world
                self._commit_experience(actor_state, result)

                # commit the world events to the world
                self._commit_world_events(actor_state, result)
                
            except Exception as e:
                logger.error("Actor %s tick failed: %s", actor_state.actor_id, e)
                actor_state.last_tick_result = None
            return

        observe_fn = actor_state.cognitive_stages.get("observe")
        if observe_fn is not None:
            try:
                await observe_fn(observation)
            except Exception as e:
                logger.error("Actor %s observe failed: %s", actor_state.actor_id, e)

    def _commit_experience(self, actor_state: ActorRuntimeState, result: Any) -> None:
        """Commit an experience from a successful tick to collective learning."""
        from src.monkey_brain.kernel.society.learning import SharedExperience, LearningType

        outcome = getattr(result, "outcome", None) or {}
        goal_achieved = outcome.get("goal_achieved", False)
        actions = getattr(result, "actions", None) or []
        learned = getattr(result, "learned", False)

        exp = SharedExperience(
            actor_id=actor_state.actor_id,
            learning_type=LearningType.SHARED_EXPERIENCE,
            description=f"tick: {len(actions)} action(s), goal={'achieved' if goal_achieved else 'pending'}",
            outcome="success" if goal_achieved else "partial",
            confidence=0.8 if goal_achieved else 0.5,
            lessons=("goal_achieved",) if goal_achieved else (),
            world_impact={"actions_executed": len(actions), "learned": learned},
        )
        self._collective_learning.share_experience(exp)

    def _commit_world_events(self, actor_state: ActorRuntimeState, result: Any) -> None:
        """Generate world events from a tick result."""
        from src.monkey_brain.kernel.society.world import WorldEvent as SocietyWorldEvent, EventType as SocietyEventType

        outcome = getattr(result, "outcome", None) or {}
        actions = getattr(result, "actions", None) or []

        if actions or outcome.get("goal_achieved"):
            event = SocietyWorldEvent(
                event_type=SocietyEventType.ACTION,
                entity_id=actor_state.actor_id,
                description=f"Actor {actor_state.profile.identity.name} executed {len(actions)} action(s)",
                source_actor_id=actor_state.actor_id,
                attributes={
                    "actions_count": len(actions),
                    "goal_achieved": outcome.get("goal_achieved", False),
                },
            )
            self._world.record_event(event)

    def _publish_tick_events(self, actor_id: str, result: Any) -> None:
        """Translates one _CognitiveTickResult into Context Stream events.
        Duck-typed against result's attributes rather than importing
        _CognitiveTickResult, so any Actor Runtime implementation's tick()
        return value works here as long as it exposes the same shape."""
        self._context_stream.publish(ContextEvent(
            event_type=ContextEventType.OBSERVATION, actor_id=actor_id,
            description="Actor observed the world",
            payload=getattr(result, "observations", None),
            provenance="society:tick",
        ))
        if getattr(result, "belief_updated", False):
            self._context_stream.publish(ContextEvent(
                event_type=ContextEventType.BELIEF_UPDATE, actor_id=actor_id,
                description="Actor beliefs updated",
                provenance="society:tick",
            ))
        actions = getattr(result, "actions", None) or []
        if actions:
            self._context_stream.publish(ContextEvent(
                event_type=ContextEventType.ACTION, actor_id=actor_id,
                description=f"{len(actions)} action(s) executed",
                payload=actions,
                provenance="society:tick",
            ))
            for action in actions:
                self._publish_message_interaction(actor_id, action)
        if getattr(result, "learned", False):
            self._context_stream.publish(ContextEvent(
                event_type=ContextEventType.LEARNING, actor_id=actor_id,
                description="Actor learned from this cycle",
                provenance="society:tick",
            ))
        predicted = getattr(result, "predicted_outcome", None)
        if predicted:
            self._context_stream.publish(ContextEvent(
                event_type=ContextEventType.PREDICTION, actor_id=actor_id,
                description="Actor predicted a future outcome",
                payload=predicted,
                provenance="society:tick",
            ))

    def _publish_message_interaction(self, actor_id: str, action: Any) -> None:
        """BroadcastToAffiliation/RespondToInquiry/RecordAgreement, used
        as ordinary plan steps (not the standalone POST /actors/{id}/ask
        route, which already publishes its own real INTERACTION event —
        see grocery.py::AskActorCapability/AnswerQuestionCapability),
        return their real message content (message/answer) but nothing
        publishes it as a conversation-typed event — it was only ever
        visible buried inside the generic ACTION event's payload list
        above. This surfaces the SAME real content those capabilities
        already computed, as its own INTERACTION event, so the
        Conversation Timeline can show what was actually said instead of
        just "N action(s) executed." ActionOutcome doesn't carry the
        capability name, so this keys off the real result shape each
        capability actually returns (grocery.py's own conventions) —
        not fabricated, and low collision risk (grepped: only these
        capabilities return "message"/"answer" keys)."""
        action_dict = action if isinstance(action, dict) else getattr(action, "__dict__", None)
        if not isinstance(action_dict, dict) or not action_dict.get("success"):
            return
        result = action_dict.get("result")
        if not isinstance(result, dict):
            return
        # AskActorCapability/DelegateTaskCapability results also carry an
        # "answer" key, but that answer's real author is target_actor,
        # not this tick's own actor_id -- confirmed live: a plan-driven
        # AskActor step ("Ask Raj if he can pick up milk") published a
        # SECOND INTERACTION event here attributing Raj's real answer to
        # Priya (the asker), on top of AskActorCapability's own already-
        # correct event (see this function's own docstring). Only
        # RespondToInquiryCapability's/BroadcastToAffiliationCapability's
        # results (no target_actor key) mean "I, the ticking actor,
        # genuinely said this."
        if result.get("target_actor"):
            return
        message = result.get("message")
        answer = result.get("answer")
        if message:
            self._context_stream.publish(ContextEvent(
                event_type=ContextEventType.INTERACTION, actor_id=actor_id,
                description=f"{actor_id}: {message}",
                payload={
                    "from_actor_id": actor_id, "message": message,
                    "participants": list(result.get("recipients") or ()),
                },
                provenance="society:tick",
            ))
        elif answer:
            self._context_stream.publish(ContextEvent(
                event_type=ContextEventType.INTERACTION, actor_id=actor_id,
                description=f"{actor_id}: {answer}",
                payload={"from_actor_id": actor_id, "answer": answer},
                provenance="society:tick",
            ))

    async def run_ticks(self, count: int) -> list[SocietyTickResult]:
        results = []
        for _ in range(count):
            result = await self.tick()
            results.append(result)
        return results

    # ── Serialization ────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "society_id": self._society.society_id,
            "society_name": self._society.name,
            "world_version": self._world.version,
            "actor_count": len(self._actors),
            "active_actor_count": len(self.active_actors()),
            "tick_count": self._tick_count,
            "interaction_count": len(self._interaction_manager.all_interactions()),
            "context_event_count": len(self._context_events),
            "context_stream_event_count": self._context_stream.event_count,
            "shared_goal_count": len(self._society.shared_goals),
            "policy_count": len(self._society.policies),
            "coordination_history_count": len(self._society.coordination_history),
            "reputation_count": len(self._collective_learning.all_reputations()),
            "governance_policy_count": len(self._governance.policies()),
        }
