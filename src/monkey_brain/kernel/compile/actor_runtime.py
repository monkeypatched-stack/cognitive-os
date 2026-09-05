"""ActorRuntime — OS kernel orchestrator for an individual actor.

Each actor is an OS with three injected kernel runtimes:
  1. CognitiveRuntime — reasoning engine (planning, execution, learning)
  2. BeliefRuntime — belief formation kernel (Layer 2: fuse observations)
  3. TrustRuntime — trust fabric kernel (weighting, scoring)

ActorRuntime orchestrates these kernels and coordinates Layer 1→2→3:

Layer 1 (Knowledge Exchange):
    O = verify(signature, identity, policy, threshold)

Layer 2 (Belief Formation):
    Belief_a = g(W_global, O_a, C_a) via belief_runtime.fuse_observations()

Layer 3 (Decision):
    Action_a = π(Belief_a) via cognitive_runtime

Dependency INVERTED: all three kernels are injected INTO the actor.
The actor OWNS its reasoning, trust fabric, and belief formation.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.actor import ActorModel
from src.monkey_brain.kernel.compile.context import Context
from src.monkey_brain.kernel.compile.society import Actor, CycleResult
from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor
from src.monkey_brain.kernel.compile.belief_runtime import BeliefRuntime
from src.monkey_brain.kernel.compile.trust_runtime import TrustRuntime
# PerActorCognitiveRuntime removed - use society.py Actor directly

logger = logging.getLogger("agentos.compile.actor_runtime")


class AuthorizationView:
    """Actor-scoped authorization boundary.

    The view owns the decision boundary exposed to an actor. It deliberately
    does not expose a global policy or registry object. A Kernel-provided
    decision callable can be injected; the compatibility default preserves the
    existing behavior for standalone ActorRuntime callers.
    """

    def __init__(self, decision: Any = None) -> None:
        self._decision = decision

    def authorize(self, action: str, resource: Any = None, context: Any = None) -> bool:
        if self._decision is None:
            return True
        return bool(self._decision(action, resource, context))


class _AuthorizedRegistryView:
    """Read-only, actor-scoped view over a Kernel registry facade."""

    def __init__(self, registry: Any = None, authorization: AuthorizationView | None = None) -> None:
        self._registry = registry
        self._authorization = authorization or AuthorizationView()

    def discover(self, name: str, *, context: Any = None) -> Any | None:
        if not self._authorization.authorize("discover", name, context):
            return None
        if self._registry is None:
            return None
        discover = getattr(self._registry, "discover", None)
        return discover(name) if callable(discover) else None


class AuthorizedCapabilityView(_AuthorizedRegistryView):
    """Actor-scoped capability discovery view."""


class AuthorizedAgentView(_AuthorizedRegistryView):
    """Actor-scoped agent discovery view."""


class ActorRuntime:
    """OS kernel orchestrator for one actor.

    Each actor has an independent OS with three injected kernel runtimes:
      - cognitive_runtime: reasoning engine (planning, execution, learning)
      - belief_runtime: belief formation kernel (Layer 2: fuse observations)
      - trust_runtime: trust fabric kernel (weighting, scoring)

    The actor is injected INTO a society (society owns the actor).
    Dependency inversion: all runtimes are INJECTED INTO the actor.
    The actor OWNS its reasoning, belief, and trust.
    """

    def __init__(self, actor_id: str, *,
                 cognitive_runtime: Any = None,
                 belief_runtime: BeliefRuntime | None = None,
                 trust_runtime: TrustRuntime | None = None,
                 context: Context | None = None,
                 local_belief: SparseTransitionTensor | None = None,
                 world_view: Any = None,
                 existing_actor: Any = None,
                 execution_context: Any = None,
                 authorization_view: AuthorizationView | None = None,
                 capability_registry: Any = None,
                 agent_registry: Any = None,
                 goal_queue: Any = None,
                 resources: dict[str, Any] | None = None,
                 identity: Any = None,
                 memory: Any = None) -> None:
        
        assert isinstance(actor_id, str) and actor_id, "actor_id must be non-empty string"
        self.actor_id = actor_id
        self.context = context or Context(tenant_id=actor_id)
        self.execution_context = execution_context
        self.identity = identity if identity is not None else actor_id
        self.authorization = authorization_view or AuthorizationView()
        self.capabilities = AuthorizedCapabilityView(capability_registry, self.authorization)
        self.agents = AuthorizedAgentView(agent_registry, self.authorization)
        self.goal_queue = goal_queue if goal_queue is not None else deque()
        self.resources = resources if resources is not None else {}
        self._context_events: deque[Any] = deque(maxlen=1000)

        # Three injected kernel runtimes (each actor has its own instance)
        # Create defaults if not provided
        if trust_runtime is None:
            trust_runtime = TrustRuntime(actor_id=actor_id)

        # Shared belief tensor — both BeliefRuntime and Actor must use the same instance
        shared_belief = local_belief or SparseTransitionTensor()

        if belief_runtime is None:
            belief_runtime = BeliefRuntime(
                shared_belief,
                self.context,
                trust_runtime=trust_runtime
            )
        # Local state
        self.belief = shared_belief
        # Memory is actor-owned state. The injected belief is the compatibility
        # default because it is already the canonical local memory model.
        self.memory = memory if memory is not None else self.belief
        self._world_view = world_view               # read-only view of the global world

        # cognitive_runtime is now handled by society.py Actor directly
        # The Actor class IS the cognitive runtime - it owns plan → simulate → execute → learn
        self._actor = existing_actor if existing_actor is not None else Actor(actor_id)
        self._actor.world = self.belief
        self._actor.actions = ActorModel(actor_id, self.belief, runtime=self)

        # CognitiveOS is an implementation detail of ActorRuntime.  The
        # society layer must never construct or retain it directly.  Reuse an
        # already-bound OS for compatibility with callers that supplied an
        # actor, otherwise create the private implementation here.
        self._cognitive_os = getattr(self._actor, "os", None)
        if self._cognitive_os is None:
            from src.monkey_brain.kernel.cognitive_os import CognitiveOS

            self._cognitive_os = CognitiveOS(world=world_view)
            self._cognitive_os.set_actor(self._actor)

        # cognitive attribute points to the injected cognitive runtime (dependency inverted)
        self.cognitive = cognitive_runtime if cognitive_runtime is not None else self._actor
        self.belief_system = belief_runtime         # BeliefRuntime (Layer 2: fusion)
        self.trust_fabric = trust_runtime           # TrustRuntime (trust weighting)

    @property
    def actor(self) -> Any:
        """Compatibility projection; cognition remains owned by this runtime."""
        return self._actor

    @property
    def cognitive_os(self) -> Any:
        """This actor's own CognitiveOS instance (Per-Actor CognitiveOS
        Isolation refactor) — the explicit, public actor-OS boundary.
        Previously only reachable as the private _cognitive_os; exposed
        here so external code (and the isolation test suite) has a real,
        supported way to reach os.kernel/os.runtime/os.graph_manager/etc.
        without reaching into a private attribute."""
        return self._cognitive_os

    async def tick(self, prompt_request: Any = None) -> Any:
        """Run the complete hidden cognitive cycle for this actor.

        Actor Runtime review, Phase 1 (P0): autonomous/scheduler-triggered
        ticks (PlanetaryRuntime._auto_tick_loop -> GeographicEntityRuntime.
        tick -> SocietyRuntime.tick_one_actor -> here) have no incoming
        HTTP request or NATS message to bind an identity from. Without
        this, every governed capability such a tick reaches would be
        denied by ensure_governed's AUTH stage in any deployment with
        COGNITIVEOS_ALLOW_INSECURE_DEV_MODE correctly off — making
        genuinely autonomous (non-request-triggered) action impossible in
        production, not merely degraded.

        A request-triggered tick (api/dependencies.py, the /execute proxy
        in actor_runtime.py, subscribe_actor_inbox) has ALREADY bound a
        real caller/workload identity before reaching here -- never
        overwrite that; only fall back to a per-actor service identity
        (the same evidence_for_service(f"actor-runtime:{actor_id}")
        pattern subscribe_actor_inbox already uses for the exact same
        "no better identity available" case) when nothing better is
        already bound.

        asyncio.gather()'d occupant ticks (kernel/geography/runtime.py)
        each get their own copy of trusted_auth's ContextVar at
        task-creation time, so binding here -- inside each occupant's own
        tick() call/task -- keeps concurrent actors' identities isolated
        from one another. Do not move this binding to a scope ABOVE the
        gather() call, which would leak one identity across every
        concurrently-ticked actor in that cycle.
        """
        from src.monkey_brain.kernel.trusted_auth import (
            bind_trusted_auth, evidence_for_service, get_trusted_auth,
        )
        if not get_trusted_auth().authenticated:
            bind_trusted_auth(evidence_for_service(f"actor-runtime:{self.actor_id}"))
        return await self._cognitive_os.tick(prompt_request)

    def set_society_runtime(self, runtime: Any) -> None:
        """Inject society services into the private cognitive implementation."""
        setter = getattr(self._cognitive_os, "set_society_runtime", None)
        if callable(setter):
            setter(runtime)

    def set_objective(self, objective: str) -> None:
        """Update actor objective without exposing the implementation actor."""
        if hasattr(self._actor, "_objective"):
            self._actor._objective = objective

    def set_goal(self, goal: str) -> None:
        """Update the actor's live goal without exposing the implementation
        actor — mirrors set_objective() above. CognitiveActor._current_goal
        (what the next cognitive tick actually reads) is only ever set at
        registration time otherwise; without this, PATCH /actors/{id}
        writing profile.goals has no effect on what the actor plans next."""
        if hasattr(self._actor, "set_goal"):
            self._actor.set_goal(goal)

    def add_goal(self, goal: str, priority: float = 0.0) -> None:
        """Queue a new goal alongside whatever this actor is already
        pursuing, without exposing the implementation actor — mirrors
        set_goal() above, but delegates to CognitiveActor.add_goal()'s
        real priority queue (dedup by goal text, re-selects the current
        goal immediately) instead of replacing it outright."""
        if hasattr(self._actor, "add_goal"):
            self._actor.add_goal(goal, priority=priority)

    def remove_goal(self, goal: str) -> None:
        """Remove a goal from the queue by exact text, without exposing
        the implementation actor — reuses CognitiveActor._complete_goal()
        (the same real removal the tick pipeline already calls when a
        goal succeeds; here it's just called from a different, real
        reason: the operator explicitly replacing a queued goal's text
        via the Assistant's Update Goal action, not completion)."""
        if hasattr(self._actor, "_complete_goal"):
            self._actor._complete_goal(goal)

    def memory_snapshot(self, limit: int = 100) -> list[Any]:
        """Return a bounded, read-only-style memory projection."""
        memory = getattr(getattr(self._actor, "_actor_belief", None), "_memory", ())
        return list(memory[-limit:])

    @property
    def affiliations(self) -> Any:
        """Actor-scoped affiliation façade used by relationship APIs."""
        return getattr(self._actor, "affiliations", None)

    # ── observed runtime state ───────────────────────────────────────────────────

    def observe(self, context: Context) -> None:
        """Update the actor's view of the current runtime state (constraints, tenant)."""
        self.context = context

    def receive_context_event(self, event: Any) -> None:
        """Receive one event from the owning SocietyContextStream.

        Context delivery is intentionally passive: the actor receives the
        event, while its next scheduled cognitive tick decides how to use it.
        This keeps stream subscribers from executing cognition recursively
        inside ``ContextStream.publish()``.
        """
        self._context_events.append(event)

    def pending_context_events(self, limit: int = 100) -> list[Any]:
        """Return the most recent context events delivered to this actor."""
        if limit <= 0:
            return []
        return list(self._context_events)[-limit:]

    def record_action(self, action: str, state: str, *, domain: str = "default",
                      weight: float = 1.0) -> None:
        """Record an action/state pair in real-time. Updates belief, trust, and emits telemetry."""
        self.belief.observe(action, state, domain=domain, weight=weight)
        _obs.counter("runtime.action_recorded", actor=self.actor_id)
        _obs.event("runtime.record_action", actor=self.actor_id,
                   action=action, state=state, domain=domain, weight=weight)

    def get_world_state(self) -> dict:
        """Return the current world state and internal context."""
        return {
            "belief_transitions": self.belief.nnz(),
            "belief_domains": self.belief.domains(),
            "context_tenant": getattr(self.context, "tenant_id", ""),
            "actor_id": self.actor_id,
        }

    def policy_size(self) -> int:
        """P3: Safe accessor for policy size (fixes deep reference trap).

        Avoids fragile deep reference chain: _owner._actor_runtime._actor.policy.size
        """
        if self._actor and hasattr(self._actor, 'policy'):
            try:
                return self._actor.policy.size
            except AttributeError:
                return 0
        return 0

    # ── LAYER 1 → LAYER 2 HANDOFF ─────────────────────────────────────────────────
    # Delegates to belief_system (BeliefRuntime)

    def accept_proposal(self, proposal: Any) -> None:
        """LAYER 1: Knowledge Exchange → LAYER 2: Belief Formation handoff.

        Delegates to belief_system (BeliefRuntime).
        """
        self.belief_system.accept_proposal(proposal)

    # ── LAYER 2: BELIEF FORMATION ─────────────────────────────────────────────────
    # Delegates to belief_system (BeliefRuntime)

    def fuse_observations(self) -> dict:
        """LAYER 2: Belief Formation — g(W_global, O_a, C_a)

        Delegates to belief_system (BeliefRuntime).
        """
        return self.belief_system.fuse_observations(self._world_view)

    # ── LAYER 3: DECISION ─────────────────────────────────────────────────────────
    # Action_a = π(Belief_a) — policy operates on fused belief

    def act(self, request: Any, actor: Any = None, *, reward: float = 1.0,
            society_runtime: Any = None) -> Any:
        """Record an action into local belief.

        ``actor``/``society_runtime`` are accepted for signature compatibility with
        callers (e.g. ActorSystem.run()) but no longer select an alternate pipeline —
        the 15-layer pipeline this used to route into has been removed.

        society_runtime is passed BY the society (the caller), not stored on the actor.
        The actor belongs to a society — society is not a dependency of the actor.
        """
        return self._act_standalone(request, reward)

    def _act_standalone(self, request: Any, reward: float = 1.0) -> dict:
        """Lightweight action recording — no pipeline, no actor system needed."""
        action = str(request)
        if self._world_view is not None:
            view_states = {s for s, _ in self._world_view}
            if action not in view_states:
                _obs.event("actor.act", actor=self.actor_id, action=action, skipped=True)
                return {"learned": [], "belief_nnz": self.belief.nnz()}
        self.belief.observe(action, action, domain="default", weight=reward)
        _obs.event("actor.act", actor=self.actor_id, action=action)
        return {"learned": [action], "belief_nnz": self.belief.nnz()}

    def cognitive_cycle(self, start: str, goal: str, *, reward: float = 1.0) -> CycleResult:
        """LAYER 3: Complete cognitive cycle on fused belief.

        Plan → Simulate → Execute → Learn

        All operations run on the actor's LOCAL belief. Belief is fused (with trust
        weighting) before planning, ensuring the actor reasons from freshest available
        information.

        Actor Runtime review, Phase 5: this is the SYNCHRONOUS, non-LLM,
        local-graph-pathfinding cycle (see CognitiveActor's own "Phase 5"
        docstring block, kernel/compile/cognitive_actor.py) -- NOT the
        async LLM-driven engine every real /prompt request uses
        (ActorRuntime.tick() -> CognitiveActor._cognitive_tick()). Its
        "Execute" step never reaches ensure_governed; it advances a local
        simulation, not a real capability.
        """
        import math
        assert isinstance(start, str) and start, "start must be non-empty string"
        assert isinstance(goal, str) and goal, "goal must be non-empty string"
        assert isinstance(reward, (int, float)) and math.isfinite(reward), "reward must be finite numeric"
        assert self._world_view is not None, "world_view must be set before calling cognitive_cycle()"

        fusion_result = self.fuse_observations()
        assert isinstance(fusion_result, dict), "fusion_result must be dict"

        try:
            result = self._actor.cognitive_cycle(start, goal, self._world_view, reward=reward)
        except (ValueError, TypeError, RuntimeError, AssertionError) as e:
            logger.error("[actor_runtime] %s cognitive cycle failed: %s", self.actor_id, e, exc_info=True)
            raise

        logger.info(
            "[actor_runtime] %s cognitive cycle: %s→%s (fused %d obs, loss=%.4f, reached=%s)",
            self.actor_id, start, goal, fusion_result["fused_count"],
            result.epistemic_loss, result.reached_goal
        )

        _obs.counter("actor.cognitive_cycle", actor=self.actor_id)
        _obs.gauge("actor.epistemic_loss", result.epistemic_loss, actor=self.actor_id)
        _obs.event("cognitive.cycle", actor=self.actor_id, start=start, goal=goal,
                   reached=result.reached_goal, loss=round(result.epistemic_loss, 4),
                   fused_observations=fusion_result["fused_count"])
        return result

    # ── knowledge exchange (import / revoke, provenance-tracked) ─────────────────

    def import_shared(self, tag: str = "enterprise") -> list[tuple[str, str]]:
        """Materialize shared/enterprise transitions from the world view into the local
        belief, tagged for later revoke. Personal transitions already in the belief are
        left untouched (only genuinely-new edges are recorded under the tag)."""
        if not hasattr(self, "_imported"):
            self._imported: dict[str, dict[tuple[str, str], float]] = {}
        added: list[tuple[str, str]] = []
        for (src, dst) in self._world_view:
            if self.belief.has_edge(src, dst):
                continue                                  # keep the actor's own copy
            self.belief.observe(src, dst,
                                domain=self._world_view.domain_of(src),
                                dst_domain=self._world_view.domain_of(dst))
            # Remember the observation count this edge had the moment it was imported.
            # revoke() compares against it to tell "purely borrowed" from "the actor has
            # since made this its own" — see revoke().
            added.append((src, dst))
        bucket = self._imported.setdefault(tag, {})
        for edge in added:
            bucket[edge] = self.belief.feature(edge[0], edge[1], Feature.FREQUENCY)
        logger.info("[actor_runtime] %s imported %d %r transition(s)", self.actor_id, len(added), tag)
        _obs.event("knowledge.import", actor=self.actor_id, tag=tag, count=len(added))
        return added

    def revoke(self, tag: str = "enterprise") -> int:
        """Remove transitions imported under `tag` (e.g. on leaving a company) while
        keeping the actor's personal knowledge intact.

        An imported edge the actor has since USED — observed again through its own
        execution — is no longer purely the employer's: the actor learned it too. This
        used to remove() every tagged edge unconditionally, so leaving a company deleted
        the actor's own accumulated observations and Q-values on any edge that happened
        to overlap with the employer's. For a lifelong personal runtime that is the one
        thing revoke must never do. An edge whose frequency has grown since import has
        been personally reinforced and is retained.
        """
        imported: dict[tuple[str, str], float] = getattr(self, "_imported", {}).get(tag, {})
        removed = 0
        retained = 0
        for (s, d), freq_at_import in imported.items():
            if self.belief.feature(s, d, Feature.FREQUENCY) > freq_at_import:
                retained += 1                       # the actor made this its own — keep it
                continue
            if self.belief.remove(s, d):
                removed += 1
        self._imported[tag] = {}
        logger.info("[actor_runtime] %s revoked %d %r transition(s), retained %d personally "
                    "reinforced; personal knowledge intact",
                    self.actor_id, removed, tag, retained)
        _obs.event("knowledge.revoke", actor=self.actor_id, tag=tag,
                   removed=removed, retained=retained)
        return removed

    # ── checkpoint / restore (runtime lifecycle) ─────────────────────────────────

    def checkpoint(self, base_path: str) -> None:
        """Persist this runtime's own state for actor recovery: the local
        SparseTransitionTensor, cognitive/trust runtime state, and
        BeliefRuntime's fusion bookkeeping.

        Step 14 — Architecture Consolidation: NOT the belief-restore path
        for /prompt. That path (PlanetaryRuntime.restore_actor_belief()/
        checkpoint_actor_belief(), kernel/society/integration.py) persists
        kernel/pipeline/belief_state.py::BeliefState instead — the
        representation CognitiveRuntime.tick() actually reads/writes.
        This tensor/BeliefRuntime bundle is vestigial on that live tick
        path (nothing in Observe/Believe/Plan/Execute/Learn reads it), but
        is kept as the legitimate persistence mechanism for whichever
        future caller still wants it (e.g. the Legacy /plan+/execute path).

        Checkpoints:
        1. Local belief (SparseTransitionTensor)
        2. Cognitive runtime state (planning, simulation, execution, learning histories)
        3. Trust runtime state (graph, decay, learning)
        4. Belief runtime state (observations, fusions)
        """
        import os
        try:
            os.makedirs(base_path, exist_ok=True)

            belief_path = os.path.join(base_path, "belief.pkl")
            self.belief.save(belief_path)
            logger.debug("[actor_runtime] %s checkpointed belief (%d transitions) to %s",
                        self.actor_id, self.belief.nnz(), belief_path)

            if hasattr(self.cognitive, 'checkpoint'):
                self.cognitive.checkpoint(os.path.join(base_path, "cognitive"))
                logger.debug("[actor_runtime] %s checkpointed cognitive runtime", self.actor_id)

            if hasattr(self.trust_fabric, 'checkpoint'):
                self.trust_fabric.checkpoint(os.path.join(base_path, "trust.json"))
                logger.debug("[actor_runtime] %s checkpointed trust runtime", self.actor_id)

            if hasattr(self.belief_system, 'checkpoint'):
                self.belief_system.checkpoint(os.path.join(base_path, "belief_runtime.json"))
                logger.debug("[actor_runtime] %s checkpointed belief runtime", self.actor_id)

            logger.info("[actor_runtime] %s checkpointed all state to %s", self.actor_id, base_path)
            _obs.event("runtime.checkpoint", actor=self.actor_id,
                       transitions=self.belief.nnz(), path=base_path)
        except (IOError, OSError) as e:
            logger.error("[actor_runtime] checkpoint failed: %s", e, exc_info=True)
            raise

    def restore(self, base_path: str) -> None:
        """Restore this runtime's own state from a checkpoint().

        Step 14 — Architecture Consolidation: NOT the belief-restore path
        for /prompt (see checkpoint()'s docstring above) — kept as the
        legacy, still-functional mechanism for the tensor/BeliefRuntime
        bundle, unreached from /prompt going forward.

        Restores:
        1. Local belief (SparseTransitionTensor)
        2. Cognitive runtime state (planning, simulation, execution, learning histories)
        3. Trust runtime state (graph, decay, learning)
        4. Belief runtime state (observations, fusions)
        """
        import os
        try:
            belief_path = os.path.join(base_path, "belief.pkl")
            self.belief.load(belief_path)
            self._actor.world = self.belief
            self._actor.actions = ActorModel(self.actor_id, self.belief, runtime=self)
            logger.debug("[actor_runtime] %s restored belief (%d transitions) from %s",
                        self.actor_id, self.belief.nnz(), belief_path)

            if hasattr(self.cognitive, 'restore'):
                self.cognitive.restore(os.path.join(base_path, "cognitive"))
                logger.debug("[actor_runtime] %s restored cognitive runtime", self.actor_id)

            if hasattr(self.trust_fabric, 'restore'):
                self.trust_fabric.restore(os.path.join(base_path, "trust.json"))
                logger.debug("[actor_runtime] %s restored trust runtime", self.actor_id)

            if hasattr(self.belief_system, 'restore'):
                self.belief_system.restore(os.path.join(base_path, "belief_runtime.json"))
                logger.debug("[actor_runtime] %s restored belief runtime", self.actor_id)

            self._validate_belief_world_compatibility()

            logger.info("[actor_runtime] %s restored all state from %s", self.actor_id, base_path)
            _obs.event("runtime.restore", actor=self.actor_id,
                       transitions=self.belief.nnz(), path=base_path)
        except (IOError, OSError) as e:
            logger.error("[actor_runtime] restore failed: %s", e, exc_info=True)
            raise

    def _validate_belief_world_compatibility(self) -> None:
        """Confirm the just-restored SparseTransitionTensor is still
        meaningful against the current world model before this legacy
        restore() hands it to downstream runtimes. A restored belief
        covering none of the current world is stale (world evolved since
        the checkpoint was taken) — logged, not fatal, since an actor's
        private knowledge is allowed to exceed or lag the shared world's
        (see belief_coverage()).

        Step 14 — Architecture Consolidation: scoped to this legacy
        tensor/BeliefRuntime restore() only; no equivalent check exists
        (or is required) for the canonical kernel/pipeline/belief_state.py::BeliefState
        restore path (PlanetaryRuntime.restore_actor_belief())."""
        world = self._world_view
        coverage_fn = getattr(self.belief_system, "belief_coverage", None)
        if world is None or not callable(coverage_fn):
            return
        try:
            coverage = coverage_fn(world)
        except Exception as e:
            logger.warning("[actor_runtime] %s belief/world compatibility check failed: %s",
                           self.actor_id, e)
            return
        logger.info("[actor_runtime] %s restored belief covers %.1f%% of current world",
                    self.actor_id, coverage * 100)

    # ── introspection ────────────────────────────────────────────────────────────

    def belief_size(self) -> int:
        return self.belief.nnz()

    def summary(self) -> dict[str, Any]:
        return {
            "actor": self.actor_id,
            "tenant": getattr(self.context, "tenant_id", ""),
            "belief_transitions": self.belief.nnz(),
            "cognitive": type(self.cognitive).__name__,
            "belief_runtime": self.belief_system.summary(),
            "trust_runtime": self.trust_fabric.summary(),
        }
