"""CognitiveActor — the single canonical Actor class for the Society Runtime.

Consolidates the 5+ previous Actor implementations into one:
  - entity.py:Actor        → type hierarchy (Person, Robot, etc.)
  - society.py:Actor       → cognitive cycle (plan/execute/learn)
  - actor_belief.py        → belief model (observe, bellman, memory, trust, Φ)
  - autonomous_actor.py    → async lifecycle (cognitive loop)
  - actor/actor.py         → action recording via ActorRuntime

Every actor in the Society Runtime derives from this class.

ARCHITECTURAL SEPARATION (ACP-2):

Actor supplies to Runtime:
    identity    — who am I (Entity fields)
    capabilities — what I can do
    goals       — what I want to achieve
    local memory — what I remember

Runtime owns (NOT the actor):
    belief      — what do I know (BeliefState)
    policy      — what should I do (PolicyStore)
    Φ           — compiled sparse transition operator
    trust       — who do I believe
    execution   — what am I doing now
    planning    — deciding what to do
    learning    — improving from outcomes

Key invariant: CognitiveActor never re-implements the cognitive lifecycle.
It delegates to CognitiveRuntime for all cognition.

Architectural invariants:
    - Global World is READ-ONLY to actors
    - Only Context Stream may modify the world
    - Actors learn into LOCAL belief, never into global state
    - Φ is recompiled after significant belief changes
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from src.monkey_brain.kernel.compile.entity import Entity, EntityType
from src.monkey_brain.kernel.compile.actor_belief import ActorBelief, ActorBeliefSnapshot
from src.monkey_brain.kernel.compile.sparse import SparseMatrix, epistemic_loss
from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor
from src.monkey_brain.kernel.compile.actor import ActorModel

logger = logging.getLogger("agentos.cognitive_actor")


@dataclass(frozen=True)
class CycleResult:
    """Result of one complete cognitive cycle (plan → simulate → execute → learn)."""
    plan: list[str]
    predicted: dict[str, float]
    observed: list[tuple[str, str | None]]
    epistemic_loss: float
    reward: float
    reached_goal: bool


@dataclass(frozen=True)
class Delta:
    """A piece of world knowledge an actor learned — the unit of gossip communication.
    Immutable; carries only what was revealed, never a handle to global state."""
    origin: str
    domain: str
    src: str
    dst: str
    reward: float = 0.0
    confidence: float = 0.0
    dst_domain: str | None = None

    def key(self) -> tuple:
        return (self.domain, self.src, self.dst)


@dataclass
class CognitiveState:
    """Mutable state tracking the actor's cognitive lifecycle."""
    tick_count: int = 0
    last_tick: datetime | None = None
    last_observation: dict = field(default_factory=dict)
    last_plan: dict | None = None
    last_execution: dict | None = None
    last_prediction: dict | None = None
    converged: bool = False


class CognitiveActor(Entity):
    """The canonical autonomous actor with full cognitive capabilities.

    Three explicit responsibilities:

    Identity & Domain
        entity_id, name, type, description
        goals, objective, capabilities
        affiliations (AffiliationManager)

    Cognition
        beliefs, policy, actions
        tick() → _cognitive_tick() → BeliefFormation pipeline
        plan(), simulate(), execute(), learn() (local graph operations)

    Social
        peers, gossip (Delta exchange)
        messaging via self.os

    The actor delegates ALL infrastructure to CognitiveOS (self.os):
        self.os.world()           → read-only world access
        self.os.send_message()    → inter-agent messaging
        self.os.score()           → objective-aware scoring
        self.os.transition()      → learned transition model

    The actor NEVER knows about SocietyRuntime or PlanetaryRuntime.

    ARCHITECTURAL INVARIANT:
        from ...society.runtime import SocietyRuntime  # FORBIDDEN
        from ...society.integration import PlanetaryRuntime  # FORBIDDEN
    """

    def __init__(
        self,
        entity_id: str,
        entity_type: EntityType = EntityType.ENTITY,
        world_view: Any = None,
        objective: str = "",
        goals: list[str] | None = None,
        engine: Any = None,
        context_factory: Callable[[str], Any] | None = None,
        name: str = "",
        **entity_kwargs: Any,
    ) -> None:
        """
        engine: an optional pre-wired cognitive engine (e.g. from
            domains/vertical_router.py's build_runtime_engine("grocery")) to
            drive this actor's cognitive tick instead of the default
            comparison-integrated runtime. Lets a vertical-specific planner/
            execution-engine pair (domain-mapped plan steps -> real
            CapabilityBus capabilities) reach a Society/PlanetaryRuntime-
            registered actor, which nothing wires by default.
        context_factory: optional callable producing the RuntimeContext (or
            plain dict, for verticals whose capabilities read a dict-shaped
            context) passed into the cognitive tick. Defaults to the
            existing RuntimeContext(world=self._world_view) behavior.
            Receives this tick's real triggering/goal text (the same text
            _cognitive_tick threads into compiled.goal below) so a
            dict-shaped context can set "question" to it — previously this
            was a fixed, no-argument callable with no per-tick hook at all,
            so every capability reading context["question"] (e.g.
            ProductSelectionCapability's request-text parsing) always saw
            "" and silently took its empty-candidates fallback path.
        name: this actor's real display name (ActorProfile.identity.name
            at the registration layer — CognitiveActor itself, and the
            pipeline Actor/CognitiveState beneath it, never otherwise
            carry one; see kernel/pipeline/prediction/persistence.py's
            module docstring for why that mattered for transition-model
            persistence). Optional/defaulted — every existing construction
            site that doesn't pass it keeps working unchanged.
        """
        super().__init__(id=entity_id, entity_type=entity_type, **entity_kwargs)
        self.name = name

        # ── Belief model (Layer 2) ──────────────────────────────────────────
        self.belief = SparseTransitionTensor()
        self._actor_belief = ActorBelief(entity_id, world_view=world_view)
        self._actions = ActorModel(entity_id, self.belief)

        # ── Policy (Layer 3) ────────────────────────────────────────────────
        from src.monkey_brain.kernel.policy.store import PolicyStore
        self.policy = PolicyStore(owner_id=entity_id)

        # ── Social graph ────────────────────────────────────────────────────
        self.peers: set[CognitiveActor] = set()
        self._seen: set[tuple] = set()

        # ── Cognitive lifecycle ─────────────────────────────────────────────
        self._cognitive_state = CognitiveState()
        self._shutdown = False
        self._tick_duration = 0.1  # 100ms between async ticks

        # ── World references (injected by CognitiveOS) ──────────────────────
        self._world_view = world_view
        self._world = world_view  # backward compat alias
        self._os: Any = None  # set by CognitiveOS.create_actor()

        # ── Domain Knowledge Graph ──────────────────────────────────────────
        from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
        self._knowledge_graph = KnowledgeGraph(person_id=entity_id)

        # ── Goal (Step 12.2: Actor Runtime owns its own goal) ────────────────
        # Level 42 (GS-4200): goals carry a real priority, and _current_goal
        # is always the highest-priority QUEUED goal — not just "goals[0]
        # at construction time" — so a more urgent goal added mid-run
        # (add_goal) genuinely preempts whatever the actor was pursuing on
        # its very next tick, and a completed goal (_complete_goal) falls
        # back to the next-highest-priority one still queued, if any.
        self._goal_queue: list[dict[str, Any]] = [{"goal": g, "priority": 0.0, "skip_next": False} for g in (goals or [])]
        # Phase 5 stress (GS-5200): an index alongside the queue, keyed by
        # goal text, so add_goal/_defer_goal can look up an existing entry
        # in O(1) instead of a linear scan over the whole queue. Without
        # this, an actor that accumulates many DISTINCT goals over its
        # lifetime (a real scenario for the "personal lifelong runtime"
        # this architecture targets) pays O(n) per add_goal call, making N
        # sequential calls cost O(n^2) overall — measured live: 10,000
        # sequential add_goal calls took 3.2s (317us/call by the end)
        # purely from the existing-goal scan, before this fix.
        self._goal_index: dict[str, dict[str, Any]] = {entry["goal"]: entry for entry in self._goal_queue}
        self._current_goal: str | None = self._select_current_goal()
        self._objective: str = objective
        """Optimization objective: 'cost', 'speed', 'reliability', or ''."""
        self._goals: list[str] = goals or []
        """List of goals this actor pursues."""

        # ── Affiliations (Actor-centric: actor owns all relationships) ────
        from src.monkey_brain.kernel.affiliations.manager import AffiliationManager
        self._affiliations = AffiliationManager()

        # ── Canonical Cognitive Engine (Step 12.2: delegation, not duplication) ─
        # One instance, reused across ticks — construction is cheap and the
        # engine is stateless across calls by design (belief_runtime.py's own
        # docstring). This IS belief_runtime.py's CognitiveRuntime — the single
        # implementation of Observe->Believe->Plan->Execute->ObserveOutcome->
        # Learn->CompileΦ->Predict->Commit. Step 12.11: _cognitive_tick() now
        # calls .tick(state) directly, passing a persistent belief/identity
        # pair (below) rather than rebuilding everything fresh via .run().
        self._cognitive_engine: Any = None
        if engine is not None:
            from src.monkey_brain.kernel.pipeline.belief_formation import BeliefFormation
            self._cognitive_engine = BeliefFormation(engine=engine)

        # Optional override for the context passed into the cognitive tick —
        # see the `context_factory` parameter docstring above. None keeps
        # the existing RuntimeContext(world=self._world_view) behavior.
        self._context_factory = context_factory

        # ── Persistent pipeline belief + identity (Step 12.11) ───────────────
        # The pipeline's own BeliefState/Actor — held here and reused BY
        # REFERENCE across every _cognitive_tick() call, so facts/hypotheses/
        # predictions the canonical engine accumulates into belief, and the
        # pipeline actor's own cycle_count, genuinely persist cycle over
        # cycle. Everything ELSE on a pipeline CognitiveState (errors,
        # diagnostics, execution_trace, actions, outcome...) is explicitly
        # documented as "discarded after cycle, not persisted" by
        # execution_state.py's own docstring — so _cognitive_tick() still
        # builds a FRESH CognitiveState every call (matching that contract)
        # and only threads THESE two persistent objects into it, instead of
        # holding one CognitiveState forever (which would leak those
        # accumulate-only lists unboundedly across an actor's lifetime).
        self._pipeline_belief: Any = None
        self._pipeline_actor: Any = None

    # ── Backward compatibility ──────────────────────────────────────────────

    @property
    def entity_id(self) -> str:
        """Alias for `.id` (Entity's field name) — satisfies ActorProtocol,
        which names this field entity_id to match the actor-runtime contract
        vocabulary used elsewhere (register_actor, ActorRuntimeState.actor_id)."""
        return self.id

    @property
    def actor_id(self) -> str:
        """Alias for `.id` — satisfies AutonomousActorProtocol
        (src/shared/actor_protocols.py), the contract ActorScheduler
        (kernel/actor_scheduler.py) registers actors against."""
        return self.id

    @property
    def knowledge_graph(self):
        """The actor's domain knowledge graph — stores, products, wallets, riders, etc."""
        return self._knowledge_graph

    @knowledge_graph.setter
    def knowledge_graph(self, value) -> None:
        self._knowledge_graph = value

    @property
    def world(self) -> SparseTransitionTensor:
        return self.belief

    @world.setter
    def world(self, value: SparseTransitionTensor) -> None:
        self.belief = value

    @property
    def actions(self) -> ActorModel:
        return self._actions

    @actions.setter
    def actions(self, value: ActorModel) -> None:
        self._actions = value

    # ── World injection ─────────────────────────────────────────────────────

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CognitiveActor):
            return NotImplemented
        return self.id == other.id

    def set_world(self, world: Any) -> None:
        self._world_view = world

    @property
    def os(self) -> Any:
        """The CognitiveOS this actor belongs to. Set by CognitiveOS.create_actor().

        Actor delegates infrastructure to self.os — never instantiates
        runtime components directly.
        """
        return self._os

    @os.setter
    def os(self, value: Any) -> None:
        """Set by CognitiveOS only. Do not call directly."""
        self._os = value

    @property
    def affiliations(self):
        """Actor-owned affiliation and trust management."""
        return self._affiliations

    def set_goal(self, goal: str | None) -> None:
        """Set this actor's current goal for its autonomous cognitive loop.

        Step 12.2: Actor Runtime owns goals as persistent state, not a
        per-call parameter — unlike cognitive_cycle()'s synchronous
        start/goal arguments, execute_cognitive_loop() ticks continuously
        against whatever goal was last set here.

        A direct override, distinct from the priority queue (add_goal/
        _select_current_goal) — it replaces _current_goal outright without
        touching _goal_queue, for callers that want to bypass prioritization
        entirely (e.g. a caller that never uses add_goal at all)."""
        self._current_goal = goal

    def add_goal(self, goal: str, priority: float = 0.0) -> None:
        """Level 42 (GS-4200): queue a new goal at the given priority, or
        reprioritize it if already queued. _current_goal is immediately
        re-selected — a higher-priority goal genuinely preempts whatever
        the actor was pursuing, taking effect on the very next tick,
        without needing set_goal() called explicitly or the actor
        reconstructed. Ties are broken by queue (insertion) order, so
        behavior stays deterministic rather than depending on dict/list
        iteration accidents.
        """
        existing = self._goal_index.get(goal)
        if existing is not None:
            was_current_and_decreased = goal == self._current_goal and priority < existing["priority"]
            existing["priority"] = priority
            existing["skip_next"] = False
            if was_current_and_decreased:
                # The current goal just got LESS urgent — something else
                # in the queue might now be the true max, and only this
                # case genuinely needs the full O(n) rescan below.
                self._current_goal = self._select_current_goal()
                return
            entry = existing
        else:
            entry = {"goal": goal, "priority": priority, "skip_next": False}
            self._goal_queue.append(entry)
            self._goal_index[goal] = entry

        # Phase 5 stress (GS-5200): an O(1) incremental update for every
        # other case (new goal, or reprioritized upward/unchanged) —
        # the current choice only needs to be compared against THIS one
        # entry, not rescanned from the whole queue via
        # _select_current_goal. Without this, N sequential add_goal
        # calls each paid the full O(n) scan-and-max cost, making an
        # actor that accumulates many distinct goals over its lifetime
        # (a real "personal lifelong runtime" scenario) cost O(n^2)
        # overall — measured live: 10,000 sequential calls took 3.2s
        # (240us/call by the end) purely from this, before the fix.
        current_entry = self._goal_index.get(self._current_goal) if self._current_goal is not None else None
        if current_entry is None or current_entry.get("skip_next") or entry["priority"] > current_entry["priority"]:
            self._current_goal = entry["goal"]

    def _select_current_goal(self) -> str | None:
        """The real, priority-ordered choice of what to pursue next — the
        highest-priority goal still in the queue that hasn't just failed,
        or None if the queue is empty.

        Level 44 (GS-4400): a goal that fails a real attempt is marked
        "skip_next" (see _cognitive_tick) so a DIFFERENT queued goal gets
        a turn next — without this, a permanently-impossible high-priority
        goal (out of stock forever, insufficient funds forever) would be
        re-selected as the highest-priority candidate on every single
        tick, starving every other queued goal indefinitely. Once every
        remaining goal has failed its most recent attempt (nothing left
        that HASN'T just failed), the skip flags are cleared and the whole
        queue becomes eligible again — a goal that seemed impossible can
        still succeed later (Level 38's restock case), it just isn't
        allowed to monopolize every tick forever while other goals could
        make real progress.
        """
        if not self._goal_queue:
            return None
        candidates = [g for g in self._goal_queue if not g.get("skip_next")]
        if not candidates:
            for g in self._goal_queue:
                g["skip_next"] = False
            candidates = self._goal_queue
        return max(candidates, key=lambda g: g["priority"])["goal"]

    def _complete_goal(self, goal: str) -> None:
        """Removes an achieved goal from the queue (Level 42) and
        re-selects the next-highest-priority one still queued, if any —
        the actor automatically falls back to a lower-priority goal it
        had queued rather than going idle just because a more urgent one
        finished."""
        self._goal_queue = [g for g in self._goal_queue if g["goal"] != goal]
        self._goal_index.pop(goal, None)
        self._current_goal = self._select_current_goal()

    def _defer_goal(self, goal: str) -> None:
        """Level 44 (GS-4400): a real attempt at `goal` failed — mark it
        so _select_current_goal gives a different queued goal a turn next,
        instead of immediately re-selecting the same goal that just failed."""
        entry = self._goal_index.get(goal)
        if entry is not None:
            entry["skip_next"] = True
        self._current_goal = self._select_current_goal()

    def _mark_goal_completed(self, state: Any) -> None:
        """Cognitive State Quality Pass: a real status transition, not a
        new mechanism — the same append-only pattern MembershipRecord
        already uses elsewhere for status changes (a new row, never an
        edit to a prior one). Without this, every GoalRecord stayed
        "active" forever (update_goal() always wrote status="active"),
        even after _complete_goal() (above) already recognized the goal
        was done in the actor's own in-memory queue — the persisted
        GoalTimeline just never learned about it, which is why goals
        only ever accumulated instead of completing."""
        from src.monkey_brain.kernel.timeline.entry import TimelineKind
        from src.monkey_brain.kernel.timeline.store import TimelineStore
        current_goal = state.belief.goal
        if not current_goal.name.strip():
            return
        TimelineStore().record(
            TimelineKind.GOAL, actor_id=self.id, name=current_goal.name,
            description=current_goal.description,
            success_criteria=tuple(current_goal.success_criteria),
            optimization_objective=current_goal.optimization_objective,
            status="completed",
        )

    def _get_cognitive_engine(self) -> Any:
        """Lazily construct the canonical cognitive engine. Lazy import to
        avoid a module-level import cycle between kernel/compile/* and
        kernel/pipeline/* (the same lazy-import shape Step 10.7/11.7's
        LearningIntegratedPolicy/PredictionIntegratedPolicy factories used
        for the identical reason)."""
        if self._cognitive_engine is None:
            from src.monkey_brain.kernel.pipeline.belief_formation import BeliefFormation
            self._cognitive_engine = BeliefFormation()
        return self._cognitive_engine

    def _get_pipeline_belief(self) -> Any:
        """Lazily construct this actor's persistent pipeline BeliefState —
        built once, reused by reference on every _cognitive_tick() call
        (Step 12.11). Distinct from `self._actor_belief` (compile-layer
        ActorBelief/SparseTransitionTensor — this actor's OWN data model,
        used by plan()/simulate()/execute()/learn() above) and from
        `self.belief` (the raw SparseTransitionTensor alias) — three
        different belief representations at three different layers, none
        of which this method reconciles into the others; only
        _cognitive_tick()'s bookkeeping (self._actor_belief.remember(...))
        does that translation, same as before 12.11."""
        if self._pipeline_belief is None:
            from src.monkey_brain.kernel.pipeline.belief_state import BeliefState as PipelineBeliefState
            self._pipeline_belief = PipelineBeliefState(
                actor_id=self.id, tenant_id=self.tenant_id or "default",
            )
        return self._pipeline_belief

    def pipeline_belief(self) -> Any:
        """Public accessor for this actor's canonical pipeline BeliefState
        (Step 14 — Architecture Consolidation: the one representation
        CognitiveRuntime.tick() actually reads/writes). Used by
        PlanetaryRuntime.checkpoint_actor_belief() to serialize belief
        for cross-request persistence — see kernel/society/integration.py."""
        return self._get_pipeline_belief()

    def restore_pipeline_belief(self, belief_state: Any) -> None:
        """Replace this actor's canonical pipeline BeliefState wholesale,
        e.g. after PlanetaryRuntime.restore_actor_belief() reconstructs one
        from persisted storage. Must run before any _cognitive_tick() call —
        this method does not itself enforce that ordering; the caller does
        (see PlanetaryRuntime.restore_actor_belief(), called immediately
        after world validation and before execute_actor_request)."""
        self._pipeline_belief = belief_state

    def _get_pipeline_actor(self) -> Any:
        """Lazily construct this actor's persistent pipeline Actor identity
        (Step 12.11) — reused by reference so `cycle_count`/`status`/
        `last_reasoned_at` genuinely accumulate across ticks instead of
        resetting every cycle the way a fresh Actor() would."""
        if self._pipeline_actor is None:
            from src.monkey_brain.kernel.pipeline.actor import Actor as PipelineActor
            self._pipeline_actor = PipelineActor(
                actor_id=self.id, tenant_id=self.tenant_id or "default",
            )
        return self._pipeline_actor

    def pipeline_actor(self) -> Any:
        """Public accessor for this actor's pipeline Actor identity
        (cycle_count/last_reasoned_at). Used by
        PlanetaryRuntime.checkpoint_actor_belief() (Step 14)."""
        return self._get_pipeline_actor()

    # ── Layer 2: Belief Formation ───────────────────────────────────────────

    def _learn(self, delta: Delta) -> bool:
        """Fold a revealed fact into the local belief. False if already known (stops gossip)."""
        if delta.key() in self._seen and self.belief.has_edge(delta.src, delta.dst):
            return False
        self._seen.add(delta.key())
        self.belief.observe(
            delta.src, delta.dst, domain=delta.domain,
            reward=delta.reward, confidence=delta.confidence,
        )
        self._actor_belief.observe(
            delta.src, delta.dst, reward=delta.reward,
            confidence=delta.confidence, source="gossip", domain=delta.domain,
        )
        return True

    def knows(self, src: str, dst: str) -> bool:
        """Check if this actor knows a specific transition."""
        return any((s, d) == (src, dst) for s, d in self.belief)

    def observe_transition(self, src: str, dst: str, *, reward: float = 0.0,
                           confidence: float = 1.0, domain: str = "default") -> None:
        """Record a direct observation into both the tensor belief and ActorBelief."""
        self.belief.observe(src, dst, domain=domain, reward=reward, confidence=confidence)
        self._actor_belief.observe(src, dst, reward=reward, confidence=confidence,
                                   source="direct", domain=domain)

    # ═══════════════════════════════════════════════════════════════════════
    # Actor Runtime review (Society architecture hardening), Phase 5: five of
    # the six methods below -- plan/simulate/execute/learn/cognitive_cycle --
    # are a SEPARATE, SYNCHRONOUS, non-LLM cognitive mechanism: graph
    # pathfinding + propagation over this actor's own local SparseTransitionTensor,
    # genuinely live (kernel/compile/actor_runtime.py::ActorRuntime.
    # cognitive_cycle(), kernel/compile/society_runtime.py), but NOT what a
    # real grocery/commerce prompt request runs. They share vocabulary with,
    # but are NOT the same mechanism as, this class's own async LLM-driven
    # engine: tick() -> _cognitive_tick() -> belief_runtime.py's canonical
    # Observe->Believe->Plan->Predict->Decide->Govern->Execute->Commit
    # pipeline (used by every real /prompt request, confirmed: zero import of
    # this synchronous path from src/monkey_brain/api/). In particular,
    # `execute()` below never reaches ensure_governed and never touches a
    # real capability -- it advances a LOCAL simulation against a supplied
    # SparseTransitionTensor `world` argument, nothing external. Do not
    # confuse a call to `actor.execute(...)` here with the governed
    # capability-execution boundary the real engine's Execute stage uses.
    #
    # The sixth, compile_phi(), is the ONE deliberate exception: a genuinely
    # shared, single-purpose utility (compile the Bellman policy into a
    # sparse transition operator Φ) that execute_cognitive_loop() ALSO calls
    # directly after the real async engine's own learning step -- reuse of
    # one real utility, not a second copy of cognition.
    # ═══════════════════════════════════════════════════════════════════════

    # ── Layer 3: Decision (Planning) ────────────────────────────────────────

    def plan(self, start: str, goal: str, k: int = 12) -> list[str]:
        """Plan a path start→goal through this actor's LOCAL belief graph.
        Uses propagation + greedy dominant-successor. Returns [start] if no route yet."""
        m = SparseMatrix.from_tensor(self.belief, Feature.PROBABILITY)
        trace = m.propagate_k({start: 1.0}, k)
        path = [start]
        for step in trace[1:]:
            if not step:
                break
            nxt = max(step, key=step.get)
            if nxt in path:
                break
            path.append(nxt)
            if nxt == goal:
                break
        return path

    def simulate(self, plan: list[str]) -> dict[str, float]:
        """Predict the plan's outcome distribution using the local operator."""
        if len(plan) < 2:
            return {plan[0]: 1.0} if plan else {}
        m = SparseMatrix.from_tensor(self.belief, Feature.PROBABILITY)
        return m.propagate_k({plan[0]: 1.0}, len(plan) - 1)[-1]

    # ── Execution ───────────────────────────────────────────────────────────

    def execute(self, plan: list[str], world: SparseTransitionTensor,
                goal: str | None = None, horizon: int = 64) -> list[tuple[str, str | None]]:
        """Execute against the given world (global/constrained), following true transitions
        and folding each into the local belief. The plan is a hint; when incomplete,
        the actor explores up to `horizon` steps."""
        observed: list[tuple[str, str | None]] = []
        cur: str | None = plan[0] if plan else None
        seen: set[str] = set()
        for _ in range(horizon):
            if cur is None or cur in seen:
                break
            seen.add(cur)
            succ = {d: world.feature(cur, d, Feature.PROBABILITY) for d, _ in world.successors(cur)}
            if not succ:
                break
            actual = max(succ, key=succ.get)
            observed.append((cur, actual))
            self.observe_transition(cur, actual, domain=world.domain_of(cur),
                                    reward=world.feature(cur, actual, Feature.REWARD))
            cur = actual
            if goal is not None and actual == goal:
                break
        return observed

    # ── Learning ────────────────────────────────────────────────────────────

    def learn(self, observed: list[tuple[str, str | None]], reward: float) -> None:
        """Update this actor's LOCAL policy from observations.
        Updates both PolicyStore (Layer 3) and ActorBelief memory."""
        for a, b in observed:
            if b:
                self.policy.update(a, "transition", reward, b)
                self._actor_belief.update_bellman(a, "transition", reward, b)
        self._actor_belief.remember({
            "type": "cognitive_cycle",
            "observed": [(a, b) for a, b in observed],
            "reward": reward,
        })

    # ── Φ Compilation ──────────────────────────────────────────────────────

    def compile_phi(self) -> SparseTransitionTensor | None:
        """Compile Bellman policy into sparse transition operator Φ.
        Φ is actor-local and persistent until recompiled."""
        phi = self._actor_belief.compile_phi()
        return phi

    # ── Synchronous Cognitive Cycle ─────────────────────────────────────────

    def cognitive_cycle(self, start: str, goal: str, world: SparseTransitionTensor,
                        *, reward: float = 1.0) -> CycleResult:
        """Full plan → simulate → execute → learn over the actor's own graph."""
        plan = self.plan(start, goal)
        predicted = self.simulate(plan)
        observed = self.execute(plan, world, goal=goal)
        observed_end = {observed[-1][1]: 1.0} if observed and observed[-1][1] else {}
        loss = epistemic_loss(
            SparseMatrix({("_end", k): v for k, v in predicted.items()}),
            SparseMatrix({("_end", k): v for k, v in observed_end.items()}),
        )
        self.learn(observed, reward)
        reached = bool(observed) and observed[-1][1] == goal
        logger.info("[actor %s] cycle %s→%s plan=%s reached=%s loss=%.2f",
                    self.id, start, goal, "→".join(plan), reached, loss)
        return CycleResult(plan, predicted, observed, loss, reward, reached)

    # ── Async Cognitive Loop (Phase 8) ──────────────────────────────────────

    async def tick(self, prompt_request: Any = None) -> _CognitiveTickResult:
        """Public entry point for coordination (Step 12.3: Society Runtime,
        Step 12.4: Planetary Runtime) to invoke this actor's complete
        cognitive cycle. One call, one full Observe->...->Commit pass —
        coordinators call this without knowing or caring that it's
        implemented via delegation to the canonical engine underneath."""
        return await self._cognitive_tick(prompt_request)

    async def execute_cognitive_loop(self) -> None:
        """Main autonomous execution loop — pure scheduling, no stage logic
        of its own (Step 12.2). Each tick delegates the complete cognitive
        lifecycle to the canonical engine via _cognitive_tick()."""
        while not self._shutdown:
            try:
                result = await self._cognitive_tick()
                self._cognitive_state.tick_count += 1
                self._cognitive_state.last_tick = datetime.now()
                if result.learned:
                    self.compile_phi()
                await asyncio.sleep(self._tick_duration)
            except Exception as e:
                logger.error("[actor %s] cognitive loop error: %s", self.id, e)
                await asyncio.sleep(self._tick_duration * 2)

    async def _cognitive_tick(self, prompt_request: Any = None) -> _CognitiveTickResult:
        """Execute one complete cognitive cycle by delegating to the
        canonical engine — Observe->Believe->Plan->Predict->Execute->
        ObserveOutcome->Compare->Learn->CompileΦ->Commit, implemented
        exactly once, in belief_runtime.py (base 9-stage order) and
        pipeline/comparison/integration.py (the real ComparisonIntegratedPolicy
        engine actually used, which reorders Predict before Execute and adds
        Compare). This method never re-implements a stage; it
        only builds the inputs the engine needs and reconciles the result
        back into this actor's own persistent belief.

        """
        from src.monkey_brain.kernel.pipeline.contracts import (
            PipelineRequest, CompiledRequest, RuntimeContext,
        )
        from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState as PipelineCognitiveState

        triggering_event = (
            getattr(prompt_request, "question", None)
            or (prompt_request.get("question") if isinstance(prompt_request, dict) else None)
        )
        # Combine, don't replace: a reactive (broadcast_context) tick's
        # triggering event explains WHY this actor woke up, but dropping
        # its own standing goal (e.g. "manage_loyalty_program") left the
        # LLM with only a generic instruction among many available
        # actions, unable to tell which capability is actually its job —
        # see MB-3105.
        if triggering_event and self._current_goal:
            goal = f"{self._current_goal} {triggering_event}"
        else:
            goal = triggering_event or self._current_goal or ""

        compiled = CompiledRequest(
            request=PipelineRequest(
                question=goal or f"actor:{self.id} autonomous tick",
                actor_id=self.id,
                tenant_id=self.tenant_id or "default",
            ),
            # Cognitive Loop Verification: this was hardcoded to
            # "autonomous_tick" unconditionally, so an explicit prompt
            # ("Buy 1L of milk.") persisted with the exact same
            # intent_type as a promptless background cycle. Real intent
            # classification (kernel/plan/goals/compile.py::compile_intent)
            # exists in this codebase but its predicate registry
            # (kernel/plan/intents/intent_registry.py) is entirely
            # manufacturing/pharma (batch records, work orders, SOPs) —
            # wiring it here would swap this wrong constant for an
            # unclassified None for every real grocery/commerce request,
            # not fix it. This distinguishes the two cases this call site
            # already knows for certain, honestly, without claiming NLU
            # this vertical doesn't have.
            intent={"intent": "user_request" if triggering_event else "autonomous_tick", "confidence": 1.0},
            # Real gap this closes: name/description were both set to the
            # SAME combined "standing goal + one-off triggering text"
            # string above — harmless for planning, but
            # BeliefState.update_goal() (belief_runtime.py) uses `name`
            # as its Goal Timeline dedup key, and every distinct
            # one-off /prompt question (different triggering_event, same
            # real standing goal) produced a DIFFERENT combined string,
            # so update_goal's dedup never recognized it as "the same
            # goal" — confirmed live: an actor's real Goal Timeline
            # filled with garbage entries like "buy groceries efficiently
            # what did we discuss about coffee", one per test question,
            # none of them a real standing goal the actor was ever given.
            # Splitting name (the real standing goal alone — stable
            # across ticks, so it correctly dedups) from description
            # (just the one-off triggering context) fixes that without
            # losing anything: context_engine.py's own
            # goal_text = f"{name} {description}" already reconstructs
            # the exact same combined text planning needs.
            # Found live: a background auto-tick can re-send the standing
            # goal itself as its own triggering_event (e.g. Arjun Mehta's
            # only goal, "find the best grocery deals", firing as both
            # self._current_goal AND triggering_event on the same tick) —
            # description would then just repeat name verbatim, producing
            # "find the best grocery deals find the best grocery deals" in
            # every real place full_goal_text gets reconstructed. Only
            # keep description when it's genuinely additional context.
            goal={
                "name": self._current_goal or triggering_event or "",
                "description": (
                    triggering_event
                    if self._current_goal and triggering_event and triggering_event.strip() != self._current_goal.strip()
                    else ""
                ),
                "optimization_objective": self._objective,
            },
            intent_ir=None,
            goal_ir=None,
            execution_context=None,
        )
        context = (
            self._context_factory(goal) if self._context_factory is not None
            else RuntimeContext(world=self._world_view)
        )

        state = PipelineCognitiveState(
            compiled=compiled,
            context=context,
            actor=self._get_pipeline_actor(),
            belief=self._get_pipeline_belief(),
        )
        # belief_updated (below, at the _CognitiveTickResult construction
        # site) reports whether the pipeline's own stages actually
        # mutated this persistent BeliefState this tick, not just whether
        # the tick ran -- BeliefState._touch() (belief_state.py) bumps
        # .version on every real mutation (add_observation/add_hypothesis/
        # update_goal/record_learning/decay_and_prune/etc.), so a delta
        # here is a real, not assumed, signal. Captured before any stage
        # runs; this was previously hardcoded True regardless of outcome
        # (see docs/adr/019-runtime-performance-audit.md's finding).
        belief_version_before = state.belief.version
        # Planetary Narrative: one real id for this whole tick, generated
        # here (before any stage runs) so every stage/record writer that
        # sees `state` can tag its own Timeline record with it — see
        # _CognitiveTickResult.execution_id's own docstring.
        #
        # Checkpoint/restart: a caller that knows it's RETRYING a request
        # whose earlier attempt may have partially completed (crash,
        # timeout, lost response -- not a fresh request) states that
        # explicitly via meta.resume_execution_id, same idiom as
        # meta.single_actor_only above and the same "caller states intent,
        # nothing is auto-discovered" precedent OrderCreationCapability's
        # resume_order_id already established at the order level. Using
        # THIS id (instead of a fresh uuid) is what lets
        # execution_checkpoint_store's step-by-step record for the
        # earlier attempt actually be found by the resumed one.
        from uuid import uuid4
        meta = getattr(prompt_request, "meta", None) or (
            prompt_request.get("meta") if isinstance(prompt_request, dict) else None
        ) or {}
        resume_execution_id = meta.get("resume_execution_id") if isinstance(meta, dict) else None
        execution_id = resume_execution_id or uuid4().hex
        state.metrics["execution_id"] = execution_id

        # optimization_objective (self._objective, above in compiled.goal) is
        # set on belief.goal by the pipeline's own _update_beliefs stage —
        # not here. update_goal() replaces belief.goal wholesale, so a manual
        # pre-set here would just get silently overwritten the moment
        # formation.from_state() runs that stage.
        formation = self._get_cognitive_engine()
        # LLMPlanner retries up to _MAX_PARSE_ATTEMPTS (3) times on a bad
        # completion, and a local Ollama model can take 15-60s per call —
        # 60s total left no room for more than one attempt before this
        # fired, turning a slow-but-working model into a hard failure on
        # anything past the simplest plan. 240s comfortably covers 3
        # attempts even at the slower end.
        formation_result = await formation.from_state(state, timeout_seconds=240.0)

        if not formation_result.success:
            # from_state() catches timeouts/exceptions internally and reports
            # them via FormationResult.errors instead of raising (so a batch
            # coordinator ticking many actors isn't torn down by one bad
            # actor). But this method's own two callers — execute_cognitive_loop()
            # (has its own try/except around _cognitive_tick()) and the HTTP
            # tick route via SocietyRuntime.tick_one_actor() (also wraps
            # exceptions into a 500) — both already expect pipeline failures
            # to raise. Without this, a broken pipeline now returns a
            # normal-looking 200 instead of the previous 500.
            detail = "; ".join(e.get("message", "") for e in formation_result.errors) or "unknown error"
            raise RuntimeError(f"Cognitive tick failed: {detail}")

        self._cognitive_state.last_observation = {"outcome": state.outcome}
        # Plan invalidation: surface a stale-plan rejection to the caller
        # explicitly (PLAN_STALE) rather than it only being visible in the
        # audit timeline — state.metrics is set by comparison/integration.py
        # ::_run_decide or belief_runtime.py::_generate_plan's skip-gate
        # whenever a Current Plan's world-state assumptions no longer
        # hold. Absent (not just falsy) when nothing was stale this tick.
        plan_stale = state.metrics.get("plan_stale") if isinstance(state.metrics, dict) else None
        if plan_stale:
            self._cognitive_state.last_observation["plan_stale"] = plan_stale
        self._cognitive_state.last_plan = state.plan
        self._cognitive_state.last_execution = {"actions": [a.__dict__ if hasattr(a, '__dict__') else a for a in state.actions], "status": "completed" if formation_result.success else "failed"}
        self._cognitive_state.last_prediction = getattr(state, "prediction_result", None)
        self._cognitive_state.converged = formation_result.goal_achieved

        # Cognitive State refactor: state.belief.intent and state.plan are
        # both real, already-computed cognitive artifacts that this method
        # already reconciles into self._cognitive_state (above) — but only
        # as the single "last" value, discarded next tick. Persist them
        # into the append-only Timeline too, so a developer/user can see
        # not just the CURRENT intent/plan but every one this actor ever
        # had, the same way GoalRecord/ExecutionRecord already work.
        self._record_cognitive_artifacts(state, formation_result)

        # Level 42 (GS-4200): an achieved goal is done — drop it from the
        # priority queue and re-select whatever's next, so the actor
        # automatically falls back to a lower-priority goal it still had
        # queued instead of continuing to "pursue" a goal that's already
        # been satisfied.
        if formation_result.goal_achieved and goal:
            self._complete_goal(goal)
            self._mark_goal_completed(state)
        # Level 44 (GS-4400): a real attempt that DIDN'T achieve the goal
        # (out of stock, insufficient funds, ...) defers it for one
        # selection round instead of leaving it queued to be immediately
        # re-picked next tick — see _select_current_goal's docstring for
        # why an un-deferred failure would starve every other queued goal.
        elif goal and self._goal_queue:
            self._defer_goal(goal)

        self._actor_belief.remember({
            "type": "cognitive_tick",
            "goal": goal,
            "status": "completed" if formation_result.success else "failed",
            "goal_achieved": formation_result.goal_achieved,
            "actions": formation_result.actions_executed,
            "reward": formation_result.reward,
            "actor_loss": formation_result.actor_loss,
        })

        # Context-event publishing is handled by the coordinator
        # (SocietyRuntime._coordinate_actor()), not by the actor.
        # The actor never publishes events directly.

        return _CognitiveTickResult(
            tick=self._cognitive_state.tick_count,
            observations=self._cognitive_state.last_observation,
            belief_updated=state.belief.version != belief_version_before,
            plan=state.plan or {},
            actions=[a.__dict__ if hasattr(a, '__dict__') else a for a in state.actions],
            predicted_outcome=getattr(state, "prediction_result", None) or {},
            actual_outcome=state.outcome or {},
            error=formation_result.actor_loss,
            # Cognitive Loop Verification (round 2): this was
            # formation_result.goal_achieved — real learning (reward/loss
            # computation) happens whenever the Learn/Compare stages
            # genuinely ran, independent of whether the specific business
            # goal was achieved. Confirmed live: a partial-success tick
            # had learned=False here alongside a real, non-zero
            # actor_loss — the field just meant something other than
            # what its name claims.
            learned=formation_result.success,
            execution_id=execution_id,
            stage_timings_ms={**state.stage_durations, **state.metrics.get("stage_timings_ms", {})},
        )

    def _record_cognitive_artifacts(self, state: Any, formation_result: Any) -> None:
        """Persist this tick's Intent and Plan into the append-only
        Timeline (kernel/timeline/) — both are real, already-computed by
        the pipeline (state.belief.intent, state.plan), previously kept
        only as self._cognitive_state.last_plan/discarded once this
        coroutine returns. Mirrors belief_runtime.py's _record_execution
        exactly: construct-via-TimelineStore.record(), never the
        dataclasses directly (see the exclusive-constructor conformance
        check)."""
        from src.monkey_brain.kernel.timeline.entry import TimelineKind
        from src.monkey_brain.kernel.timeline.store import TimelineStore
        from src.monkey_brain.kernel.compile import _obs

        # Planetary Narrative: the one id shared by every record this
        # tick produces — see _CognitiveTickResult.execution_id's own
        # docstring for why (composing one execution's full narrative
        # without it means guessing which records from independent
        # per-kind queries actually belong to the same tick).
        execution_id = state.metrics.get("execution_id", "")

        intent = state.belief.intent
        TimelineStore().record(
            TimelineKind.INTENT, actor_id=self.id, intent_type=intent.type,
            confidence=intent.confidence,
            metadata={**dict(intent.metadata), "execution_id": execution_id},
        )
        _obs.counter("cognitive.intents_classified")

        # Cognitive State Quality Pass: BeliefFusion (kernel/society/
        # belief.py) only ever sees SharedWorld entities (geography/
        # presence) — it has no visibility into the real, KG-grounded
        # commerce facts (real product/price/id) the planner actually
        # retrieved and reasoned over for THIS goal (belief_runtime.py's
        # _generate_plan now stashes them on belief.metadata
        # ["relevant_knowledge"] instead of discarding them). Persisting
        # them here as real Beliefs is what a debugger needs to answer
        # "what did the actor know when it made this plan" — not a
        # second belief-formation mechanism, just not throwing this one
        # away.
        for item in state.belief.metadata.get("relevant_knowledge") or ():
            content = getattr(item, "content", "") or ""
            if not content.strip():
                continue
            source = getattr(item, "source", "") or "knowledge_graph"
            # Cognitive Loop Verification (round 2): subject used to be
            # content[:80] — the WHOLE formatted dump ("1L Milk (asset,
            # id=..., price=$2.5)"), not the entity name. That was masked
            # by api/routes/actors.py's _grouped_beliefs() re-parsing the
            # value to derive a clean subject — which broke for OTHER,
            # real belief sources (BeliefFusion) whose subject/value
            # genuinely differ. The real fix is here, not there: extract
            # the clean name once, at the one place that actually knows
            # this content's real format (_explore_knowledge's two
            # formats, context_engine.py), so every reader can just trust
            # record["subject"] directly.
            subject = content.split(" (", 1)[0].split(" <-> ", 1)[0][:80] or content[:80]
            TimelineStore().record(
                TimelineKind.BELIEF, actor_id=self.id,
                subject=subject, predicate="known_fact", value=content,
                confidence=getattr(item, "confidence", 1.0), source=source,
                metadata={
                    "evidence": [source], "evidence_count": 1, "previous_value": None,
                    "reason": "retrieved from knowledge graph for this goal",
                    "execution_id": execution_id,
                },
            )
            _obs.counter("cognitive.beliefs_created")

        plan = state.plan
        exec_result = state.execution_result
        # Cognitive Loop Verification (round 2): this used to be
        # "completed" if formation_result.success else "failed" —
        # formation_result.success means "the tick pipeline ran without
        # raising," not "the goal was achieved," so a plan where 5/6
        # steps succeeded and one honestly failed was recorded as
        # "completed" — contradicting ExecutionRecord.outcome (below,
        # _record_execution), which already computes the real three-way
        # success/failure/partial from exec_result. Same real signal,
        # same three-way logic, so Domain Plan and Episodic Memory
        # (derived from this record) stop disagreeing with Execution
        # History about the same tick.
        if exec_result is None:
            plan_status = "completed" if formation_result.success else "failed"
        elif exec_result.failure_count == 0:
            plan_status = "completed"
        elif exec_result.success_count == 0:
            plan_status = "failed"
        else:
            plan_status = "partial"
        # Plan hysteresis: state.plan is whichever plan actually executed
        # this tick — the freshly generated one ("replace") or the real
        # Current Plan swapped in by the Decide stage ("keep"). Reusing
        # decide_new_plan_id/decide_current_plan_id as this record's
        # plan_id (instead of a fresh uuid every tick) lets the debugger
        # group every real execution of the same Current Plan together.
        correlated_plan_id = (
            state.metrics.get("decide_new_plan_id") or state.metrics.get("decide_current_plan_id")
        )
        # Comparator-hardening pass left state.comparison_result
        # (kernel/pipeline/comparison/integration.py::_run_comparison's
        # real, unmocked ComparisonResult.to_dict()) reachable only for
        # the remainder of THIS tick's in-memory state -- nothing
        # persisted it, so no reader outside the process could ever learn
        # what the Comparator concluded for a specific, already-completed
        # execution_id. Persisting just its outcome/loss summary here
        # (the PLAN record already written every tick, already tagged
        # with this same execution_id) closes that gap via the existing
        # GET /actors/{id}/plans surface instead of adding a new route.
        comparison = state.comparison_result if isinstance(state.comparison_result, dict) else None
        comparator_metadata = (
            {
                "comparator_outcome": comparison.get("outcome"),
                "comparator_actor_loss": comparison.get("actor_loss"),
                "comparator_world_loss": comparison.get("world_loss"),
                "comparator_policy_loss": comparison.get("policy_loss"),
            }
            if comparison else {}
        )
        TimelineStore().record(
            TimelineKind.PLAN, actor_id=self.id,
            **({"plan_id": correlated_plan_id} if correlated_plan_id else {}),
            goal=plan.goal if plan else "",
            steps=tuple(s.action for s in (plan.steps if plan else ())),
            step_descriptions=tuple(s.description for s in (plan.steps if plan else ())),
            node_count=len(plan.steps) if plan else 0,
            completed_nodes=exec_result.success_count if exec_result else 0,
            cost=plan.cost if plan else 0.0,
            confidence=plan.confidence if plan else 0.0,
            risk=plan.risk if plan else 0.0,
            status=plan_status,
            result=str(state.outcome) if state.outcome else "",
            metadata={"execution_id": execution_id, **comparator_metadata},
        )
        _obs.counter("cognitive.plans_generated")

        # Plan hysteresis: when this tick replaced the Current Plan, the
        # OLD one is genuinely retired — write a terminal "superseded"
        # record for it (fresh entry_id, reusing its own plan_id) rather
        # than mutating the "generated" record it already got when it was
        # created; Timeline entries are append-only (see GoalRecord's own
        # active->completed precedent — always two records, never one
        # mutated in place).
        replaced_snapshot = state.metrics.get("decide_replaced_plan_snapshot")
        if state.metrics.get("decide_action") == "replace" and replaced_snapshot:
            TimelineStore().record(
                TimelineKind.PLAN, actor_id=self.id,
                plan_id=replaced_snapshot.get("plan_id", ""),
                goal=replaced_snapshot.get("goal", ""),
                steps=tuple(replaced_snapshot.get("steps") or ()),
                step_descriptions=tuple(replaced_snapshot.get("step_descriptions") or ()),
                node_count=len(replaced_snapshot.get("steps") or ()), completed_nodes=0,
                cost=float(replaced_snapshot.get("cost", 0.0) or 0.0),
                risk=float(replaced_snapshot.get("risk", 0.0) or 0.0),
                confidence=float(replaced_snapshot.get("confidence", 0.0) or 0.0),
                status="superseded",
                result=f"Superseded by plan {state.metrics.get('decide_new_plan_id', '')}",
                metadata={"execution_id": execution_id},
            )

        # Cognitive State Quality Pass: DecisionRecord was previously
        # only written from the negotiation-trace path (integration.py::
        # execute_actor_request), so any plan that never touched a
        # negotiation action (e.g. a real commerce plan — ProductSelection
        # -> OrderCreation -> ...) left Decision/Candidate Futures empty,
        # even though the Predict stage computes a real prediction EVERY
        # tick (state.prediction_result — confidence/expected_utility/
        # recommendation/rationale per real candidate scenario, already
        # serialized to a plain dict by kernel/pipeline/prediction/
        # integration.py::prediction_result_to_dict). This persists that
        # already-computed prediction instead of discarding it; the
        # negotiation-trace DecisionRecord (when it fires) is a separate,
        # additional write, not replaced by this one.
        prediction = state.prediction_result or {}
        candidates_raw = prediction.get("candidates") or []
        if candidates_raw:
            selected = prediction.get("selected") or {}
            selected_prediction = selected.get("prediction") or {}
            confidence_info = selected_prediction.get("confidence") or {}
            evidence = tuple(
                str(outcome.get("description", ""))
                for outcome in (selected_prediction.get("predicted_outcomes") or [])
                if outcome.get("description")
            )
            # Cognitive Loop Verification (round 2): _execute_plan
            # (belief_runtime.py) may have executed anyway despite this
            # rejection — only when the rejection was built entirely from
            # "no knowledge yet," not real negative evidence (see its own
            # docstring). Reflecting that override here keeps this
            # Decision honest about what actually happened, instead of
            # still reading "do not execute" next to an Execution History
            # that did.
            override_reason = state.metrics.get("decision_override_reason")
            reason = str(prediction.get("rationale") or "")
            if override_reason:
                reason = f"{reason} (overridden: {override_reason})" if reason else f"Overridden: {override_reason}"
            TimelineStore().record(
                TimelineKind.DECISION, actor_id=self.id,
                selected_strategy=str(prediction.get("recommendation") or selected.get("scenario_label") or ""),
                reason=reason,
                confidence=float(confidence_info.get("point_estimate", 0.0) or 0.0),
                utility=float(selected_prediction.get("expected_utility", 0.0) or 0.0),
                evidence=evidence,
                candidates=tuple(
                    {
                        "name": candidate.get("scenario_label", ""),
                        "probability": candidate.get("probability", 0.0),
                        "utility": (candidate.get("prediction") or {}).get("expected_utility", 0.0),
                    }
                    for candidate in candidates_raw
                ),
                metadata={
                    "decision_kind": "scenario_recommendation", "execution_id": execution_id,
                    # Prediction subsystem hardening: reuses the same
                    # prediction_id/scenario_participation the Predict
                    # stage already computed (kernel/pipeline/prediction/
                    # scenarios.py) rather than a new persistence path --
                    # this is the one place that record is already
                    # written durably every tick.
                    "prediction_id": prediction.get("prediction_id", ""),
                    "scenario_participation": (prediction.get("metadata") or {}).get("scenario_participation"),
                },
            )
            _obs.counter("cognitive.decisions_made")
            _obs.gauge("cognitive.candidate_futures_evaluated", float(len(candidates_raw)))

        # Plan hysteresis: a second, always-on DecisionRecord "flavor" —
        # unlike the scenario-recommendation one above (only fires when
        # Predict produced real candidates), this fires every tick that
        # ran the Decide stage, tagged metadata.decision_kind so a reader
        # (and the frontend) can tell the two apart. This is the primary
        # explainability artifact plan hysteresis exists to produce: which
        # plan actually ran this tick, and why.
        decide_action = state.metrics.get("decide_action")
        if decide_action:
            from src.monkey_brain.kernel.pipeline.planning.plan_hysteresis import hysteresis_margin
            TimelineStore().record(
                TimelineKind.DECISION, actor_id=self.id,
                selected_strategy="Replace Plan" if decide_action == "replace" else "Keep Existing Plan",
                reason=str(state.metrics.get("decide_reason") or ""),
                utility=float(state.metrics.get("decide_new_score") or 0.0),
                evidence=(
                    f"new_plan_score={state.metrics.get('decide_new_score')}",
                    f"current_plan_score={state.metrics.get('decide_current_score')}",
                    f"hysteresis_margin={hysteresis_margin():.0%}",
                ),
                candidates=(
                    {
                        "name": "current_plan", "plan_id": state.metrics.get("decide_current_plan_id"),
                        "utility": state.metrics.get("decide_current_score"),
                    },
                    {
                        "name": "new_plan", "plan_id": state.metrics.get("decide_new_plan_id"),
                        "utility": state.metrics.get("decide_new_score"),
                        **(state.metrics.get("decide_score_components") or {}),
                    },
                ),
                metadata={"decision_kind": "plan_hysteresis", "action": decide_action, "execution_id": execution_id},
            )
            _obs.counter(f"cognitive.plan_decide_{decide_action}")

    async def shutdown(self) -> None:
        """Stop cognitive loop gracefully."""
        self._shutdown = True

    # ── Introspection ───────────────────────────────────────────────────────

    def belief_state(self) -> ActorBeliefSnapshot:
        """Snapshot of current belief state."""
        return self._actor_belief.snapshot()

    def summary(self) -> dict:
        """Human-readable summary of actor state."""
        return {
            "id": self.id,
            "type": self.entity_type.value,
            "belief_nnz": self.belief.nnz(),
            "bellman_entries": len(self._actor_belief._bellman),
            "observations": self._actor_belief._observation_count,
            "memory_size": len(self._actor_belief._memory),
            "trust_entries": len(self._actor_belief._trust),
            "phi_compiled": self._actor_belief._phi is not None,
            "peers": len(self.peers),
            "tick_count": self._cognitive_state.tick_count,
        }


@dataclass(frozen=True)
class _CognitiveTickResult:
    """Internal result of one async cognitive tick."""
    tick: int
    observations: dict
    belief_updated: bool
    plan: dict
    actions: list
    predicted_outcome: dict
    actual_outcome: dict
    error: float
    learned: bool
    execution_scope: dict = field(default_factory=dict)
    """Society-Scoped Interactive Execution metrics (spaces/societies/
    actors coordinated for the INITIATING actor's own request) — set via
    dataclasses.replace() in PlanetaryRuntime.execute_actor_request(),
    empty for every other caller of formation.from_state(). Defaulted
    (not required) so existing construction sites don't need updating."""
    coordination_trace: tuple = ()
    """True Multi-Actor Coordination: the ordered list of
    {event, society_id, actors_ticked} propagation steps this request's
    world mutations triggered in OTHER societies — also set via
    dataclasses.replace() in execute_actor_request(), empty by default
    for the same reason as execution_scope above."""
    execution_id: str = ""
    """Planetary Narrative: one real id shared by every Timeline record
    (Intent/Belief/Plan/Decision/Execution, and the negotiation Decision
    when one fires) this single tick produced — generated once in
    _cognitive_tick(), stashed on state.metrics so every stage/record
    writer downstream can tag its own record with it. Lets
    /actors/{id}/executions/{execution_id}/... compose everything one
    tick produced instead of querying each Timeline kind separately."""
    stage_timings_ms: dict = field(default_factory=dict)
    """Performance analysis instrumentation only (measurement, not a
    behavior change) — Runtime Performance Audit: merges state.
    stage_durations (the coarse observe/believe/plan/predict/decide/
    execute/observe_outcome/compare/learn/compile_phi/commit buckets
    run_stages() already times) with state.metrics["stage_timings_ms"]
    (the finer grounding_ms/prompt_build_ms/llm_call_ms/llm_call_count/
    response_parse_ms/planning_total_ms/perturbation_publish_ms entries
    _generate_plan/_execute_plan and llm_planner.py write) — same
    "stash on state.metrics, pull into the result at construction time"
    plumbing this class already uses for execution_id, above."""

    @property
    def outcome(self) -> dict:
        """Alias for actual_outcome — satisfies TickResultProtocol
        (src/shared/api_protocols.py), which names this field `outcome`."""
        return self.actual_outcome
