"""Planetary Runtime Integration (Step 12.12) — integrates all previous
steps into the complete end-state architecture.

Every actor executes independently:
    Observe → Believe → Plan → Execute → Learn → Compile Φ → Predict → Commit

The society continuously evolves through:
    Context Stream → Shared Semantic World → Actor Observations →
    Beliefs → Plans → Actions → Learning → Context Stream

SocietyRuntime coordinates this continuous cycle without centralizing
cognition.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, replace as _dc_replace
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Awaitable
from uuid import uuid4

from src.monkey_brain.kernel.society.actor_lifecycle import ActorDesiredState, ObservedActorState
from src.monkey_brain.kernel.society.actor_scheduler import ActorPlacementRequirements, ActorScheduler, ExecutionNode, NodeClass
from src.monkey_brain.kernel.society.redis_index_reconstruction import ConsistencyCheckResult, RedisReconstructionResult
from src.monkey_brain.kernel.timeline.presence import PresenceTimeline
from src.monkey_brain.kernel.society.domain import Society, ActorProfile, Team
from src.monkey_brain.kernel.society.world import SharedWorld, WorldEntity, WorldEvent, EventType
from src.monkey_brain.kernel.society.perturbation_queue import PerturbationQueue
from src.monkey_brain.kernel.society.cycle_performance import CyclePerformanceReport, ActorPerformanceReport
from src.monkey_brain.kernel.society.interaction import InteractionType, Interaction
from src.monkey_brain.kernel.society.coordination import CoordinationEngine
from src.monkey_brain.kernel.society.game_theory import GameTheoryRuntime
from src.monkey_brain.kernel.society.context_stream import SocietyContextStream, ContextEvent, ContextEventType
from src.monkey_brain.kernel.society.learning import (
    CollectiveLearningEngine, SharedExperience, CollectiveLearningResult,
)
from src.monkey_brain.kernel.society.governance import SocietyGovernanceEngine
from src.monkey_brain.kernel.society.observability import SocietyObservability, SocietyTrace
from src.monkey_brain.kernel.society.runtime import SocietyRuntime, ActorRuntimeState
from src.monkey_brain.kernel.society.communication import CommunicationDecision
from src.monkey_brain.kernel.society.federation import Federation, FederationManager
from src.monkey_brain.kernel.geography.entity import GeographicEntity, GeographicEntityType, Country, City
from src.monkey_brain.kernel.geography.registry import GeographicRegistry
from src.monkey_brain.kernel.geography.runtime import GeographicEntityRuntime, GeographicTickResult
from src.monkey_brain.kernel.compile.world_model_runtime import WorldModelRuntime
from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.timeline.store import TimelineStore
from src.monkey_brain.kernel.society.membership import SocietyMembershipRegistry, MembershipGovernor
from src.monkey_brain.kernel.society.movement_perturbation import MovementPerturbationEngine
from src.monkey_brain.kernel.society.activation import SocietyActivationEngine, SocietyActivationResult
from src.monkey_brain.kernel.society.commerce_network import CommerceNetwork
from src.monkey_brain.kernel.relationships import RelationshipGraph, RelationshipKind
from src.monkey_brain.kernel.society.actor_lifecycle_controller import (
            ActorLifecycleController,
        )

logger = logging.getLogger("agentos.planetary_runtime")

StageFn = Callable[[Any], Awaitable[Any]]


@dataclass(frozen=True)
class PlanetaryCycleResult:
    """Result of one complete planetary cycle."""
    cycle_id: str = field(default_factory=lambda: uuid4().hex)
    cycle_number: int = 0
    actors_observed: int = 0
    beliefs_updated: int = 0
    interactions_routed: int = 0
    context_events: int = 0
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    error: str | None = None


@dataclass(frozen=True)
class FederatedCycleResult:
    """Result of one federated cycle — every locally-registered member
    society of a Federation ticked once, in one coordinated pass."""
    cycle_id: str = field(default_factory=lambda: uuid4().hex)
    federation_id: str = ""
    societies_ticked: tuple[str, ...] = ()
    """society_ids that were actually ticked this cycle."""
    unregistered_society_ids: tuple[str, ...] = ()
    """Federation members not in this runtime's society registry — known
    about, but not locally runnable (e.g. they live on another node).
    Skipped, not an error."""
    actors_ticked_total: int = 0
    interactions_routed_total: int = 0
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CityTickResult:
    """Backward-compat shape for tick_city() — Physical Geography refactor
    superseded the real implementation with kernel/geography/runtime.py::
    GeographicTickResult (one generic result for all 8 tiers); this remains
    only as the return shape tick_city()'s compat wrapper builds from a
    GeographicTickResult, so existing callers of tick_city() see no change."""
    cycle_id: str = field(default_factory=lambda: uuid4().hex)
    city_id: str = ""
    societies_ticked: tuple[str, ...] = ()
    actors_ticked_total: int = 0
    interactions_routed_total: int = 0
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CountryTickResult:
    """Backward-compat shape for tick_country() — see CityTickResult."""
    cycle_id: str = field(default_factory=lambda: uuid4().hex)
    country_id: str = ""
    cities_ticked: tuple[str, ...] = ()
    societies_ticked_total: int = 0
    actors_ticked_total: int = 0
    interactions_routed_total: int = 0
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class GeographyReconciliationResult:
    """Result of PlanetaryRuntime.reconcile_default_geography() — what, if
    anything, got migrated off the synthetic bootstrap "Default Planet"
    chain and onto a real canonical root (e.g. "Earth")."""
    performed: bool
    reason: str = ""
    canonical_root_id: str | None = None
    target_city_id: str | None = None
    migrated_society_ids: tuple[str, ...] = ()
    created_entity_ids: tuple[str, ...] = ()
    deleted_entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActorRegistryEntry:
    """Actor Registry (Deployment Architecture, Section 7): a lightweight,
    durable record of one Actor's existence, lifecycle status, and owning
    node — readable via PlanetaryRuntime.locate_actor()/list_registry()
    with a single Redis read, no actor reconstruction required. This is
    the piece that was missing from the already-real _save_actor()/
    _load_actors() persistence: that mechanism could always rebuild an
    actor's full cognition from Redis, but nothing let a caller cheaply
    ask "does this actor exist, and where" without doing so."""
    actor_id: str
    actor_type: str
    name: str
    society_id: str
    status: str
    node_id: str
    updated_at: float
    artifact_version: str = ""
    """Actor Artifact model (docs/ACTOR_ARTIFACT.md): the operator-supplied
    version of whatever built the process currently reporting this
    record — e.g. "1.4". "" for any record written before this field
    existed, or by a process that never set ACTOR_ARTIFACT_VERSION —
    never required, purely observability. Distinct from actor_id: an
    artifact upgrade (this field changing) must never imply a different
    Actor."""
    runtime_version: str = ""
    """This module's own ACTOR_RUNTIME_VERSION — the Actor Runtime
    (actor_runtime.py) build that last wrote this record, independent of
    the artifact_version an operator assigns to their own deployment."""


@dataclass(frozen=True)
class _ActorTickOutcome:
    """Result of PlanetaryRuntime._run_actor_tick() — named fields instead
    of a 4-tuple, handed to _finalize_actor_execution()."""
    actor_execution_result: Any
    actors_coordinated: set[str]
    spaces_coordinated: set[str]
    context_events_before: int


class PropagationMode(str, Enum):
    """Execution mode for _finalize_actor_execution's post-tick
    coordination fan-out (_propagate_coordination).

    SYNCHRONOUS (the default): the originating actor's request blocks
    until every subscribed society has been propagated to, its execution
    traces collected, and the results aggregated — required for
    negotiation/transaction/consensus/approval-style operations where the
    caller's own outcome depends on the propagated result.

    ASYNCHRONOUS: the originating actor's request returns immediately
    with the propagation scheduled as a background task; results are
    delivered afterward via a Redis pub/sub completion event
    (monkeybrain.propagation.completed.{actor_id}) rather than the
    original execution thread. Appropriate for notification/telemetry/
    observation-sharing fan-out that the caller doesn't need to wait on.
    """
    SYNCHRONOUS = "SYNCHRONOUS"
    ASYNCHRONOUS = "ASYNCHRONOUS"


class PropagationScope(str, Enum):
    """Recipient-selection scope for _propagate_coordination — independent
    of PropagationMode (sync/async govern *when* the caller sees results;
    this governs *who* receives the propagated message).

    POINT_TO_POINT: delivers directly to exactly one target actor
    (``meta.propagation_target_actor_id``), gated by the same
    affiliation/trust/authorization policy resolve_communication() already
    enforces for direct actor-to-actor messaging. No multi-society
    cascade — a single hop to the named recipient.

    BROADCAST (the default): delivers to every actor in a society
    subscribed to the domain event(s) that fired — but only those
    resolve_communication() actually authorizes to hear from the
    originating actor (shared affiliation, or same-society membership),
    never an indiscriminate whole-society blast.
    """
    POINT_TO_POINT = "POINT_TO_POINT"
    BROADCAST = "BROADCAST"


_MESSAGE_INBOX_KEY_PREFIX = "monkeybrain:messages:"
_MESSAGE_INBOX_TTL_SECONDS = 86400
"""Bounded so a message addressed to an actor_id that's mistyped, or that
never becomes resident anywhere, doesn't grow its inbox key forever —
same TTL-bounded-without-explicit-eviction shape as RunStore's own
RUN_STORE_TTL_SECONDS default."""

_DRAIN_INBOX_SCRIPT = """
local msgs = redis.call('lrange', KEYS[1], 0, -1)
if #msgs > 0 then redis.call('del', KEYS[1]) end
return msgs
"""
"""Atomic read-and-clear: a plain LRANGE followed by a separate DEL from
Python would have a real race — a message RPUSHed by another process
between the two calls would be silently deleted, never delivered. Runs
server-side via EVAL so the two operations are atomic, the same reasoning
_RELEASE_LOCK_IF_OWNER_SCRIPT already documents for the actor lease."""

_ACTOR_LEASE_KEY_PREFIX = "monkeybrain:actor:lease:"
_ACTOR_FENCE_KEY_PREFIX = "monkeybrain:actor:fence:"
_ACTOR_LEASE_DEFAULT_TTL_SECONDS = 330.0
"""Covers SocietyRuntime.tick_one_actor()'s own 300s outer timeout plus
margin -- the lease must outlive the longest a single real tick is ever
allowed to run, or it could expire and let a second node acquire it
while the first is still legitimately mid-tick."""

_PLANETARY_CYCLE_LOCK_KEY = "monkeybrain:planetary:cycle:lock"
_RELEASE_LOCK_IF_OWNER_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""
"""Atomic compare-and-delete: only removes the lock if its value still
matches the token THIS acquisition set. Without this, a slow/orphaned
holder's delayed release (see _release_planetary_cycle_lock) could delete
a DIFFERENT replica's lock that legitimately acquired the key after the
first one's TTL expired -- the classic unsafe-release bug in naive
SET NX + DEL locking. Runs server-side via EVAL so the get-then-delete is
atomic; a plain GET-then-DEL from Python would have the exact same race
it's meant to close."""


class PlanetaryRuntime:
    """

    This is the world level runtime
        # the planetary runtime takes care of
            # 1. actor/society/geography resolution,
            # 2. recursive traversal,
            # 3. context/world updates,
            # 4. and actor coordination
        # the planetary runtime acts as a controller for actor scheduling and execution

    Continuously evolves through:
        Context Stream → Shared Semantic World → Actor Observations →
        Beliefs → Plans → Actions → Learning → Context Stream

    SocietyRuntime coordinates this cycle without centralizing cognition.

    Architecturally this is similar to Kubernetes and extends the kubernetees infra to help manage actors 
    where each actor is analogus to a pod with multiple runtimes for resiliency purposes
    
    NOTE: Ideally this should be in go . python is ok for now right now 

    """

    def __init__(self, society: Society | None = None, default_bootstrap_space_id: str | None = None) -> None:
        
        self._boot_time = time.time()

        self._peak_queue_depth = 0
        
        # Identify this runtime instance for actor ownership, node registration,
        # and distributed lease/lock coordination. Operators can provide a stable
        # node identity; otherwise each process receives a unique execution-node ID.
        self._node_id = os.getenv("COGNITIVEOS_NODE_ID") or f"node-{uuid4().hex[:12]}"

        # Version of the actor artifact being executed. This is used to identify
        # the actor's deployed/application artifact independently of the runtime.
        self._artifact_version = os.getenv("ACTOR_ARTIFACT_VERSION", "")

        # Version of the CognitiveOS actor runtime executing the actor. Keeping
        # this separate from the artifact version allows runtime upgrades to be
        # distinguished from changes to the actor itself.
        self._runtime_version = os.getenv("ACTOR_RUNTIME_VERSION", "")

        # ID of the space used when an actor does not explicitly specify a space
        # during bootstrap. This can be supplied programmatically or configured
        # globally through the environment.
        self._default_bootstrap_space_id: str | None = (
            default_bootstrap_space_id
            or os.getenv("PLANETARY_DEFAULT_BOOTSTRAP_SPACE_ID")
            or None
        )

        # Create the single shared semantic world for this Planetary runtime.
        # Actors and runtime services that need world state should reference this
        # instance rather than maintaining independent copies.
        self._world = SharedWorld()

        # Expose the same world through the WorldModel runtime facade. The facade
        # provides world-model operations while preserving a single underlying
        # SharedWorld instance.
        self._world_model = WorldModelRuntime(semantic_world=self._world)

        # Persistence is initialized during runtime startup. Keep the attribute
        # explicitly unset until the persistence layer has been constructed.
        self._persistence_manager = None

        # Local durable actor state store. This is the runtime's access point for
        # persisted actor belief/state and is populated during initialization.
        self._actor_state_store: Any = None

        # In-memory fallback for desired actor state when the persistent state store
        # is temporarily unavailable. This is not the authoritative durable state.
        self._desired_state_fallback: dict[str, dict[str, Any]] = {}

        # Controls actor lifecycle transitions such as startup, shutdown,
        # reconciliation, and state restoration.
        self._lifecycle_controller: Any = None

        # Scheduler responsible for runtime-managed scheduled work.
        self._scheduler: Any = None

        # Provisioner used when actor workloads need to be created or managed
        # on Kubernetes-backed infrastructure.
        self._kubernetes_provisioner: Any = None

        # Provisioner used to manage actor workloads on edge infrastructure.
        self._edge_provisioner: Any = None

        # In-memory fallback for node registration state when the persistent
        # node registry is unavailable. Durable node registration remains the
        # authoritative source when persistence is available.
        self._node_registry_fallback: dict[str, dict[str, Any]] = {}

        # In-memory fallback for desired node state. This allows reconciliation
        # logic to retain the requested node configuration when the durable
        # state store is temporarily unavailable.
        self._desired_node_fallback: dict[str, dict[str, Any]] = {}

        # In-memory fallback for placement requirements. These requirements
        # describe the infrastructure constraints used when deciding where
        # actor workloads should run.
        self._placement_requirements_fallback: dict[str, dict[str, Any]] = {}

        # NATS client used for asynchronous messaging, subscriptions, and
        # distributed runtime communication. The connection is established
        # during the explicit async startup lifecycle.
        self._nats_client: Any = None

        # Initialize the persistence subsystem. Durable actor, node, placement,
        # and reconciliation state should be recovered from persistence rather
        # than reconstructed solely from in-memory defaults.
        self._init_persistence()

        # Strategic negotiation runtime used by the society to evaluate and
        # maintain agreements between actors. Planetary agreements are part of
        # the strategic world state shared with the society runtime.
        self._game_theory = GameTheoryRuntime(
            world_state={"planetary_agreements": {}}
        )

        # Initialize the society runtime. The society owns coordination at the
        # social level while delegating strategic reasoning to the game-theory
        # runtime above.
        self._society_runtime = SocietyRuntime(
            society or Society(name="Planetary Society"),
            strategic_runtime=self._game_theory,
        )
        self._society_runtime._world = self._world

        # Establish the coordination engine from the society runtime. Keeping
        # this reference allows subsequent ticks to use the same coordination
        # state and strategic runtime rather than creating a new coordination
        # context for every cycle.
        self._coordination_engine = self._society_runtime.coordinate()

        # Observability for society-level operations, including coordination,
        # lifecycle activity, and runtime execution metrics.
        self._observability = SocietyObservability()

        # Serialize tick execution so concurrent callers cannot execute multiple
        # Planetary cycles against the same mutable runtime state simultaneously.
        self._tick_lock = asyncio.Lock()
        self._actor_lease_fences: dict[str, int] = {}

        # Token representing the distributed lease/lock acquired for the current
        # execution cycle. It is used to identify ownership of a cycle across
        # competing runtime instances.
        self._cycle_lock_token: str | None = None

        # Per-actor timing measurements collected during the current or most
        # recent execution cycle. Used for diagnostics and performance analysis.
        self._cycle_actor_timing_ms: dict[str, float] = {}

        # Most recent complete cycle report, containing the results and metadata
        # produced by the Planetary execution cycle.
        self._last_cycle_report: Any = None

        # Duration of the most recently completed Planetary tick in milliseconds.
        self._last_tick_duration_ms: float = 0.0

        # Timestamp at which the most recent Planetary tick completed.
        self._last_tick_timestamp: float = 0.0

        # Background task responsible for automatically triggering Planetary ticks
        # when auto-ticking is enabled.
        self._auto_tick_task: asyncio.Task | None = None

        # Interval between automatically triggered ticks. Five minutes is the
        # default cadence when no explicit interval is configured.
        self._auto_tick_interval: float = 300.0  # 5 minutes default

        # Background task that periodically reconciles desired actor/node state
        # with the actual runtime and infrastructure state.
        self._lifecycle_reconciliation_task: asyncio.Task | None = None

        # Interval between full lifecycle reconciliation passes.
        self._lifecycle_reconciliation_interval: float = 300.0

        # Background worker that drains the reconciliation queue and processes
        # pending lifecycle/state changes asynchronously.
        self._reconcile_queue_task: asyncio.Task | None = None

        # Polling interval used by the reconciliation queue worker when looking
        # for newly queued reconciliation work.
        self._reconcile_queue_interval: float = 2.0

        # Maximum number of reconciliation items processed in a single queue
        # batch, preventing an unexpectedly large queue from monopolizing the
        # runtime event loop.
        self._reconcile_queue_batch_size: int = 50

        # Limits concurrent reconciliation operations so infrastructure and
        # persistence systems are not overwhelmed by a large reconciliation batch.
        self._reconcile_queue_semaphore: asyncio.Semaphore | None = None

        # Actor ID whose lifecycle state is currently being reconciled when
        # reconciliation is scoped to a specific actor. None means reconciliation
        # is not currently restricted to one actor.
        self._reconciliation_scope_actor_id: str | None = None

        # Tracks asynchronous background propagation operations so they can be
        # observed, awaited, and cancelled during runtime shutdown.
        self._background_propagation_tasks: set[asyncio.Task] = set()

        # Tracks NATS subscription tasks so subscriptions can be explicitly
        # managed and cancelled during runtime shutdown.
        self._inbox_subscription_tasks: set[asyncio.Task] = set()

        # Manages federation between this runtime and other CognitiveOS/runtime
        # domains, including cross-node or cross-geography coordination.
        self._federation_manager = FederationManager()

        # Registry responsible for mapping actors/nodes to their geographic
        # presence and restoring geographic context required for correct
        # placement and actor rehydration.
        self._geo_registry = GeographicRegistry()

        from src.monkey_brain.kernel.learn.memory.manager import MemoryManager
        from src.monkey_brain.kernel.learn.memory.vector_backend import RedisBackedVectorBackend
        from src.monkey_brain.kernel.learn.memory.graph_adapter import KnowledgeGraphMemoryAdapter
        from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
        from src.monkey_brain.kernel.pipeline.planning.context_engine import ContextConstructionEngine

        # In-memory semantic knowledge graph for the actor/society runtime.
        # This is the structured representation of entities and relationships
        # accumulated by the runtime.
        self._knowledge_graph = KnowledgeGraph()

        # Register the callback used to propagate knowledge-graph mutations into
        # the rest of the runtime. Changes to the graph therefore flow through
        # the runtime's knowledge/state propagation path rather than remaining
        # isolated inside the graph.
        self._knowledge_graph.set_on_change(self._on_knowledge_graph_change)

        # Unified memory subsystem combining vector-based retrieval with the
        # structured knowledge graph. Redis provides the vector-backed memory
        # store, while the graph adapter exposes structured knowledge as part
        # of the same memory interface.
        self._memory_manager = MemoryManager(
            RedisBackedVectorBackend(self._redis),
            KnowledgeGraphMemoryAdapter(self._knowledge_graph),
        )

        # Registry of society membership and membership history. The registry
        # uses the memory subsystem to persist/retrieve membership-related
        # context rather than maintaining an independent persistence mechanism.
        self._membership_registry = SocietyMembershipRegistry(
            memory_manager=self._memory_manager
        )

        # Timeline of actor geographic presence. Presence is maintained separately
        # from the geographic registry: the registry describes geographic context,
        # while the timeline records how an actor's presence changes over time.
        self._presence = PresenceTimeline(self._geo_registry)

        # Governs membership transitions and their relationship to geographic
        # presence. Temporary membership grants/revocations are routed back into
        # the runtime through the supplied callbacks so membership changes can
        # trigger the appropriate state propagation.
        self._membership_governor = MembershipGovernor(
            self._presence,
            self._geo_registry,
            self._membership_registry,
            context_stream=self.context_stream,
            on_temporary_granted=self._on_temporary_membership_granted,
            on_temporary_revoked=self._on_temporary_membership_revoked,
        )

        # Models movement-related changes to actor state. The engine is given
        # the runtime's actor-movement operation so that simulated or policy-driven
        # movement goes through the same movement path as normal actor relocation.
        self._movement_perturbation = MovementPerturbationEngine(
            self.move_actor,
            self._presence,
            self._geo_registry,
        )

        # Society-level commerce network representing economic relationships and
        # interactions between actors. This is maintained as a separate domain
        # subsystem from the actor runtime and can evolve independently.

        #TODO: this must not be injected here the two systems must be completely separated 
        self._commerce_network = CommerceNetwork()

        from src.monkey_brain.kernel.society.delegation import DelegationRegistry

        # Registry for permissions delegated through the actor's membership relationships.
        self._delegation_registry = DelegationRegistry(self._membership_registry)

        # Coordinates activation and lifecycle of societies available to this actor.
        self._society_activation = SocietyActivationEngine(
            self.societies_for_actor, self.get_society_runtime,
        )

        # Graph representing relationships between actors and other entities.
        self._relationships = RelationshipGraph()

        # Builds the contextual state used by cognition from memory and knowledge.
        self._context_engine = ContextConstructionEngine(
            planetary_runtime=self, memory_manager=self._memory_manager,
            knowledge_graph=self._knowledge_graph,
        )

        # Graph representing the actor's affiliations with societies and other entities.
        from src.monkey_brain.kernel.affiliations.graph import AffiliationGraph

        # initate the affiliation graph
        self._affiliation_graph = AffiliationGraph(self)

        # Coordinates transactional operations across society-level state changes.
        from src.monkey_brain.kernel.society.transaction import TransactionCoordinator

        # initate the transaction cordinator
        self._transaction_coordinator = TransactionCoordinator(self)

        # Queue for external perturbations that may affect the actor or its environment.
        self._perturbation_queue = PerturbationQueue()

        # Factory for constructing the execution engine used by domain-specific capabilities.
        from src.monkey_brain.kernel.domains.vertical_router import build_execution_engine
        
        # In-memory registry of all society runtimes managed by this planetary runtime.
        self._societies: dict[str, SocietyRuntime] = {}

        # Number of completed cognitive/runtime cycles.
        self._cycle_count = 0

        # Version of the world perturbation context used to track world-state changes.
        self._world_perturbation_context_version = 0

        # Edge-First Architecture: construct the local authority/persistence
        # stack BEFORE any _load_* call below — every one of them (world,
        # societies, geography, actors, knowledge graph) falls back to this
        # when self._redis is None, so it must exist first, not be built
        # later alongside the execution engine (a real ordering bug this
        # comment exists to prevent regressing: _edge_local_store used to
        # be constructed after these _load_* calls, so they always saw it
        # as unset and silently failed to load anything on a disconnected
        # edge node's very first boot).
        node_class_hint = os.getenv("ACTOR_NODE_CLASS", "cloud").strip().lower()
        offline_safety_default = "true" if node_class_hint in ("edge", "device", "robot") else "false"
        offline_safety_enabled = (
            os.getenv("OFFLINE_SAFETY_GATE_ENABLED", offline_safety_default).strip().lower()
            not in ("false", "0", "no")
        )
        self._edge_local_store = None
        self._edge_policy_cache = None
        self._local_governance = None
        if offline_safety_enabled:
            from src.monkey_brain.kernel.edge.local_store import get_edge_local_store
            from src.monkey_brain.kernel.edge.policy_cache import EdgePolicyCache
            from src.monkey_brain.kernel.edge.local_governance import LocalGovernanceEvaluator

            self._edge_local_store = get_edge_local_store()
            self._edge_policy_cache = EdgePolicyCache(self._edge_local_store)
            self._local_governance = LocalGovernanceEvaluator(
                self._edge_policy_cache,
                current_authority_epoch_fn=lambda: self._edge_local_store.get_sync_state("policy")[0],
            )

        # Restore the persisted world state before reconstructing dependent runtime state.
        self._load_world()

        # Restore persisted societies and their runtime state.
        self._load_societies()

        # Use the first persisted society as the active society runtime when available.
        if self._societies:
            first_id = next(iter(self._societies))
            self._society_runtime = self._societies[first_id]
        else:
            # Persist the initially configured society when no societies have been stored yet.
            self._societies[self._society_runtime.society.society_id] = self._society_runtime
            self._save_societies()

        # Optional connectivity validation used by the offline safety gate.
        # Edge-First Architecture: defaults to ENABLED for edge/device/robot
        # node classes (an ACTOR_NODE_CLASS actor_runtime.py always sets
        # before constructing this) — previously defaulted to "false" for
        # every process, cloud or edge, meaning the entire offline-safety +
        # edge-authority mechanism below was inert everywhere unless an
        # operator explicitly opted in. Cloud's own default (unset
        # ACTOR_NODE_CLASS -> "cloud") is unchanged: still "false" unless
        # explicitly overridden, preserving exact prior behavior there.
        # (offline_safety_enabled/self._edge_local_store et al. were
        # already constructed above, before the _load_* calls.)
        connectivity_check = None
        edge_governance = None
        if offline_safety_enabled:
            # ActionExecutor (kernel/pipeline/action_executor.py) has
            # accepted an edge_governance parameter since it was built;
            # nothing in this factory chain ever constructed one to pass
            # in until now.
            from src.monkey_brain.kernel.pipeline.offline_safety import make_connectivity_check
            connectivity_check = make_connectivity_check(self)
            edge_governance = self._local_governance
        self._connectivity_check = connectivity_check

        # Grocery registers itself on import (vertical_router does not import
        # verticals). Boot used to hit this before any grocery import, so
        # resolve_vertical("grocery") failed with an empty registry.
        from src.monkey_brain.kernel.domains import grocery as _grocery_vertical  # noqa: F401

        # Build the domain execution engine with the current context stream and safety checks.
        self._execution_engine = build_execution_engine(
            "grocery", context_stream=self.context_stream, connectivity_check=connectivity_check,
            edge_governance=edge_governance,
        )

        # Attach each restored society to the planetary runtime.
        for society_runtime in self._societies.values():
            self._attach_society(society_runtime)

        # Restore the persisted geographic hierarchy before loading actors that depend on it.
        self._load_geography()

        # Restore persisted actors and their runtime state.
        self._load_actors()

        # Restore persisted contextual state used by the runtime.
        self._load_context()

        # Restore persisted relationships between actors and other entities.
        self._load_relationships()

        # Restore the persisted knowledge graph.
        self._load_knowledge_graph()

        # Bootstrap a default geographic hierarchy when no geography exists yet.
        if not self._geo_registry.all():
            # Create the root planetary geographic entity.
            self._default_planet = self._geo_registry.create(GeographicEntityType.PLANET, "Default Planet")

            # Create the country within the default planet.
            self._default_country = self._geo_registry.create(
                GeographicEntityType.COUNTRY, "Default Country", parent_id=self._default_planet.entity_id,
            )

            # Create the state within the default country.
            self._default_state = self._geo_registry.create(
                GeographicEntityType.STATE, "Default State", parent_id=self._default_country.entity_id,
            )

            # Create the county within the default state.
            self._default_county = self._geo_registry.create(
                GeographicEntityType.COUNTY, "Default County", parent_id=self._default_state.entity_id,
            )

            # Create the city within the default county.
            self._default_city = self._geo_registry.create(
                GeographicEntityType.CITY, "Default City", parent_id=self._default_county.entity_id,
            )

            # Create the default street within the city.
            default_street = self._geo_registry.create(
                GeographicEntityType.STREET, "Default Street", parent_id=self._default_city.entity_id,
            )

            # Create the default building within the street.
            default_building = self._geo_registry.create(
                GeographicEntityType.BUILDING, "Default Building", parent_id=default_street.entity_id,
            )

            # Create the default space used as the runtime's bootstrap location.
            self._default_space = self._geo_registry.create(
                GeographicEntityType.SPACE, "Default Space", parent_id=default_building.entity_id,
            )

            # Record the default space as the bootstrap location when one has not been configured.
            if self._default_bootstrap_space_id is None:
                self._default_bootstrap_space_id = self._default_space.entity_id

            # Associate every restored society with the default city.
            for society_runtime in self._societies.values():
                self._default_city = self._geo_registry.host_society(
                    self._default_city.entity_id, society_runtime.society.society_id
                )

            # Persist the newly created geographic hierarchy.
            self._save_geography()
        else:
            # Locate the existing default geographic entities by type and name.
            def _find_default(entity_type: GeographicEntityType, name: str) -> Any:
                candidates = self._geo_registry.all(entity_type)
                return next((e for e in candidates if e.name == name), None)

            # Restore references to the existing default geographic hierarchy.
            self._default_planet = _find_default(GeographicEntityType.PLANET, "Default Planet")
            self._default_country = _find_default(GeographicEntityType.COUNTRY, "Default Country")
            self._default_state = _find_default(GeographicEntityType.STATE, "Default State")
            self._default_county = _find_default(GeographicEntityType.COUNTY, "Default County")
            self._default_city = _find_default(GeographicEntityType.CITY, "Default City")
            self._default_space = _find_default(GeographicEntityType.SPACE, "Default Space")

            # Reconstruct the lower geographic hierarchy if the persisted space is missing.
            if self._default_space is None and self._default_city is not None:
                # Create the missing default street.
                default_street = self._geo_registry.create(
                    GeographicEntityType.STREET, "Default Street", parent_id=self._default_city.entity_id,
                )

                # Create the missing default building.
                default_building = self._geo_registry.create(
                    GeographicEntityType.BUILDING, "Default Building", parent_id=default_street.entity_id,
                )

                # Create the missing default space.
                self._default_space = self._geo_registry.create(
                    GeographicEntityType.SPACE, "Default Space", parent_id=default_building.entity_id,
                )

                # Persist the reconstructed geographic hierarchy.
                self._save_geography()

            # Establish the bootstrap space from the restored default space when necessary.
            if self._default_bootstrap_space_id is None and self._default_space is not None:
                self._default_bootstrap_space_id = self._default_space.entity_id

            # Ensure every society has a geographic host in the restored city.
            if self._default_city is not None:
                hosted_changed = False
                for society_runtime in self._societies.values():
                    sid = society_runtime.society.society_id

                    # Add the society to the default city when no geographic host exists.
                    if self._geo_registry.entity_for_society(sid) is None:
                        self._default_city = self._geo_registry.host_society(self._default_city.entity_id, sid)
                        hosted_changed = True

                # Persist geography only when society hosting was changed.
                if hosted_changed:
                    self._save_geography()

        # Initialize the event persistence layer after the core runtime state has been restored.
        self._event_store = None
        self._init_event_store()

        # Track the persisted version of each actor's context for incremental persistence.
        self._context_persisted_version: dict[str, int] = {}

        # Register persistence as the callback invoked whenever the context stream publishes changes.
        self.context_stream.set_on_publish(self._save_context)

        # Initialize persistence for collective learning state.
        self._init_collective_learning_persistence()

    def _init_persistence(self) -> None:
        """Initialize Redis client for world/actor/society persistence."""
        try:
            import redis as _redis
            retry = _redis.retry.Retry(_redis.backoff.ExponentialBackoff(cap=2, base=0.1), 3)
            retry_kwargs = dict(
                retry=retry,
                retry_on_error=[_redis.exceptions.ConnectionError, _redis.exceptions.TimeoutError],
                socket_timeout=5, health_check_interval=30,
            )
            redis_url = os.getenv("REDIS_URL", "").strip()
            if redis_url and "REDIS_HOST" not in os.environ:
                self._redis = _redis.Redis.from_url(
                    redis_url, decode_responses=True, socket_connect_timeout=2, **retry_kwargs,
                )
            else:
                self._redis = _redis.Redis(
                    host=os.getenv("REDIS_HOST", "localhost"),
                    port=int(os.getenv("REDIS_PORT", "6379")),
                    db=int(os.getenv("REDIS_DB", "0")),
                    decode_responses=True,
                    socket_connect_timeout=2,
                    **retry_kwargs,
                )
            self._redis.ping()
            self._persistence_manager = None
            logger.info("Redis connected for PlanetaryRuntime persistence")
            from src.monkey_brain.kernel.production_gates import validate_production_gates
            validate_production_gates(
                redis_available=True,
                opa_configured=bool(os.getenv("OPA_URL", "").strip()),
            )
            
            from src.monkey_brain.kernel.society.redis_index_reconstruction import (
                RedisIndexReconstructor,
            )
            self._redis_reconstructor = RedisIndexReconstructor(self)
            
            from src.monkey_brain.kernel.society.actor_state_rehydrator import (
                ActorStateRehydrator,
            )
            self._actor_state_rehydrator = ActorStateRehydrator(self)
            # Rehydrate actors from MongoDB (must happen before _load_actors)
            # Non-blocking; errors are logged but don't block startup
            try:
                rehydration_result = self._actor_state_rehydrator.rehydrate_from_mongodb()
                if rehydration_result.success:
                    logger.info("Actor state rehydration complete: %s", rehydration_result.summary())
                    
                    # Enforce desired state for rehydrated actors immediately
                    # This ensures actors don't unexpectedly become active if
                    # they were previously PAUSED/SUSPENDED/TERMINATED.
                    try:
                        lifecycle = self.lifecycle
                        lifecycle_results = lifecycle.reconcile_rehydrated_actors()
                        logger.info(
                            "Rehydrated actor lifecycle reconciliation: %d total, "
                            "%d enforced desired state",
                            len(lifecycle_results),
                            sum(1 for r in lifecycle_results if r.action != "none"),
                        )
                    except Exception as exc:
                        logger.warning("Rehydrated actor lifecycle reconciliation failed: %s", exc)
                else:
                    logger.warning("Actor state rehydration failed: %d errors", len(rehydration_result.errors))
            except Exception as exc:
                logger.warning("Actor state rehydration failed: %s", exc)

            try:
                rebuild = self.rebuild_redis_index_from_mongodb()
                logger.info("Redis actor index rebuilt from MongoDB: %s", rebuild.summary())
            except Exception as exc:
                logger.warning("Redis index rebuild from MongoDB failed: %s", exc)
            
        except Exception as exc:
            logger.warning("Redis not available: %s", exc)
            self._redis = None
            self._persistence_manager = None
            self._redis_reconstructor = None
            from src.monkey_brain.kernel.production_gates import validate_production_gates
            validate_production_gates(
                redis_available=False,
                opa_configured=bool(os.getenv("OPA_URL", "").strip()),
            )

    def _init_event_store(self) -> None:
        """Initialize the event persistence layer."""
        try:
            from src.monkey_brain.persistence.context_event_store import get_event_store
            self._event_store = get_event_store()
            if self._redis:
                self._event_store.set_redis(self._redis)
                # Set small buffer size for immediate persistence
                self._event_store._buffer_size = 1
            logger.info("Event persistence layer initialized")
        except Exception as exc:
            logger.warning("Event store initialization failed: %s", exc)
            self._event_store = None

    def _init_collective_learning_persistence(self) -> None:
        """Wire the real Redis client into every already-registered
        SocietyRuntime's CollectiveLearningEngine (mirrors
        _init_event_store's set_redis() pattern) and hydrate each from
        whatever was persisted before a restart. A no-op, not an error,
        when Redis isn't available (each engine already degrades to
        in-memory-only on its own — see CollectiveLearningEngine.set_redis's
        own docstring)."""
        if not self._redis:
            return
        for society_runtime in self._societies.values():
            engine = society_runtime.collective_learning
            engine.set_redis(self._redis, society_runtime.society.society_id)
            loaded = engine.load_recent()
            if loaded:
                logger.info(
                    "CollectiveLearningEngine restored %d experiences for society %s",
                    loaded, society_runtime.society.society_id,
                )

    _KG_ENTITIES_HASH_KEY = "monkeybrain:knowledge_graph:entities"
    _KG_RELATIONSHIPS_HASH_KEY = "monkeybrain:knowledge_graph:relationships"

    # ── Edge-First Architecture: generic local-persistence fallback ─────
    #
    # Every _load_*/_save_* pair below (KG/catalog, world, actors,
    # societies, geography) historically opened with `if not self._redis:
    # return` — a silent no-op with no local durability whatsoever. These
    # three helpers give those methods a SECOND, local-only home for the
    # exact same snapshot/hash data (kernel/edge/local_store.py::
    # EdgeLocalStore, SQLite, no network dependency) — used only when
    # self._edge_local_store was constructed (offline-safety gate enabled,
    # see __init__), never replacing Redis when Redis is reachable. Kept
    # deliberately generic (three tiny methods) rather than one bespoke
    # local-persistence mechanism per subsystem, mirroring EdgeLocalStore's
    # own "namespace column, not a table per concept" design choice.
    def _edge_local_put(self, namespace: str, key: str, value: dict) -> None:
        if self._edge_local_store is None:
            return
        try:
            from src.monkey_brain.kernel.edge.freshness import CacheProvenance
            provenance = CacheProvenance(source="edge_local:planetary_runtime", observed_at=time.time(), freshness_requirement="safe_offline")
            self._edge_local_store.put(namespace, key, value, provenance)
        except Exception as exc:
            logger.debug("edge-local put failed for %s/%s: %s", namespace, key, exc)

    def _edge_local_get(self, namespace: str, key: str) -> dict | None:
        if self._edge_local_store is None:
            return None
        try:
            entry = self._edge_local_store.get(namespace, key)
            return entry.value if entry is not None else None
        except Exception as exc:
            logger.debug("edge-local get failed for %s/%s: %s", namespace, key, exc)
            return None

    def _edge_local_list(self, namespace: str) -> list[dict]:
        if self._edge_local_store is None:
            return []
        try:
            return [entry.value for entry in self._edge_local_store.list(namespace)]
        except Exception as exc:
            logger.debug("edge-local list failed for %s: %s", namespace, exc)
            return []

    def _edge_local_delete(self, namespace: str, key: str) -> None:
        if self._edge_local_store is None:
            return
        try:
            self._edge_local_store.delete(namespace, key)
        except Exception as exc:
            logger.debug("edge-local delete failed for %s/%s: %s", namespace, key, exc)

    def _on_knowledge_graph_change(self, kind: str, obj_id: str, action: str) -> None:
        """Gate 6 (Persistence) — KnowledgeGraph.set_on_change()'s callback.
        O(1) per mutation from the start (ADR-011 already found the cost
        of getting this wrong): one HSET/HDEL for the single entity or
        relationship that actually changed, never a full-graph resync.
        Edge-First Architecture: this is also where the operational
        catalog (products/stores, KG entities with entity_type=ASSET)
        gets its edge-local persistence — same generic entity/relationship
        path, no separate catalog-specific mechanism."""
        namespace = "kg_entity" if kind == "entity" else "kg_relationship"
        if kind == "entity":
            getter = self._knowledge_graph.get_entity
        else:
            getter = self._knowledge_graph.get_relationship

        if action == "delete":
            if self._redis:
                try:
                    hash_key = self._KG_ENTITIES_HASH_KEY if kind == "entity" else self._KG_RELATIONSHIPS_HASH_KEY
                    self._redis.hdel(hash_key, obj_id)
                except Exception as exc:
                    logger.debug("KnowledgeGraph save failed for %s %s: %s", kind, obj_id, exc)
            self._edge_local_delete(namespace, obj_id)
            return

        obj = getter(obj_id)
        if obj is None:
            return
        if self._redis:
            try:
                hash_key = self._KG_ENTITIES_HASH_KEY if kind == "entity" else self._KG_RELATIONSHIPS_HASH_KEY
                self._redis.hset(hash_key, obj_id, json.dumps(obj.to_dict()))
            except Exception as exc:
                logger.debug("KnowledgeGraph save failed for %s %s: %s", kind, obj_id, exc)
        self._edge_local_put(namespace, obj_id, obj.to_dict())

    def _load_knowledge_graph(self) -> None:
        try:
            from src.monkey_brain.kernel.knowledge_graph import Entity, Relationship

            if self._redis:
                entities_data = self._redis.hgetall(self._KG_ENTITIES_HASH_KEY)
                relationships_data = self._redis.hgetall(self._KG_RELATIONSHIPS_HASH_KEY)
            else:
                entities_data = {d["entity_id"]: json.dumps(d) for d in self._edge_local_list("kg_entity")}
                relationships_data = {d["relationship_id"]: json.dumps(d) for d in self._edge_local_list("kg_relationship")}

            for entity_id, raw in entities_data.items():
                entity = Entity.from_dict(json.loads(raw))
                self._knowledge_graph._entities[entity_id] = entity
                self._knowledge_graph._index_add(entity)

            for rel_id, raw in relationships_data.items():
                rel = Relationship.from_dict(json.loads(raw))
                self._knowledge_graph._relationships[rel_id] = rel
                self._knowledge_graph._adjacency.setdefault(rel.source_id, []).append(rel_id)
                self._knowledge_graph._reverse_adjacency.setdefault(rel.target_id, []).append(rel_id)

            if entities_data or relationships_data:
                logger.info(
                    "KnowledgeGraph loaded: %d entities, %d relationships",
                    len(entities_data), len(relationships_data),
                )
        except Exception as exc:
            logger.warning("KnowledgeGraph load failed: %s", exc)

    def _save_world(self) -> None:
        if self._redis:
            try:
                self._redis.set("monkeybrain:world", json.dumps(self._world.to_dict()))
            except Exception as exc:
                logger.debug("World save failed: %s", exc)
        self._edge_local_put("world", "world", self._world.to_dict())

    def _load_world(self) -> None:
        try:
            data = self._redis.get("monkeybrain:world") if self._redis else None
            if data is None:
                edge_value = self._edge_local_get("world", "world")
                data = json.dumps(edge_value) if edge_value is not None else None
            if data:
                new_world = SharedWorld.from_dict(json.loads(data))
                # Update existing world in-place instead of replacing
                # (actors hold references to self._world, replacing would break them)
                semantic_world = self._world_model.semantic_world
                semantic_world._entities = new_world._entities
                semantic_world._events = new_world._events
                semantic_world._relationships = new_world._relationships
                semantic_world._resources = new_world._resources
                semantic_world._capabilities = new_world._capabilities
                semantic_world._locations = new_world._locations
                semantic_world._policies = new_world._policies
                semantic_world._version = new_world._version
                self._society_runtime._world = self._world
                self._society_runtime._observation_provider._world = self._world
                logger.info("World refreshed: %d entities, version %d",
                            len(list(self._world.entities())), self._world.version)
        except Exception as exc:
            logger.warning("World load failed: %s", exc)

    _ACTORS_HASH_KEY = "monkeybrain:actors:hash"
    _ACTORS_LEGACY_ARRAY_KEY = "monkeybrain:actors"

    def _actor_state_to_dict(self, state: Any, society_id: str) -> dict[str, Any]:
        actor_data = state.profile.to_dict()
        actor_data["society_id"] = society_id
        if state.belief_state is not None:
            actor_data["belief_state"] = state.belief_state.to_dict()
        affiliations = getattr(state.actor_runtime, "affiliations", None)
        if affiliations is not None and affiliations.count():
            actor_data["affiliations"] = affiliations.to_dict()
        # Actor Registry (Deployment Architecture, Section 7): these three
        # fields are what turn this already-real, already-Redis-backed
        # persistence into a genuine registry lookup (locate_actor()/
        # list_registry() below) rather than only a rehydration payload —
        # a caller can now answer "does this actor exist, what state was
        # it in, and which node last owned it" via a single HGET, with no
        # need to reconstruct the actor's full cognition first.
        status = getattr(state, "status", None)
        actor_data["status"] = status.value if hasattr(status, "value") else str(status or "")
        actor_data["node_id"] = self._node_id
        actor_data["updated_at"] = time.time()
        actor_data["artifact_version"] = self._artifact_version
        actor_data["runtime_version"] = self._runtime_version
        return actor_data

    def _save_actor(self, state: Any, society_id: str = "") -> None:
        """Persist ONE actor — O(1), not O(n) in however many actors exist.

        This is what register_actor() calls on every registration (its
        hot path): the previous implementation called _save_actors()
        (below), which re-serializes and rewrites EVERY actor ever
        registered on every single call — O(n) work per registration,
        O(n^2) total to register n actors. Confirmed live: 200 actors
        through register_actor() took over 60s and was still climbing.
        Both this and _save_actors() write to the SAME hash key so the
        two paths never drift out of sync with each other."""
        try:
            if not society_id:
                society_id = next(
                    (sid for sid, s in self._societies.items() if state in s.all_actors()), "",
                )
            actor_data = self._actor_state_to_dict(state, society_id)
            if self._redis:
                self._redis.hset(self._ACTORS_HASH_KEY, state.actor_id, json.dumps(actor_data))
            self._edge_local_put("actor", state.actor_id, actor_data)
        except Exception as exc:
            # Live Deployment Validation finding: this was DEBUG-level,
            # invisible at this deployment's default LOG_LEVEL=INFO --
            # a real durable-persistence failure (the actor stays
            # registered in-memory, callers see success, but the
            # Registry write silently never happens) needs to be
            # visible without an operator having to already suspect
            # this exact method and turn on DEBUG logging to find it.
            logger.warning("Actor save failed for %r: %s", getattr(state, "actor_id", "?"), exc)

    def _save_actors(self) -> None:
        """Full resync of every currently-registered actor — still O(n)
        per call by nature (there are n actors to write), but now writes
        via the same per-field hash _save_actor() uses (HSET, one
        pipelined write per actor) rather than one giant JSON blob, so a
        caller that only changed one actor should prefer _save_actor()
        instead of paying for everyone else's unchanged data too."""
        try:
            seen: set[str] = set()
            pipe = self._redis.pipeline() if self._redis else None
            wrote_any = False
            for sid, sr in self._societies.items():
                for state in sr.all_actors():
                    if state.actor_id in seen:
                        continue
                    seen.add(state.actor_id)
                    actor_data = self._actor_state_to_dict(state, sid)
                    if pipe is not None:
                        pipe.hset(self._ACTORS_HASH_KEY, state.actor_id, json.dumps(actor_data))
                        wrote_any = True
                    self._edge_local_put("actor", state.actor_id, actor_data)
            if wrote_any and pipe is not None:
                pipe.execute()
        except Exception as exc:
            logger.debug("Actors save failed: %s", exc)

    def _load_actors(self) -> None:
        """Load actors from Redis (or, absent Redis, EdgeLocalStore — Edge-
        First Architecture) and register them.

        Idempotent: skips actors that are already registered (by name + society).

        Reads the per-actor hash _save_actor()/_save_actors() now write
        (HGETALL) — falls back to the legacy single-JSON-array key
        (read-only, never written again) so actors persisted before this
        fix stay visible instead of silently disappearing on the next boot.
        """
        try:
            if self._redis:
                hash_data = self._redis.hgetall(self._ACTORS_HASH_KEY)
                if hash_data:
                    actors = [json.loads(v) for v in hash_data.values()]
                else:
                    legacy = self._redis.get(self._ACTORS_LEGACY_ARRAY_KEY)
                    actors = json.loads(legacy) if legacy else []
            else:
                actors = self._edge_local_list("actor")
            # Live Deployment Validation finding (P0, confirmed live):
            # a single-actor Actor Runtime pod (actor_runtime.py,
            # ACTOR_ID set) previously loaded and locally activated
            # EVERY actor in the shared registry, not just its own --
            # get_actor_runtime(actor_id) then returned non-None for
            # OTHER actors too, defeating suspend_actor_for_migration's
            # "only act if resident_here" guard (integration.py's own
            # docstring there: "a no-op ... when the actor isn't
            # resident here"). Confirmed live: a two-actor rollout
            # produced BOTH actors' durable registry records pointing
            # at the SAME (wrong) node_id, written within 3ms of each
            # other -- one pod's migrate-away reconciliation for an
            # actor it never actually owned stamped its own node_id
            # into that actor's registry entry. Scoping this load to
            # ACTOR_ID when set closes the gap at its source: a
            # single-actor pod now only ever has ITS OWN actor's
            # runtime resident, so resident_here is correctly False for
            # every other actor, and AskActor-style cross-actor lookups
            # still work via locate_actor()'s durable-registry fallback
            # (see grocery.py::AskActorCapability, already the fallback
            # path for an actor not in this process's local societies).
            # None (default) preserves the exact prior full-load
            # behavior for every multi-actor process (deployment.yaml's
            # control-plane pod, tests) that never sets ACTOR_ID.
            scope_actor_id = os.getenv("ACTOR_ID", "").strip()
            if scope_actor_id:
                actors = [
                    a for a in actors
                    if a.get("identity", {}).get("actor_id") == scope_actor_id
                ]
            if actors:
                loaded = 0
                skipped = 0
                for actor_data in actors:
                    try:
                        profile = ActorProfile.from_dict(actor_data)
                        sid = actor_data.get("society_id", "")
                        target_sr = self._societies.get(sid) or self._society_runtime

                        # Check if actor already exists (by name + society)
                        existing = None
                        for state in target_sr.all_actors():
                            if (state.profile.identity.name == profile.identity.name and
                                state.profile.identity.actor_type == profile.identity.actor_type):
                                existing = state
                                break

                        if existing is not None:
                            # Actor already exists, skip registration
                            skipped += 1
                            continue

                        # Register through SocietyRuntime; implementation actor
                        # construction is hidden behind ActorRuntime.
                        target_sr.register_actor(profile)
                        loaded += 1
                        # Multi-Actor Execution Handoff: this loop bypasses
                        # PlanetaryRuntime.register_actor() (the method above
                        # that would otherwise wire this) entirely — every
                        # persisted actor needs its real NATS inbox
                        # re-subscribed after every restart, same as a
                        # brand-new registration.
                        self._subscribe_actor_inbox(profile.identity.actor_id, profile)

                        if "belief_state" in actor_data and actor_data["belief_state"]:
                            from src.monkey_brain.kernel.society.belief import BeliefState
                            state = target_sr.get_actor(profile.identity.actor_id)
                            if state:
                                state.belief_state = BeliefState.from_dict(actor_data["belief_state"])

                        # Actor Registry: restore the persisted lifecycle
                        # status too (register_actor() above always creates a
                        # fresh REGISTERED state) -- without this, an actor
                        # that was ACTIVE before a restart silently reverts to
                        # REGISTERED on reload, and nothing re-activates it.
                        # Unknown/legacy records (no "status" key, from before
                        # this field existed) are left at the fresh default
                        # rather than guessed.
                        persisted_status = actor_data.get("status")
                        if persisted_status:
                            from src.monkey_brain.kernel.society.domain import ActorStatus
                            state = target_sr.get_actor(profile.identity.actor_id)
                            if state:
                                try:
                                    state.status = ActorStatus(persisted_status)
                                except ValueError:
                                    logger.debug("Unknown persisted actor status %r for %s", persisted_status, profile.identity.actor_id)

                        restored_state = target_sr.get_actor(profile.identity.actor_id)
                        restored_runtime = restored_state.actor_runtime if restored_state else None
                        restored_affiliations = getattr(restored_runtime, "affiliations", None)
                        if actor_data.get("affiliations") and restored_affiliations is not None:
                            from src.monkey_brain.kernel.affiliations.manager import AffiliationManager
                            restored = AffiliationManager.from_dict(actor_data["affiliations"])
                            for aff in restored.all():
                                restored_affiliations.add(aff)
                            for target, level in restored.trust_engine.all_trust("self").items():
                                restored_affiliations.trust_engine.set_trust("self", target, level)
                    except Exception as actor_exc:
                        # A single malformed persisted record (e.g. a legacy
                        # actor_type value not in the current ActorType enum)
                        # must not abort loading of every other actor in the
                        # registry — that previously left the whole registry
                        # empty after any one bad record, breaking prompt
                        # execution for every actor, not just the bad one.
                        bad_id = actor_data.get("identity", {}).get("actor_id", "<unknown>")
                        logger.warning("Skipping malformed persisted actor %s: %s", bad_id, actor_exc)
                        continue

                logger.info("Actors loaded: %d, skipped (already exist): %d", loaded, skipped)
        except Exception as exc:
            logger.warning("Actors load failed: %s", exc)

    def reconcile_actors_from_redis(self) -> None:
        """Qualification Gap Closure (BUG-002, Cause A): public entry point
        for a caller that needs THIS process's in-memory actor registry to
        reflect the real, shared Redis state before trusting it — the real
        cause of a genuine class of world-validation false positive
        (actor_without_presence / membership_invalid_actor): a real
        Presence/Membership record can be written by ANY process sharing
        this Redis (this session's own in-process pytest tests are one real
        example, confirmed live), but _load_actors() (above) was only ever
        called once, at __init__ -- an actor persisted AFTER this process
        booted never got reconciled into sr._actors, so a real, valid
        record for it looked identical to genuine corruption. _load_actors()
        is already idempotent (skips by name+society) and already does
        exactly this reconciliation; this just gives it a second, real
        caller (kernel/validation/world_validator.py's Gate 3, called
        before flagging a violation) instead of inventing new logic."""
        self._load_actors()

    @staticmethod
    def _registry_entry_from_dict(actor_data: dict[str, Any]) -> "ActorRegistryEntry":
        identity = actor_data.get("identity") or {}
        return ActorRegistryEntry(
            actor_id=identity.get("actor_id", ""),
            actor_type=identity.get("actor_type", ""),
            name=identity.get("name", ""),
            society_id=actor_data.get("society_id", ""),
            status=actor_data.get("status", ""),
            node_id=actor_data.get("node_id", ""),
            updated_at=float(actor_data.get("updated_at", 0.0) or 0.0),
            artifact_version=actor_data.get("artifact_version", ""),
            runtime_version=actor_data.get("runtime_version", ""),
        )

    def _list_registry_from_mongodb(self) -> tuple["ActorRegistryEntry", ...] | None:
        recon = getattr(self, "_redis_reconstructor", None)
        if recon is None:
            try:
                from src.monkey_brain.kernel.society.redis_index_reconstruction import (
                    RedisIndexReconstructor,
                )
                recon = RedisIndexReconstructor(self)
            except Exception:
                return None
        store = self._get_actor_state_store()
        if not store:
            return None
        try:
            docs = recon._scan_mongodb_actors(store)
        except Exception:
            return None
        entries: list[ActorRegistryEntry] = []
        for actor_id, doc in docs.items():
            raw = recon._construct_registry_entry_from_mongodb(actor_id, doc)
            if raw:
                entries.append(self._registry_entry_from_dict(raw))
        return tuple(entries)

    def _locate_actor_from_mongodb(self, actor_id: str) -> "ActorRegistryEntry | None":
        rows = self._list_registry_from_mongodb()
        if not rows:
            return None
        for entry in rows:
            if entry.actor_id == actor_id:
                return entry
        return None

    def _cache_registry_entry(self, entry: "ActorRegistryEntry") -> None:
        if not self._redis:
            return
        try:
            payload = {
                "identity": {
                    "actor_id": entry.actor_id,
                    "actor_type": entry.actor_type,
                    "name": entry.name,
                },
                "society_id": entry.society_id,
                "status": entry.status,
                "node_id": entry.node_id,
                "updated_at": entry.updated_at,
                "artifact_version": entry.artifact_version,
                "runtime_version": entry.runtime_version,
            }
            self._redis.hset(self._ACTORS_HASH_KEY, entry.actor_id, json.dumps(payload))
        except Exception as exc:
            logger.debug("cache registry entry failed: %s", exc)

    def locate_actor(self, actor_id: str) -> ActorRegistryEntry | None:
        """Actor Registry lookup: does `actor_id` exist anywhere this
        PlanetaryRuntime's Redis knows about, which society is it home to,
        what lifecycle status was it last recorded in, and which node
        (self._node_id, possibly a DIFFERENT process than the one calling
        this) last wrote that record?

        Deliberately does NOT reconstruct the actor (no register_actor(),
        no belief_state/affiliations restoration, no cognition) — a single
        Redis HGET, safe to call from any process for a cheap existence/
        location check. This is the primitive Section 8 of the Deployment
        Architecture needs for cross-process discovery (e.g. AskActor
        resolving a target actor that isn't in THIS process's in-memory
        `_actors`) — resolution can check here before giving up, instead
        of only ever searching pr.all_societies() locally.

        Falls back to the in-memory registry (this process's own
        `_home_society_runtime`) when Redis is unavailable, so a
        single-process/dev/test setup with no Redis still gets a real
        answer, not an unconditional None.
        """
        rows = self._list_registry_from_mongodb()
        if rows:
            for entry in rows:
                if entry.actor_id == actor_id:
                    self._cache_registry_entry(entry)
                    return entry
        elif rows is None and self._redis:
            try:
                raw = self._redis.hget(self._ACTORS_HASH_KEY, actor_id)
                if raw:
                    return self._registry_entry_from_dict(json.loads(raw))
            except Exception as exc:
                logger.debug("locate_actor(%r): Redis lookup failed: %s", actor_id, exc)
        elif rows is not None and self._redis:
            try:
                raw = self._redis.hget(self._ACTORS_HASH_KEY, actor_id)
                if raw:
                    logger.warning(
                        "locate_actor(%r): Redis entry ignored; Mongo is the registry of record",
                        actor_id,
                    )
            except Exception as exc:
                logger.debug("locate_actor(%r): Redis lookup failed: %s", actor_id, exc)
        sr = self._home_society_runtime(actor_id)
        if sr is None:
            return None
        state = sr.get_actor(actor_id)
        if state is None:
            return None
        status = getattr(state, "status", None)
        return ActorRegistryEntry(
            actor_id=actor_id,
            actor_type=state.profile.identity.actor_type.value if hasattr(state.profile.identity.actor_type, "value") else str(state.profile.identity.actor_type),
            name=state.profile.identity.name,
            society_id=sr.society.society_id,
            status=status.value if hasattr(status, "value") else str(status or ""),
            node_id=self._node_id,
            updated_at=state.last_cycle or self._boot_time,
        )

    def list_registry(self) -> tuple[ActorRegistryEntry, ...]:
        """Every Actor this PlanetaryRuntime's Redis registry currently
        knows about, across every society and regardless of which node
        last wrote each record — the "any Execution Node can discover any
        Actor" primitive the Deployment Architecture's Control Plane
        needs (Sections 5/6/7). Lightweight: parses only the registry
        fields (society_id/status/node_id/updated_at/identity), never
        reconstructs belief_state or affiliations. Falls back to this
        process's own in-memory actor set when Redis is unavailable."""
        rows = self._list_registry_from_mongodb()
        if rows:
            for entry in rows:
                self._cache_registry_entry(entry)
            return rows
        if rows is None and self._redis:
            try:
                hash_data = self._redis.hgetall(self._ACTORS_HASH_KEY)
                if hash_data:
                    return tuple(
                        self._registry_entry_from_dict(json.loads(raw))
                        for raw in hash_data.values()
                    )
            except Exception as exc:
                logger.debug("list_registry(): Redis scan failed: %s", exc)
        elif rows is not None and self._redis:
            try:
                hash_data = self._redis.hgetall(self._ACTORS_HASH_KEY)
                if hash_data:
                    logger.warning(
                        "list_registry(): ignoring Redis index with no Mongo documents"
                    )
            except Exception as exc:
                logger.debug("list_registry(): Redis scan failed: %s", exc)
        entries: list[ActorRegistryEntry] = []
        for sid, sr in self._societies.items():
            for state in sr.all_actors():
                status = getattr(state, "status", None)
                entries.append(ActorRegistryEntry(
                    actor_id=state.actor_id,
                    actor_type=state.profile.identity.actor_type.value if hasattr(state.profile.identity.actor_type, "value") else str(state.profile.identity.actor_type),
                    name=state.profile.identity.name,
                    society_id=sid,
                    status=status.value if hasattr(status, "value") else str(status or ""),
                    node_id=self._node_id,
                    updated_at=state.last_cycle or self._boot_time,
                    artifact_version=self._artifact_version,
                    runtime_version=self._runtime_version,
                ))
        return tuple(entries)

    def rebuild_redis_index_from_mongodb(self) -> "RedisReconstructionResult":
        """Rebuild Redis actor registry from MongoDB source of truth.
        
        Closes ephemeral storage gap: when Redis is lost (pod crash,
        emptyDir recreation), actors persist in MongoDB but become invisible
        to registry lookups. This method deterministically reconstructs the
        Redis index from MongoDB, enabling discovery again.
        
        **Automatic:** Called during boot if inconsistency detected.
        **Manual:** Call explicitly to force rebuild or verify recovery.
        **Idempotent:** Safe to run multiple times; skips recent entries.
        
        Returns:
            RedisReconstructionResult with reconstruction statistics
        """
        if not self._redis_reconstructor:
            logger.warning("Redis reconstructor not available")
            from src.monkey_brain.kernel.society.redis_index_reconstruction import (
                RedisReconstructionResult,
            )
            return RedisReconstructionResult(
                success=False,
                actors_scanned=0,
                actors_rebuilt=0,
                actors_skipped=0,
                errors=[],
                duration_seconds=0.0,
            )
        
        return self._redis_reconstructor.rebuild_from_mongodb()
    
    def verify_redis_mongodb_consistency(self) -> "ConsistencyCheckResult":
        """Verify consistency between Redis and MongoDB actor registries.
        
        Detects:
        • Actors in MongoDB but missing from Redis (needs rebuild)
        • Actors in Redis but missing from MongoDB (corruption)
        • Stale Redis entries (not updated recently)
        
        **Use case:** Diagnostic tool; call before/after Redis recovery.
        
        Returns:
            ConsistencyCheckResult with detailed findings
        """
        if not self._redis_reconstructor:
            logger.warning("Redis reconstructor not available")
            from src.monkey_brain.kernel.society.redis_index_reconstruction import (
                ConsistencyCheckResult,
            )
            return ConsistencyCheckResult(
                is_consistent=False,
                total_in_mongodb=0,
                total_in_redis=0,
                missing_from_redis=[],
                missing_from_mongodb=[],
                stale_entries=[],
                issues=["Redis reconstructor not available"],
            )
        
        return self._redis_reconstructor.verify_consistency()

    # ── Actor Lifecycle Controller: desired state ──────────────────────────
    # (kernel/society/actor_lifecycle.py::ActorDesiredState,
    # actor_lifecycle_controller.py::ActorLifecycleController). A dedicated
    # key per actor_id, deliberately separate from the actor registry hash
    # (_ACTORS_HASH_KEY) above: desired state is a declarative record
    # ("what should this actor be doing") independent of whether the actor
    # is currently registered/resident anywhere, the same way a Kubernetes
    # Pod's spec lives in etcd independently of whether any kubelet has it
    # scheduled — writing it is a plain SET, never a read-modify-write
    # race on the much larger actor profile/belief/affiliations blob.

    _ACTOR_DESIRED_STATE_KEY_PREFIX = "monkeybrain:actor:desired_state:"

    def set_actor_desired_state(self, actor_id: str, desired: "ActorDesiredState", *, reason: str = "") -> None:
        """Durably record what the control plane wants for this actor.
        Never raises — a failed write degrades to the in-memory fallback
        (this process only), never blocks the caller."""
        payload = {
            "state": desired.value, "reason": reason,
            "set_at": time.time(), "set_by": self._node_id,
        }
        if self._redis is not None:
            try:
                self._redis.set(f"{self._ACTOR_DESIRED_STATE_KEY_PREFIX}{actor_id}", json.dumps(payload))
                self._enqueue_reconciliation(actor_id)
                return
            except Exception as exc:
                logger.warning("set_actor_desired_state(%r) Redis write failed: %s", actor_id, exc)
        self._desired_state_fallback[actor_id] = payload

    def get_actor_desired_state(self, actor_id: str) -> "ActorDesiredState":
        """What the control plane wants for this actor. Defaults to
        RUNNING when no explicit record exists — matches the pre-Lifecycle-
        Controller behavior every already-registered actor already had
        (register + activate = tick automatically), so introducing this
        controller does not silently pause any existing actor that never
        had its desired state set explicitly."""
        from src.monkey_brain.kernel.society.actor_lifecycle import ActorDesiredState

        payload: dict[str, Any] | None = None
        if self._redis is not None:
            try:
                raw = self._redis.get(f"{self._ACTOR_DESIRED_STATE_KEY_PREFIX}{actor_id}")
                if raw:
                    payload = json.loads(raw)
            except Exception as exc:
                logger.debug("get_actor_desired_state(%r) Redis read failed: %s", actor_id, exc)
        if payload is None:
            payload = self._desired_state_fallback.get(actor_id)
        if payload is None:
            return ActorDesiredState.RUNNING
        try:
            return ActorDesiredState(payload.get("state", "running"))
        except ValueError:
            return ActorDesiredState.RUNNING

    _ACTOR_STALE_SECONDS = float(os.getenv("ACTOR_LIFECYCLE_STALE_SECONDS", "600"))
    """A RUNNING-desired actor whose registry record hasn't been refreshed
    in this long, AND whose lease no one currently holds, is treated as
    FAILED (Section 12). Default 600s = 2x the default auto-tick interval
    (AGENTOS_TICK_INTERVAL, kernel.py) plus real margin for a slow LLM-
    bound tick, so a merely-busy actor is never misclassified as crashed."""

    def observe_actor(self, actor_id: str, *, reconcile_lease_token: str | None = None) -> "ObservedActorState":
        """Merge durable registry state with local residency and lease status.

        Does not reconstruct cognition. reconcile_lease_token excludes the
        caller's own reconcile lease from the staleness calculation so crash
        recovery is not downgraded while the lease is held.
        """
        from src.monkey_brain.kernel.society.actor_lifecycle import ObservedActorState

        entry = self.locate_actor(actor_id)
        resident_here = False
        home_sr = self._home_society_runtime(actor_id)
        if home_sr is not None:
            local_state = home_sr.get_actor(actor_id)
            if local_state is not None and local_state.is_active:
                resident_here = True
        if entry is None and not resident_here:
            return ObservedActorState(actor_id=actor_id, exists=False)

        status = entry.status if entry is not None else ""
        node_id = entry.node_id if entry is not None else self._node_id
        updated_at = entry.updated_at if entry is not None else self._boot_time
        lease_held = False
        lease_held_by_other = False
        if self._redis is not None:
            try:
                raw = self._redis.get(f"{_ACTOR_LEASE_KEY_PREFIX}{actor_id}")
                if raw:
                    lease_held = True
                    lease_held_by_other = not (
                        reconcile_lease_token is not None and raw == reconcile_lease_token
                    )
            except Exception as exc:
                logger.debug("observe_actor(%r) lease check failed: %s", actor_id, exc)
        desired = self.get_actor_desired_state(actor_id)
        is_stale = (
            desired.value == "running"
            and not lease_held_by_other
            and (time.time() - updated_at) > self._ACTOR_STALE_SECONDS
        )
        return ObservedActorState(
            actor_id=actor_id, exists=True, status=status, node_id=node_id,
            updated_at=updated_at, is_stale=is_stale, resident_here=resident_here,
            lease_held=lease_held, desired_node_id=self.get_actor_desired_node(actor_id),
        )

    @property
    def lifecycle(self) -> "ActorLifecycleController":
        """The Actor Lifecycle Controller (Deployment Architecture,
        Section 5) — manages actor lifecycle (create/start/suspend/
        resume/terminate/recover), never actor cognition. Lazily
        constructed, same composition pattern as self._game_theory/
        self._coordination_engine: a facilitator holding a back-reference
        to this PlanetaryRuntime, owning none of its state directly."""
        if self._lifecycle_controller is None:
            from src.monkey_brain.kernel.society.actor_lifecycle_controller import ActorLifecycleController
            self._lifecycle_controller = ActorLifecycleController(self)
        return self._lifecycle_controller

    # ── Actor Scheduler: node registry ──────────────────────────────────
    # (kernel/society/actor_scheduler.py::ExecutionNode/ActorScheduler). A
    # dedicated hash, deliberately separate from the actor registry hash
    # above: a node is compute/execution location, never an actor — the
    # scheduler's central invariant (ACTOR IDENTITY != ACTOR LOCATION)
    # means these two registries must never be conflated into one record.

    _NODES_HASH_KEY = "monkeybrain:nodes:hash"

    # ── Event-driven reconciliation queue ───────────────────────────────
    # (Horizontal Scheduler Scaling, Section 26/27): "prefer event-driven
    # scheduling... do not implement a scheduler loop that repeatedly
    # scans every Actor at scale." This is the queue every state-changing
    # write below (register_actor, set_actor_desired_state,
    # set_actor_desired_node) pushes into — the reconciliation loop's
    # FAST path drains it with bounded concurrency (Section 25:
    # backpressure) every couple of seconds, touching only actors that
    # actually changed. The existing full-table reconcile_all() sweep
    # remains, at a much longer interval, purely as the correctness
    # backstop Section 27 explicitly allows ("may exist... but must not
    # be the primary scaling mechanism") — for the one case the queue
    # can't precisely target: a node's capacity freeing up (register_node/
    # heartbeat_node) doesn't know WHICH previously-unschedulable actors
    # might now fit, so it isn't (and can't cheaply be) enqueued
    # precisely; the backstop sweep is what eventually reschedules them.
    # A plain Redis LIST, not a priority queue or a new distributed
    # database — LPUSH/LPOP is already atomic server-side, and duplicate
    # entries for a hot actor are harmless (reconcile() is already an
    # idempotent, cheap-read fast path for an already-settled actor), so
    # no dedup bookkeeping is needed.
    _RECONCILE_QUEUE_KEY = "monkeybrain:reconcile:queue"

    def _enqueue_reconciliation(self, actor_id: str) -> None:
        """Never raises, never blocks the caller on failure — a lost
        enqueue only means this one change waits for the next full-sweep
        backstop instead of the fast path, not a correctness loss."""
        if self._redis is None or not actor_id:
            return
        try:
            self._redis.rpush(self._RECONCILE_QUEUE_KEY, actor_id)
        except Exception as exc:
            logger.debug("_enqueue_reconciliation(%r) failed (non-fatal): %s", actor_id, exc)

    def _drain_reconcile_queue_batch(self, max_items: int) -> list[str]:
        """Pop up to max_items actor_ids in one round trip. redis-py's
        LPOP-with-count (Redis >= 6.2) is already atomic server-side —
        two concurrent reconciler processes draining the same queue never
        receive overlapping items, the natural "Scheduler Pool" work-
        distribution property Section 6 asks for, with zero new
        infrastructure."""
        if self._redis is None:
            return []
        try:
            items = self._redis.lpop(self._RECONCILE_QUEUE_KEY, max_items)
        except Exception as exc:
            logger.debug("_drain_reconcile_queue_batch failed (non-fatal): %s", exc)
            return []
        return list(items) if items else []

    _RESERVE_NODE_CAPACITY_SCRIPT = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then
    return -1
end
local node = cjson.decode(raw)
local delta = tonumber(ARGV[2])
local capacity = tonumber(node['capacity']) or 0
local current = tonumber(node['current_actor_count']) or 0
local new_count = current + delta
if delta > 0 and new_count > capacity then
    return -2
end
if new_count < 0 then
    new_count = 0
end
node['current_actor_count'] = new_count
node['updated_at'] = tonumber(ARGV[3])
redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(node))
return new_count
"""
    """Atomic reserve/release of one unit of a node's capacity (Actor
    Scheduler spec, Section 17-18: concurrency safety for simultaneous
    scheduling decisions without an unnecessary distributed lock).
    Without this running server-side, two concurrent schedule() calls for
    two different actors could each read the same current_actor_count,
    both decide the node has room, and both write back count+1 — silently
    losing one increment and over-allocating the node. A plain HGET/HSET
    round-trip from Python has exactly that race; EVAL makes the
    read-check-write atomic, the same reasoning _RELEASE_LOCK_IF_OWNER_
    SCRIPT and _DRAIN_INBOX_SCRIPT above already document. Returns -1 if
    the node is unknown, -2 if reserving would exceed capacity, otherwise
    the new count. Deliberately NOT the source of truth for a node's
    actual load — heartbeat_node()'s own len(sr.all_actors()) recount
    (the reconciliation loop, every interval) periodically overwrites
    this with ground truth, so any transient reservation drift self-heals
    rather than accumulating forever."""
    _NODE_STALE_SECONDS = float(os.getenv("SCHEDULER_NODE_STALE_SECONDS", "600"))
    """A node whose heartbeat hasn't refreshed in this long is treated as
    NodeHealth.UNKNOWN regardless of its last self-reported health — the
    scheduler's own analog of _ACTOR_STALE_SECONDS above.

    Conformance-test finding (live deployment run): this MUST stay
    comfortably larger than the interval of whatever actually sends the
    heartbeat — _actor_lifecycle_reconciliation_loop's backstop sweep,
    default 300s (ACTOR_LIFECYCLE_RECONCILE_INTERVAL), not the 60s
    figure this comment previously assumed from before the Horizontal
    Scheduler Scaling pass raised that default. A threshold smaller than
    (or too close to) the heartbeat interval means every node
    predictably goes UNKNOWN for the gap between heartbeats, and —
    combined with the heartbeat_node() bug fixed alongside this comment
    — could turn one transient gap into a permanently stuck UNKNOWN.
    Default 600s = 2x the 300s backstop interval, the same safety-margin
    reasoning _ACTOR_STALE_SECONDS already uses relative to its own
    heartbeat source."""

    def register_node(self, node: "ExecutionNode") -> None:
        """Durably record one execution node — called by a process at
        boot (register_self_as_node) or by an external heartbeat/agent
        for a non-PlanetaryRuntime node (an edge device, a plain worker).
        Never raises; a failed write degrades to the in-memory fallback
        (this process's own view only)."""
        payload = node.to_dict()
        if self._redis is not None:
            try:
                self._redis.hset(self._NODES_HASH_KEY, node.node_id, json.dumps(payload))
                return
            except Exception as exc:
                logger.warning("register_node(%r) Redis write failed: %s", node.node_id, exc)
        self._node_registry_fallback[node.node_id] = payload

    def deregister_node(self, node_id: str) -> None:
        """Remove a node from the registry — e.g. a clean shutdown. A
        node that simply crashes is NOT expected to call this; it is
        instead detected via heartbeat staleness (list_nodes/get_node),
        exactly like actor crash detection above."""
        if self._redis is not None:
            try:
                self._redis.hdel(self._NODES_HASH_KEY, node_id)
            except Exception as exc:
                logger.debug("deregister_node(%r) Redis delete failed: %s", node_id, exc)
        self._node_registry_fallback.pop(node_id, None)

    def heartbeat_node(self, node_id: str, *, current_actor_count: int | None = None) -> None:
        """Refresh a node's updated_at (and, when known, its current
        actor count) without requiring the full ExecutionNode record to
        be reconstructed by the caller — the node-registry analog of
        _save_actor's cheap per-tick refresh.

        Conformance-test finding (live deployment run): this MUST set
        reported_health back to HEALTHY explicitly, not copy whatever
        get_node() just computed. get_node()/_node_with_computed_health
        return a COMPUTED UNKNOWN once a node has gone stale even once
        (e.g. the gap between _NODE_STALE_SECONDS and the backstop sweep
        interval that calls this method) — copying that computed value
        back into the persisted record turns one transient staleness gap
        into a PERMANENTLY stuck UNKNOWN, since every subsequent
        heartbeat then reads back its own frozen UNKNOWN and re-persists
        it, and reported_health only gets recomputed as UNKNOWN when it
        was persisted HEALTHY (never the reverse). A caller that reaches
        this method at all is, by definition, still alive right now —
        that is exactly what a heartbeat means."""
        node = self.get_node(node_id)
        if node is None:
            return
        from src.monkey_brain.kernel.society.actor_scheduler import ExecutionNode, NodeHealth
        refreshed = ExecutionNode(
            node_id=node.node_id, node_class=node.node_class, capacity=node.capacity,
            current_actor_count=node.current_actor_count if current_actor_count is None else current_actor_count,
            capabilities=node.capabilities, region=node.region,
            reported_health=NodeHealth.HEALTHY, updated_at=time.time(),
        )
        self.register_node(refreshed)

    def _reserve_node_capacity(self, node_id: str, delta: int) -> int | None:
        """Atomically add `delta` (+1 to reserve a placement, -1 to
        release one) to a node's current_actor_count. Returns the new
        count, or None if the node is unknown or (delta > 0) reserving
        would exceed capacity — the caller must treat None as "this
        placement did not happen," not retry-forever. See
        _RESERVE_NODE_CAPACITY_SCRIPT above for why this must run
        atomically rather than as a Python read-then-write."""
        if self._redis is not None:
            try:
                result = int(self._redis.eval(
                    self._RESERVE_NODE_CAPACITY_SCRIPT, 1, self._NODES_HASH_KEY,
                    node_id, delta, time.time(),
                ))
                return None if result < 0 else result
            except Exception as exc:
                logger.warning("_reserve_node_capacity(%r, %r) Redis eval failed: %s", node_id, delta, exc)
                return None
        raw = self._node_registry_fallback.get(node_id)
        if raw is None:
            return None
        capacity = int(raw.get("capacity", 0))
        current = int(raw.get("current_actor_count", 0))
        new_count = current + delta
        if delta > 0 and new_count > capacity:
            return None
        new_count = max(0, new_count)
        raw["current_actor_count"] = new_count
        raw["updated_at"] = time.time()
        return new_count

    def get_node(self, node_id: str) -> "ExecutionNode | None":
        from src.monkey_brain.kernel.society.actor_scheduler import ExecutionNode

        raw: dict[str, Any] | None = None
        if self._redis is not None:
            try:
                raw_json = self._redis.hget(self._NODES_HASH_KEY, node_id)
                if raw_json:
                    raw = json.loads(raw_json)
            except Exception as exc:
                logger.debug("get_node(%r) Redis read failed: %s", node_id, exc)
        if raw is None:
            raw = self._node_registry_fallback.get(node_id)
        if raw is None:
            return None
        return self._node_with_computed_health(ExecutionNode.from_dict(raw))

    def list_nodes(self) -> tuple["ExecutionNode", ...]:
        """Every registered node, health recomputed for staleness at read
        time (never trusts a stored "healthy" flag past its heartbeat
        window) — the same "observed can be more current than persisted"
        pattern used throughout this codebase's registries."""
        from src.monkey_brain.kernel.society.actor_scheduler import ExecutionNode

        raws: dict[str, dict[str, Any]] = {}
        if self._redis is not None:
            try:
                hash_data = self._redis.hgetall(self._NODES_HASH_KEY)
                for node_id, raw_json in hash_data.items():
                    try:
                        raws[node_id] = json.loads(raw_json)
                    except (TypeError, ValueError):
                        continue
            except Exception as exc:
                logger.debug("list_nodes() Redis read failed: %s", exc)
        for node_id, raw in self._node_registry_fallback.items():
            raws.setdefault(node_id, raw)
        return tuple(
            self._node_with_computed_health(ExecutionNode.from_dict(raw))
            for raw in raws.values()
        )

    def _node_with_computed_health(self, node: "ExecutionNode") -> "ExecutionNode":
        from src.monkey_brain.kernel.society.actor_scheduler import ExecutionNode, NodeHealth

        if node.reported_health == NodeHealth.HEALTHY and (time.time() - node.updated_at) > self._NODE_STALE_SECONDS:
            return ExecutionNode(
                node_id=node.node_id, node_class=node.node_class, capacity=node.capacity,
                current_actor_count=node.current_actor_count, capabilities=node.capabilities,
                region=node.region, reported_health=NodeHealth.UNKNOWN, updated_at=node.updated_at,
            )
        return node

    def register_self_as_node(self, *, node_class: "NodeClass | None" = None, capacity: int | None = None,
                              capabilities: tuple[str, ...] | None = None, region: str | None = None) -> None:
        """Self-register this process as an execution node, using its own
        already-stable self._node_id (the same identity actor-registry
        records and leases already key on) — called once at boot
        (kernel.py) and refreshed by the lifecycle reconciliation loop's
        own heartbeat, never by the scheduler itself (Section 5: the
        scheduler only ever reads the node registry, it does not
        maintain node liveness). Any parameter left unset falls back to
        an env var (SCHEDULER_NODE_CLASS/_CAPACITY/_CAPABILITIES/_REGION)
        so an operator can describe this deployment's node without a code
        change — SCHEDULER_NODE_CAPABILITIES is comma-separated."""
        from src.monkey_brain.kernel.society.actor_scheduler import ExecutionNode, NodeClass

        if node_class is None:
            try:
                node_class = NodeClass(os.getenv("SCHEDULER_NODE_CLASS", "cloud"))
            except ValueError:
                node_class = NodeClass.CLOUD
        if capacity is None:
            capacity = int(os.getenv("SCHEDULER_NODE_CAPACITY", "1000"))
        if capabilities is None:
            raw_caps = os.getenv("SCHEDULER_NODE_CAPABILITIES", "")
            capabilities = tuple(c.strip() for c in raw_caps.split(",") if c.strip())
        if region is None:
            region = os.getenv("SCHEDULER_NODE_REGION", "")

        current_actor_count = sum(len(sr.all_actors()) for sr in self._societies.values())
        self.register_node(ExecutionNode(
            node_id=self._node_id, node_class=node_class, capacity=capacity,
            current_actor_count=current_actor_count, capabilities=capabilities, region=region,
        ))

    @property
    def scheduler(self) -> "ActorScheduler":
        """The Actor Scheduler (Actor Scheduler spec, Section 5) —
        decides WHERE an actor executes, never what it does or thinks.
        Lazily constructed, same composition pattern as self.lifecycle."""
        if self._scheduler is None:
            from src.monkey_brain.kernel.society.actor_scheduler import ActorScheduler
            self._scheduler = ActorScheduler(self)
        return self._scheduler

    @property
    def kubernetes_provisioner(self) -> "Any":
        """Gap Remediation audit fix: the Lifecycle Controller's one
        additional action for a genuinely UNSCHEDULABLE actor (no
        healthy node exists at all) — see kernel/society/
        kubernetes_provisioner.py. Lazily constructed, same composition
        pattern as self.scheduler/self.lifecycle; opt-in and inert
        unless KUBERNETES_PROVISIONING_ENABLED=true AND kubectl is on
        PATH."""
        if self._kubernetes_provisioner is None:
            from src.monkey_brain.kernel.society.kubernetes_provisioner import KubernetesProvisioner
            self._kubernetes_provisioner = KubernetesProvisioner(self)
        return self._kubernetes_provisioner

    @property
    def edge_provisioner(self) -> "Any":
        """Edge Deployment: the Lifecycle Controller's action for an
        actor scheduled onto a registered EDGE/DEVICE/ROBOT-class node
        with nothing resident there yet — see kernel/society/
        edge_provisioner.py. Lazily constructed, same composition
        pattern as self.kubernetes_provisioner; opt-in and inert unless
        EDGE_PROVISIONING_ENABLED=true."""
        if self._edge_provisioner is None:
            from src.monkey_brain.kernel.society.edge_provisioner import EdgeProvisioner
            self._edge_provisioner = EdgeProvisioner(self)
        return self._edge_provisioner

    # ── Actor Scheduler: desired placement + placement requirements ────
    # Deliberately separate keys from desired STATE above: "should this
    # actor be RUNNING" and "where should it run" are independent
    # questions the Lifecycle Controller and Scheduler each own one of —
    # conflating them into a single record would recreate exactly the
    # coupling the Deployment Architecture work spent this session
    # untangling for actor state itself.

    _ACTOR_DESIRED_NODE_KEY_PREFIX = "monkeybrain:actor:desired_node:"
    _ACTOR_PLACEMENT_REQ_KEY_PREFIX = "monkeybrain:actor:placement_requirements:"

    def set_actor_desired_node(self, actor_id: str, node_id: str) -> None:
        current = self.get_actor_desired_node(actor_id)
        payload = {"node_id": node_id, "set_at": time.time()}
        if self._redis is not None:
            try:
                self._redis.set(f"{self._ACTOR_DESIRED_NODE_KEY_PREFIX}{actor_id}", json.dumps(payload))
                if self._should_enqueue_placement_change(current, node_id):
                    self._enqueue_reconciliation(actor_id)
                return
            except Exception as exc:
                logger.warning("set_actor_desired_node(%r) Redis write failed: %s", actor_id, exc)
        self._desired_node_fallback[actor_id] = payload
        if self._should_enqueue_placement_change(current, node_id):
            self._enqueue_reconciliation(actor_id)

    def _should_enqueue_placement_change(self, current: str, node_id: str) -> bool:
        """Enqueue when placement changes or targets a remote node."""
        return current != node_id and (bool(current) or node_id != self._node_id)

    def get_actor_desired_node(self, actor_id: str) -> str:
        """"" means "never scheduled" — no placement requirement has ever
        been expressed for this actor, matching today's implicit
        single-node behavior for every actor that never opts in."""
        payload: dict[str, Any] | None = None
        if self._redis is not None:
            try:
                raw = self._redis.get(f"{self._ACTOR_DESIRED_NODE_KEY_PREFIX}{actor_id}")
                if raw:
                    payload = json.loads(raw)
            except Exception as exc:
                logger.debug("get_actor_desired_node(%r) Redis read failed: %s", actor_id, exc)
        if payload is None:
            payload = self._desired_node_fallback.get(actor_id)
        return (payload or {}).get("node_id", "")

    def set_actor_placement_requirements(self, actor_id: str, requirements: "ActorPlacementRequirements") -> None:
        payload = requirements.to_dict()
        if self._redis is not None:
            try:
                self._redis.set(f"{self._ACTOR_PLACEMENT_REQ_KEY_PREFIX}{actor_id}", json.dumps(payload))
                return
            except Exception as exc:
                logger.warning("set_actor_placement_requirements(%r) Redis write failed: %s", actor_id, exc)
        self._placement_requirements_fallback[actor_id] = payload

    def get_actor_placement_requirements(self, actor_id: str) -> "ActorPlacementRequirements":
        from src.monkey_brain.kernel.society.actor_scheduler import ActorPlacementRequirements

        payload: dict[str, Any] | None = None
        if self._redis is not None:
            try:
                raw = self._redis.get(f"{self._ACTOR_PLACEMENT_REQ_KEY_PREFIX}{actor_id}")
                if raw:
                    payload = json.loads(raw)
            except Exception as exc:
                logger.debug("get_actor_placement_requirements(%r) Redis read failed: %s", actor_id, exc)
        if payload is None:
            payload = self._placement_requirements_fallback.get(actor_id)
        if payload is None:
            return ActorPlacementRequirements()
        return ActorPlacementRequirements.from_dict(payload)

    def suspend_actor_for_migration(self, actor_id: str) -> bool:
        """Checkpoint + locally suspend an actor resident on THIS process
        as the first half of a migration (Actor Scheduler spec, Section
        14) — deliberately reuses ActorLifecycleController's own suspend
        action rather than a parallel implementation, and deliberately
        does NOT change desired_state (the intent stays RUNNING
        throughout a migration; only location changes). A no-op,
        returning False, if the actor isn't resident here — the correct
        outcome when migrate_actor() is invoked from a process other than
        the one currently hosting the actor; that other process's own
        reconcile loop will notice the node_id mismatch and suspend it
        there instead."""
        if self.get_actor_runtime(actor_id) is None:
            return False
        observed = self.observe_actor(actor_id)
        if observed.node_id and observed.node_id != self._node_id:
            # Registry names another owner; deactivate the stale local copy only.
            from src.monkey_brain.kernel.society.domain import ActorStatus
            sr = self._home_society_runtime(actor_id)
            state = sr.get_actor(actor_id) if sr is not None else None
            if state is not None:
                state.is_active = False
                state.status = ActorStatus.SUSPENDED
            return True
        desired = self.get_actor_desired_state(actor_id)
        result = self.lifecycle._do_suspend(actor_id, desired, observed, reason="migrating to a different node")
        return result.succeeded

    _CONTEXT_LIST_KEY = "monkeybrain:context:list"
    _CONTEXT_LEGACY_KEY = "monkeybrain:context"
    _SOCIETY_CONTEXT_LEGACY_KEY = "monkeybrain:society_context"

    def _save_context(self) -> None:
        """Save context events to Redis and event store.

        This is set_on_publish (__init__, ~line 304) — called on EVERY
        SINGLE context_stream.publish(), which register_actor() alone
        calls once per actor. The two full-blob `redis.set()` writes
        below used to rebuild and overwrite up to 10,000 events on every
        one of those calls (confirmed live: this, not _save_actors(),
        was the dominant cost once _save_actors() was fixed — 500 actors
        still took minutes). The event-store block further down already
        got an incremental fix at some point (_context_persisted_version
        tracks "pushed so far", only NEW events since then are written) —
        this applies that same already-established pattern to the two
        `redis.set()` calls that never got it: RPUSH the single
        newly-published event onto a Redis LIST instead of re-serializing
        and overwriting the whole history every time."""
        if not self._redis:
            return
        try:
            cs = self.context_stream
            latest = cs.events(limit=1)
            if latest:
                self._redis.rpush(self._CONTEXT_LIST_KEY, json.dumps(latest[-1].to_dict()))

            for sid, sr in self._societies.items():
                sr_latest = sr.context_stream.events(limit=1)
                if sr_latest:
                    self._redis.rpush(f"{self._CONTEXT_LIST_KEY}:{sid}", json.dumps(sr_latest[-1].to_dict()))

            # Also persist to event store for durable storage
            if self._event_store:
                from src.monkey_brain.persistence.context_event_store import StoredEvent
                # Save events for all societies — only events newer than the
                # last-persisted version. events(limit=100) returns the last
                # 100 events in the stream's whole history, not "events since
                # the last publish", so without this filter every publish
                # (this is on_publish, called on EVERY event) re-pushes up to
                # 100 duplicate entries onto the Redis list.
                for sid, sr in self._societies.items():
                    last_version = self._context_persisted_version.get(sid, 0)
                    new_events = [e for e in sr.context_stream.events(limit=100) if e.version > last_version]
                    for event in new_events:
                        stored = StoredEvent(
                            event_type=event.event_type.value,
                            actor_id=event.actor_id,
                            society_id=sid,
                            description=event.description,
                            payload=event.payload,
                            confidence=event.confidence,
                            provenance=event.provenance,
                            timestamp=event.timestamp,
                            version=event.version,
                        )
                        self._redis.rpush(
                            f"{self._event_store._KEY_PREFIX}:{stored.society_id}",
                            json.dumps(stored.to_dict())
                        )
                    if new_events:
                        self._context_persisted_version[sid] = new_events[-1].version
        except Exception as exc:
            logger.debug("Context save failed: %s", exc)

    def _load_context(self) -> None:
        """Reads the incremental LIST _save_context() now writes (LRANGE) —
        falls back to the legacy single-JSON-blob keys (read-only, never
        written again) so events persisted before that fix stay visible."""
        if not self._redis:
            return
        try:
            from src.monkey_brain.kernel.society.context_stream import ContextEvent

            cs = self.context_stream
            raw_events = self._redis.lrange(self._CONTEXT_LIST_KEY, 0, -1)
            if raw_events:
                for raw in raw_events:
                    cs.publish(ContextEvent.from_dict(json.loads(raw)))
                logger.info("Context loaded: %d events", len(raw_events))
            else:
                legacy = self._redis.get(self._CONTEXT_LEGACY_KEY)
                if legacy:
                    events = json.loads(legacy)
                    for event_data in events:
                        cs.publish(ContextEvent.from_dict(event_data))
                    logger.info("Context loaded (legacy): %d events", len(events))

            for sid in self._societies:
                sr = self._societies[sid]
                raw_sr_events = self._redis.lrange(f"{self._CONTEXT_LIST_KEY}:{sid}", 0, -1)
                if raw_sr_events:
                    for raw in raw_sr_events:
                        sr.context_stream.publish(ContextEvent.from_dict(json.loads(raw)))

            soc_data = self._redis.get(self._SOCIETY_CONTEXT_LEGACY_KEY)
            if soc_data:
                society_ctx = json.loads(soc_data)
                for sid, events in society_ctx.items():
                    if sid in self._societies and not self._redis.exists(f"{self._CONTEXT_LIST_KEY}:{sid}"):
                        sr = self._societies[sid]
                        for event_data in events:
                            sr.context_stream.publish(ContextEvent.from_dict(event_data))
                logger.info("Society context loaded (legacy)")
        except Exception as exc:
            logger.warning("Context load failed: %s", exc)

    def _save_relationships(self) -> None:
        """Persist self._relationships (MEMBER_OF/HOSTED_BY edges + the
        bounded, now-serializable RelationshipHistoryEntry audit trail —
        see kernel/relationships/__init__.py's to_dict()/from_dict()).
        Same whole-snapshot-overwrite pattern as _save_societies/_save_world."""
        if not self._redis:
            return
        try:
            self._redis.set("monkeybrain:relationships", json.dumps(self._relationships.to_dict()))
        except Exception as exc:
            logger.debug("Relationships save failed: %s", exc)

    def _load_relationships(self) -> None:
        if not self._redis:
            return
        try:
            data = self._redis.get("monkeybrain:relationships")
            if data:
                self._relationships = RelationshipGraph.from_dict(json.loads(data))
        except Exception as exc:
            logger.warning("Relationships load failed: %s", exc)

    def _save_geography(self) -> None:
        """Persist the full physical-geography hierarchy (self._geo_registry
        — every Planet/Country/.../Space entity, GeographicEntity.to_dict()'s
        shape). Same whole-snapshot-overwrite pattern as _save_societies/
        _save_world. Geography was the one subsystem with no persistence at
        all — every other registry here (world, actors, societies,
        relationships, context) already survives a restart; a tracked
        geographic entity (a created County/City, a hosted society) must
        too, not silently vanish on the next boot."""
        try:
            entities = [e.to_dict() for e in self._geo_registry.all()]
            if self._redis:
                self._redis.set("monkeybrain:geography", json.dumps(entities))
            self._edge_local_put("geography", "all", {"entities": entities})
        except Exception as exc:
            logger.debug("Geography save failed: %s", exc)

    def _load_geography(self) -> None:
        try:
            if self._redis:
                data = self._redis.get("monkeybrain:geography")
                entities = json.loads(data) if data else []
            else:
                edge_value = self._edge_local_get("geography", "all")
                entities = edge_value.get("entities", []) if edge_value is not None else []
            for entity_data in entities:
                self._geo_registry.register_from_dict(entity_data)
        except Exception as exc:
            logger.warning("Geography load failed: %s", exc)

    def _save_societies(self) -> None:
        try:
            societies = []
            for society_id, sr in self._societies.items():
                societies.append({
                    "society_id": society_id,
                    "name": sr.society.name,
                    "description": sr.society.description,
                    "society_type": sr.society.society_type,
                    "activation_tags": list(sr.society.activation_tags),
                    "always_active": sr.society.always_active,
                    "metadata": dict(sr.society.metadata),
                    "shared_goals": list(sr.society.shared_goals),
                    "policies": list(sr.society.policies),
                    "governance_policies": [
                        {
                            "policy_id": p.policy_id, "name": p.name, "description": p.description,
                            "policy_type": p.policy_type.value, "level": p.level.value,
                            "rules": list(p.rules), "scope": p.scope,
                            "enabled": p.enabled, "priority": p.priority,
                        }
                        for p in sr.governance.policies(enabled_only=False)
                    ],
                    "permissions": [
                        {
                            "permission_id": p.permission_id, "actor_id": p.actor_id,
                            "resource": p.resource, "action": p.action,
                            "granted_by": p.granted_by, "expires_at": p.expires_at,
                        }
                        for p in sr.governance.all_permissions()
                    ],
                    # SocietyGovernanceEngine cross-process gap: policies/
                    # permissions above were already persisted (this same
                    # blob), but trust_records/safety_constraints/audit_log
                    # had NO persisted fields at all before this fix -- a
                    # process restart, or a second process reading this
                    # same Redis key, saw none of them.
                    "trust_records": [
                        {
                            "actor_id": t.actor_id, "trust_score": t.trust_score,
                            "evidence_count": t.evidence_count, "factors": dict(t.factors),
                        }
                        for t in sr.governance.all_trust_records()
                    ],
                    "safety_constraints": [
                        {
                            "constraint_id": c.constraint_id, "name": c.name,
                            "description": c.description, "rule": c.rule,
                            "severity": c.severity, "applies_to": list(c.applies_to),
                        }
                        for c in sr.governance.safety_constraints()
                    ],
                    # Bounded, not the full unbounded history: matches
                    # audit_log()'s own existing limit=100 read convention.
                    # A full permanent audit trail is a heavier concern
                    # this dormant-in-production governance layer does not
                    # need to solve to close the cross-process gap; a
                    # bounded recent window is real, durable, and correct
                    # for what audit_log() itself already promises callers.
                    "audit_log": [
                        {
                            "entry_id": e.entry_id, "actor_id": e.actor_id, "action": e.action,
                            "policy_id": e.policy_id, "compliance_status": e.compliance_status.value,
                            "details": e.details, "timestamp": e.timestamp,
                        }
                        for e in sr.governance.audit_log(limit=200)
                    ],
                })
            if self._redis:
                self._redis.set("monkeybrain:societies", json.dumps(societies))
            self._edge_local_put("society", "all", {"societies": societies})
        except Exception as exc:
            logger.debug("Societies save failed: %s", exc)

    def _load_societies(self) -> None:
        try:
            if self._redis:
                data = self._redis.get("monkeybrain:societies")
                societies_raw = json.loads(data) if data else None
            else:
                edge_value = self._edge_local_get("society", "all")
                societies_raw = edge_value.get("societies") if edge_value is not None else None
            if societies_raw:
                    import dataclasses
                    from src.monkey_brain.kernel.society.governance import (
                        GovernancePolicy, PolicyType, GovernanceLevel, Permission,
                        TrustRecord, SafetyConstraint, AuditEntry, ComplianceStatus,
                    )
                    societies = societies_raw
                    for soc_data in societies:
                        sid = soc_data.get("society_id", "")
                        if sid and sid not in self._societies:
                            from src.monkey_brain.kernel.society.domain import Society
                            society = Society(
                                society_id=sid,
                                name=soc_data.get("name", ""),
                                description=soc_data.get("description", ""),
                                society_type=soc_data.get("society_type", "generic"),
                                activation_tags=tuple(soc_data.get("activation_tags", [])),
                                always_active=soc_data.get("always_active", False),
                                metadata=dict(soc_data.get("metadata", {})),
                                shared_goals=tuple(soc_data.get("shared_goals", [])),
                                policies=tuple(soc_data.get("policies", [])),
                            )
                            sr = SocietyRuntime(society, strategic_runtime=self._game_theory)
                            sr._world = self._world
                            sr._observation_provider._world = self._world
                            self._societies[sid] = sr
                        elif sid and sid in self._societies:
                            sr = self._societies[sid]
                            sr._society = dataclasses.replace(
                                sr._society,
                                society_type=soc_data.get("society_type", sr._society.society_type),
                                activation_tags=tuple(soc_data.get("activation_tags", [])) or sr._society.activation_tags,
                                always_active=soc_data.get("always_active", sr._society.always_active),
                                metadata=dict(soc_data.get("metadata", sr._society.metadata)),
                                shared_goals=tuple(soc_data.get("shared_goals", [])),
                                policies=tuple(soc_data.get("policies", [])),
                            )
                        sr = self._societies[sid]
                        for gp_data in soc_data.get("governance_policies", []):
                            try:
                                policy = GovernancePolicy(
                                    policy_id=gp_data.get("policy_id", ""),
                                    name=gp_data.get("name", ""),
                                    description=gp_data.get("description", ""),
                                    policy_type=PolicyType(gp_data.get("policy_type", "guideline")),
                                    level=GovernanceLevel(gp_data.get("level", "global")),
                                    rules=tuple(gp_data.get("rules", [])),
                                    scope=gp_data.get("scope", ""),
                                    enabled=gp_data.get("enabled", True),
                                    priority=gp_data.get("priority", 0),
                                )
                                sr.governance.add_policy(policy)
                                # Same versioned-world mirroring as the live
                                # POST /societies/{id}/governance-policies
                                # route (api/routes/societies.py) -- keeps
                                # SharedWorld.policies() populated across a
                                # process restart's rehydration too, not
                                # only for policies added after boot.
                                sr.world.record_policy(
                                    policy_id=policy.policy_id, name=policy.name,
                                    description=policy.description, rules=policy.rules,
                                    scope=policy.scope,
                                )
                            except ValueError:
                                continue
                        for perm_data in soc_data.get("permissions", []):
                            permission = Permission(
                                permission_id=perm_data.get("permission_id", ""),
                                actor_id=perm_data.get("actor_id", ""),
                                resource=perm_data.get("resource", ""),
                                action=perm_data.get("action", ""),
                                granted_by=perm_data.get("granted_by", ""),
                                expires_at=perm_data.get("expires_at", 0.0),
                            )
                            sr.governance.grant_permission(permission)
                        for tr_data in soc_data.get("trust_records", []):
                            sr.governance.restore_trust_record(TrustRecord(
                                actor_id=tr_data.get("actor_id", ""),
                                trust_score=tr_data.get("trust_score", 0.5),
                                evidence_count=tr_data.get("evidence_count", 0),
                                factors=dict(tr_data.get("factors", {})),
                            ))
                        for sc_data in soc_data.get("safety_constraints", []):
                            sr.governance.add_safety_constraint(SafetyConstraint(
                                constraint_id=sc_data.get("constraint_id", ""),
                                name=sc_data.get("name", ""), description=sc_data.get("description", ""),
                                rule=sc_data.get("rule", ""), severity=sc_data.get("severity", "high"),
                                applies_to=tuple(sc_data.get("applies_to", [])),
                            ))
                        # Oldest-first, same order they were saved in, so
                        # restore_audit_entry's append-only log stays
                        # chronological (audit_log()'s own log[-limit:]
                        # slicing assumes this).
                        for ae_data in soc_data.get("audit_log", []):
                            try:
                                compliance_status = ComplianceStatus(ae_data.get("compliance_status", "compliant"))
                            except ValueError:
                                compliance_status = ComplianceStatus.COMPLIANT
                            sr.governance.restore_audit_entry(AuditEntry(
                                entry_id=ae_data.get("entry_id", ""), actor_id=ae_data.get("actor_id", ""),
                                action=ae_data.get("action", ""), policy_id=ae_data.get("policy_id", ""),
                                compliance_status=compliance_status, details=ae_data.get("details", ""),
                                timestamp=ae_data.get("timestamp", 0.0),
                            ))
                    logger.info("Societies loaded: %d", len(societies))
        except Exception as exc:
            logger.warning("Societies load failed: %s", exc)

    @property
    def society(self) -> Society:
        return self._society_runtime.society

    @property
    def world(self) -> SharedWorld:
        return self._world_model.semantic_world

    @property
    def world_model(self) -> WorldModelRuntime:
        """Canonical Planetary world facade."""
        return self._world_model

    @property
    def context_stream(self) -> SocietyContextStream:
        return self._society_runtime.context_stream

    @property
    def governance(self) -> SocietyGovernanceEngine:
        return self._society_runtime.governance

    @property
    def collective_learning(self) -> CollectiveLearningEngine:
        return self._society_runtime.collective_learning

    # ── Actor Registration ───────────────────────────────────────────────

    @property
    def default_bootstrap_space_id(self) -> str | None:
        """Governance/Membership/Registration Model refactor: the Space
        register_actor() hosts an Actor's home Society at when no
        home_space_id is given. Configurable via the constructor, the
        PLANETARY_DEFAULT_BOOTSTRAP_SPACE_ID env var, or
        set_default_bootstrap_space() — never hard-coded."""
        return self._default_bootstrap_space_id

    def set_default_bootstrap_space(self, space_id: str) -> None:
        """Reconfigure the default bootstrap Space. Raises ValueError
        (rather than silently accepting an invalid id) if space_id doesn't
        resolve to a real Space — the whole point of this Space is to be a
        valid host for register_actor(), so an invalid one here would
        just move the invariant-violation to registration time instead of
        catching it now."""
        space = self._geo_registry.get(space_id)
        if space is None or space.entity_type != GeographicEntityType.SPACE:
            raise ValueError(f"{space_id!r} does not resolve to a real Space")
        self._default_bootstrap_space_id = space_id

    def register_actor(self, profile: ActorProfile,
                       cognitive_stages: dict[str, StageFn] | None = None,
                       actor: Any = None, home_space_id: str | None = None,
                       society_id: str | None = None) -> ActorRuntimeState:
        """The single canonical actor-registration workflow — Registration
        Entry Points (Governance/Membership/Registration Model refactor):
        "the world must expose a single canonical actor registration
        workflow... regardless of which public API is used, actor
        registration must enforce the same invariants and produce the same
        world state." Every public entry point that creates a new Actor
        within a PlanetaryRuntime (REST routes, CLI, importers) MUST call
        this method rather than SocietyRuntime.register_actor() directly —
        that lower-level method has no geography/PresenceTimeline
        awareness at all (by design: SocietyRuntime is usable standalone,
        with no PlanetaryRuntime, for callers that don't need physical
        presence) and so cannot enforce these invariants itself.

        Registers profile as a new Actor, permanently affiliated with
        society_id (self.society — this PlanetaryRuntime's own default
        Society — if society_id is omitted) via a real, explicitly stored
        PERMANENT membership (see SocietyRuntime.register_actor()'s own
        membership_registry.add() call), not only societies joined later
        via join_society().

        Invariant Enforcement: successful completion of this method
        GUARANTEES, regardless of how registration was initiated —
        execute_actor_request() works immediately afterward with no extra
        setup calls:
          1. the target Society exists (LookupError if society_id doesn't
             resolve to a Society this PlanetaryRuntime manages);
          2. the target Society is associated with at least one Space
             (host_society(home_space_id or default_bootstrap_space_id,
             target_society_id) — hosting an ALREADY-hosted Society again
             just moves it, so this only actually changes anything when
             home_space_id is explicitly given or nothing was hosted yet);
          3. the Actor has exactly one current Space (moved there via
             move_actor(), giving it a PresenceTimeline entry immediately
             — "Actors have exactly one current Space" is unconditional,
             not "may optionally have one");
          4. effective memberships can be computed immediately
             (effective_societies() only ever reads permanent + temporary
             state that's already fully established by the time this
             method returns — nothing further to compute).

        Fails atomically: an unknown society_id, an invalid home_space_id,
        or no default_bootstrap_space_id when none was given and the
        target Society isn't hosted anywhere yet, all raise BEFORE the
        Actor is constructed — never a half-registered Actor with no
        valid Space.
        """
        target_runtime = self._society_runtime if society_id is None else self.get_society_runtime(society_id)
        if target_runtime is None:
            raise LookupError(f"Society {society_id!r} not found")
        target_society_id = target_runtime.society.society_id
        actor_id = profile.identity.actor_id

        if home_space_id is not None:
            space = self._geo_registry.get(home_space_id)
            if space is None or space.entity_type != GeographicEntityType.SPACE:
                raise ValueError(f"home_space_id {home_space_id!r} does not resolve to a real Space")
            self.host_society(home_space_id, target_society_id)
            effective_home_space_id = home_space_id
        else:
            existing_spaces = self._geo_registry.spaces_for_society(target_society_id)
            if existing_spaces:
                effective_home_space_id = existing_spaces[0].entity_id
            else:
                if self._default_bootstrap_space_id is None:
                    raise RuntimeError(
                        f"Cannot register Actor {actor_id!r}: Society {target_society_id!r} has no "
                        "associated Space and no default_bootstrap_space_id is configured — pass "
                        "home_space_id explicitly, or configure PlanetaryRuntime's "
                        "default_bootstrap_space_id (constructor arg, "
                        "PLANETARY_DEFAULT_BOOTSTRAP_SPACE_ID env var, or set_default_bootstrap_space())."
                    )
                self.host_society(self._default_bootstrap_space_id, target_society_id)
                effective_home_space_id = self._default_bootstrap_space_id

        # Defensive, not redundant: catches a hosting bug HERE, before the
        # Actor is constructed, rather than leaving a half-valid world.
        self._geo_registry.validate_society_has_space(target_society_id)

        state = target_runtime.register_actor(profile, cognitive_stages, actor=actor)

        if not self.move_actor(actor_id, effective_home_space_id):
            # Should be unreachable — effective_home_space_id was just
            # validated as a real Space above — but "fail without partial
            # changes" means unwinding the registration rather than
            # returning an Actor that violates "exactly one current Space."
            # Unregisters from target_runtime directly (not self.
            # unregister_actor(), which only ever knows how to remove from
            # self._society_runtime, the HOME society specifically).
            target_runtime.unregister_actor(actor_id)
            raise RuntimeError(
                f"Failed to place Actor {actor_id!r} at its home Space {effective_home_space_id!r}"
            )

        self.context_stream.publish(ContextEvent(
            event_type=ContextEventType.WORLD_UPDATE,
            actor_id=actor_id,
            description=f"Actor registered: {profile.identity.name}",
            payload=profile.to_dict(),
            provenance="api:actors",
        ))
        # _save_actor(), not _save_actors(): the hot path for bulk/scale
        # registration — see _save_actor()'s docstring for the O(n^2) bug
        # this replaced (confirmed live: 200 actors via the old
        # _save_actors() call here took 222s and was still climbing).
        self._save_actor(state, target_society_id)
        self._subscribe_actor_inbox(actor_id, profile)
        # Event-driven scheduling (Section 26): a newly registered Actor
        # needs a start decision -- wake the fast reconciliation path
        # instead of waiting for it to be discovered by the next full
        # backstop sweep.
        self._enqueue_reconciliation(actor_id)
        return state

    def _subscribe_actor_inbox(self, actor_id: str, profile: Any) -> None:
        """Multi-Actor Execution Handoff: real per-actor NATS inbox
        (kernel/domains/grocery.py::subscribe_actor_inbox), so
        AskActorCapability's real point-to-point request/reply has a real
        subscriber to answer it. Called from every real place an Actor
        becomes live — this method (the canonical registration path) AND
        the boot-time actor-reload loop below (_load_actors, which calls
        SocietyRuntime.register_actor directly and never reaches this
        method at all) — not just once, since either path alone would
        leave some actors with no live inbox.

        Fire-and-forget (asyncio.create_task): register_actor() itself
        isn't async, and turning it async would ripple through every
        caller; a dropped subscription here just means this one actor's
        AskActorCapability calls fall back to the in-process degrade path
        (grocery.py's own AskActorCapability.handle()), never fatal."""
        try:
            import asyncio
            from src.monkey_brain.kernel.domains.grocery import subscribe_actor_inbox
            goals = list(profile.goals) if getattr(profile, "goals", None) else []
            name = profile.identity.name
            actor_role = f"{name}, whose responsibilities include: {', '.join(goals)}" if goals else name
            task = asyncio.create_task(subscribe_actor_inbox(self, actor_id, actor_role))
            self._inbox_subscription_tasks.add(task)
            task.add_done_callback(self._inbox_subscription_tasks.discard)
        except Exception:
            logger.debug("_subscribe_actor_inbox: suppressed exception for %s", actor_id, exc_info=True)

    async def wait_for_inbox_subscriptions(self) -> None:
        """Wait for all subscriptions scheduled during startup/registration."""
        tasks = tuple(self._inbox_subscription_tasks)
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        failures = [r for r in results if isinstance(r, BaseException) or r is False]
        if failures:
            raise RuntimeError(f"Actor inbox subscription setup failed: {failures[0]}")

    def unregister_actor(self, actor_id: str) -> bool:
        """Unregister an actor's cognition from wherever it currently
        lives. Searches every managed society (self._societies), not only
        self._society_runtime — register_actor()'s society_id parameter
        can point anywhere this PlanetaryRuntime manages, so this method
        previously silently no-op'd for any actor registered against a
        non-default society_id. DELETE /actors/{id} used to work around
        that with its own inline search loop over pr.all_societies(); that
        loop now just calls this method, so the fix lives in one place.

        Checkpoint-before-terminate: persists the actor's belief BEFORE
        unregistering — an actor's last real cognitive state must never
        be silently discarded on deletion, regardless of which entry
        point (this method, the Actor Lifecycle Controller's
        terminate_actor, or a future caller) triggers it. Never raises: a
        checkpoint failure degrades to best-effort and unregistration
        still proceeds, matching checkpoint_actor_belief's own fail-soft
        contract — a stuck belief store must not make an actor
        undeletable.
        """
        self.checkpoint_actor_belief(actor_id)
        result = False
        for sr in self._societies.values():
            if sr.unregister_actor(actor_id):
                result = True
                break
        if result:
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                actor_id=actor_id,
                description=f"Actor unregistered: {actor_id}",
                payload={"actor_id": actor_id},
                provenance="api:actors/{id}",
            ))
            if self._redis:
                try:
                    self._redis.hdel(self._ACTORS_HASH_KEY, actor_id)
                except Exception as exc:
                    logger.debug("Actor delete failed for %r: %s", actor_id, exc)
        return result

    def active_actors(self) -> tuple[ActorRuntimeState, ...]:
        return self._society_runtime.active_actors()

    # ── Society Membership (Society as Organizational Context refactor) ──
    # An actor's cognition (ActorRuntimeState) still lives in exactly one
    # "home" SocietyRuntime (register_actor/unregister_actor above,
    # unchanged). Organizational MEMBERSHIP is a separate, many-to-many
    # concept: an actor may be MEMBER_OF any number of societies at once,
    # tracked by self._membership_registry, fully independent of cognition
    # ownership — mirroring how geography HOSTS societies without owning
    # them (kernel/geography/registry.py).

    def _home_society_runtime(self, actor_id: str) -> SocietyRuntime | None:
        return next((sr for sr in self._societies.values() if sr.get_actor(actor_id) is not None), None)

    async def _tick_present_actor(self, actor_id: str) -> bool:
        """ActorTicker (kernel/geography/runtime.py) for Prompt 4's
        "tick every physically present Actor" step: coordinates one
        Actor's cognitive cycle via its home Society, independent of
        whether that Society happens to be hosted where the Actor
        currently physically stands — physical presence and Society
        membership are independent dimensions (Prompt 3). Only called for
        Actors GeographicEntityRuntime hasn't already ticked this cycle
        via their home Society's own society-wide tick()."""
        home = self._home_society_runtime(actor_id)
        if home is None:
            return False
        # Performance analysis instrumentation only (measurement, not a
        # behavior change) -- Runtime Performance Audit: total wall time
        # for this one actor's tick, ground truth against which the
        # finer stage_timings_ms breakdown (read back below from
        # actor_state.last_tick_result) is compared. Wraps the WHOLE
        # tick_one_actor call, not just formation.from_state(), because
        # tick_one_actor also runs get_observation()/belief_fusion.fuse()
        # BEFORE the cognitive engine's own stages even start (runtime.py
        # tick_one_actor) -- work state.stage_durations never sees.
        started = time.perf_counter()
        result = bool(await home.tick_one_actor(actor_id))
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._cycle_actor_timing_ms[actor_id] = elapsed_ms
        return result

    def _temporary_membership_lookup(self, actor_id: str) -> tuple[str, ...]:
        """MembershipLookup (kernel/geography/runtime.py) for Prompt 5's
        GeoResult.temporary_memberships."""
        return tuple(self._membership_governor.temporary_societies_for_actor(actor_id))

    def _effective_membership_lookup(self, actor_id: str) -> tuple[str, ...]:
        """MembershipLookup (kernel/geography/runtime.py) for Prompt 5's
        GeoResult.effective_memberships."""
        return tuple(self._membership_governor.effective_societies(actor_id))

    def _publish_lemon_metrics(
        self, geo_result: GeographicTickResult, perturbations: list[dict[str, Any]],
        actors_observed: int, interactions_routed: int, duration_ms: float,
    ) -> None:
        """Prompt 8 — Lemon Observability: publishes both the previously
        proposed planetary-cycle metrics and this prompt's new governance/
        presence metrics, every cycle. Routes through kernel/compile/_obs.py
        — the existing Lemon integration point every other subsystem in
        this codebase already uses — so this is a silent no-op wherever
        Lemon hasn't booted, same as every other _obs.py caller.

        Cumulative counts (memberships created/revoked, movements, timeline
        entries, ...) are published as gauges (the CURRENT running total,
        tracked on the owning object itself — PresenceTimeline,
        MembershipGovernor) rather than as counter increments: these
        objects already maintain the true total, so re-deriving a
        since-last-cycle delta to feed a counter would be redundant
        bookkeeping and risks double-counting if this method is ever
        called out of its normal per-cycle cadence. perturbations_applied
        is the one genuine per-cycle delta with no other running total, so
        it alone uses counter()."""
        # -- Retained planetary cycle metrics --
        _obs.gauge("planetary.cycle_duration_ms", duration_ms)
        _obs.gauge("planetary.entities_ticked", float(geo_result.entities_ticked_total))
        _obs.gauge("planetary.societies_ticked", float(geo_result.societies_ticked_total))
        _obs.gauge("planetary.actors_observed", float(actors_observed))
        _obs.gauge("planetary.interactions_routed", float(interactions_routed))
        _obs.counter("planetary.perturbations_applied", increment=len(perturbations))
        _obs.gauge("planetary.context_events_published", float(self.context_stream.event_count))
        _obs.gauge("planetary.simulation_time_seconds", time.time() - self._boot_time)

        queue_depth = max((len(getattr(sr, "_message_queue", ())) for sr in self._societies.values()), default=0)
        self._peak_queue_depth = max(self._peak_queue_depth, queue_depth)
        _obs.gauge("planetary.peak_queue_depth", float(self._peak_queue_depth))

        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            _obs.gauge("planetary.memory_usage_bytes", float(usage.ru_maxrss) * 1024)
            # Gate 5 (Observability): CPU time consumed so far (user + system),
            # not instantaneous percent — resource.getrusage has no percent
            # concept and adding a sampling window (e.g. via psutil) is a
            # heavier dependency than this gauge needs; ru_utime+ru_stime is
            # the same "no new dependency, reuse what memory already uses"
            # tradeoff the line above already made.
            _obs.gauge("planetary.cpu_time_seconds", float(usage.ru_utime + usage.ru_stime))
        except Exception:
            logger.debug("_publish_lemon_metrics: suppressed exception", exc_info=True)

        try:
            kg = getattr(self, "knowledge_graph", None)
            kg_entities = len(kg.entities) if kg is not None else 0
            world_entities = len(self._world.entities())
            world_relationships = len(self._world.relationships())
            # Gate 5 (Observability): "graph size" — both graphs this
            # session's ADR-006 (World Schema v1.0) documents as genuinely
            # separate (commerce.py's KnowledgeGraph vs the generic
            # SharedWorld world_graph), reported distinctly rather than
            # summed into one misleading total.
            _obs.gauge("planetary.knowledge_graph_entities", float(kg_entities))
            _obs.gauge("planetary.world_graph_entities", float(world_entities))
            _obs.gauge("planetary.world_graph_relationships", float(world_relationships))
        except Exception:
            logger.debug("_publish_lemon_metrics: suppressed exception", exc_info=True)

        # Gate 11 (production readiness): AlertManager existed and was
        # wired into Lemon but had zero registered rules (confirmed live —
        # see Lemon._register_default_alert_rules for the fix). This is
        # the actual per-cycle evaluation call site for the two tick-
        # duration rules registered there — without a call to evaluate()
        # somewhere, registering rules alone still alerts on nothing.
        try:
            from src.introspection.lemon import get_lemon
            lemon = get_lemon()
            if lemon is not None:
                for alert in lemon.alerts.evaluate({
                    "duration_ms": duration_ms,
                    "actors_observed": actors_observed,
                }):
                    logger.warning("ALERT FIRED: %s [%s] %s", alert.name, alert.severity.value, alert.message)
        except Exception:
            logger.debug("_publish_lemon_metrics: suppressed exception", exc_info=True)

        # -- Prompt 8 — governance and presence metrics --
        _obs.gauge("governance.permanent_memberships",
                   float(len(self._membership_registry.active_memberships())))
        _obs.gauge("governance.temporary_memberships_created",
                   float(self._membership_governor.created_count))
        _obs.gauge("governance.temporary_memberships_revoked",
                   float(self._membership_governor.revoked_count))
        _obs.gauge("governance.effective_membership_calculations",
                   float(self._membership_governor.effective_calculation_count))
        _obs.gauge("governance.membership_events_published",
                   float(self._membership_governor.events_published_count))

        spaces = self._geo_registry.all(GeographicEntityType.SPACE)
        spaces_with = sum(1 for s in spaces if self._geo_registry.societies_at_or_above(s.entity_id))
        _obs.gauge("governance.spaces_with_societies", float(spaces_with))
        _obs.gauge("governance.spaces_without_societies", float(len(spaces) - spaces_with))
        societies_without_spaces = sum(
            1 for society_id in self._societies
            if not self._geo_registry.spaces_for_society(society_id)
        )
        _obs.gauge("governance.societies_without_spaces", float(societies_without_spaces))

        _obs.gauge("presence.updates", float(self._presence.presence_update_count))
        _obs.gauge("presence.actor_movements", float(self._presence.movement_count))
        _obs.gauge("presence.timeline_entries", float(TimelineStore().entry_count()))

    def join_society(self, actor_id: str, society_id: str, role: str = "member") -> bool:
        """Add actor_id as an organizational member of society_id, WITHOUT
        constructing a second ActorRuntimeState — the direct fix for the
        pre-refactor /memberships route bug where joining a second society
        silently duplicated an actor's entire cognition. Requires the actor
        already have a home registration somewhere (register_actor first)."""
        if self._home_society_runtime(actor_id) is None:
            return False
        if self.get_society_runtime(society_id) is None:
            return False
        self._membership_registry.add(actor_id, society_id, role=role)
        self._relationships.add(actor_id, society_id, RelationshipKind.MEMBER_OF)
        self._mirror_membership_affiliation(actor_id, society_id)
        self._save_relationships()
        self.context_stream.publish(ContextEvent(
            event_type=ContextEventType.WORLD_UPDATE,
            actor_id=actor_id,
            description=f"Actor {actor_id} joined society {society_id}",
            payload={"actor_id": actor_id, "society_id": society_id, "role": role},
        ))
        return True

    def _mirror_membership_affiliation(self, actor_id: str, society_id: str) -> None:
        """Mirrors an organizational membership (join_society, above) into
        the actor's own AffiliationManager as a structural "member_of"
        affiliation, so society membership is visible through the same
        AffiliationManager.active()/by_target() surface every other
        relationship uses (e.g. TransactionCoordinator._eligible_affiliates'
        scan) — without being a basis for DIRECT communication eligibility
        on its own: AffiliationGraph._NON_NEGOTIABLE_TYPES excludes
        "member_of" from rules 1-3, since shared society membership is
        already authorized separately and more precisely by rule 5's real
        SocietyRuntime membership check. Idempotent (checked by target_id +
        type, not just target_id, so a real non-structural affiliation to
        the same target_id — unusual but not impossible — isn't clobbered).
        Non-fatal: an actor with no AffiliationManager reachable yet
        (mid-registration) just has no mirror created."""
        affiliations = None
        for sr in self._societies_for(actor_id):
            state = sr.get_actor(actor_id)
            if state is not None:
                affiliations = getattr(getattr(state, "actor_runtime", None), "affiliations", None)
                if affiliations is not None:
                    break
        if affiliations is None:
            return
        if any(
            a.target_id == society_id and a.affiliation_type == "member_of"
            for a in affiliations.all()
        ):
            return
        sr = self.get_society_runtime(society_id)
        society_name = sr.society.name if sr is not None else society_id
        from uuid import uuid4
        from src.monkey_brain.kernel.affiliations.affiliation import Affiliation
        affiliations.add(Affiliation(
            affiliation_id=uuid4().hex, affiliation_type="member_of",
            target_id=society_id, target_name=society_name,
        ))

    def _unmirror_membership_affiliation(self, actor_id: str, society_id: str) -> None:
        """Symmetric counterpart to _mirror_membership_affiliation — removes
        the member_of Affiliation mirror so a terminated/removed Membership
        stops rendering a stale MEMBER_OF edge in the graph (the frontend's
        Society Graph and Ontology Explorer both read MEMBER_OF straight
        from this AffiliationManager surface, not from the membership
        registry directly). No-op if there's no reachable
        AffiliationManager or no matching mirror — safe to call even for
        an already-terminated membership."""
        affiliations = None
        for sr in self._societies_for(actor_id):
            state = sr.get_actor(actor_id)
            if state is not None:
                affiliations = getattr(getattr(state, "actor_runtime", None), "affiliations", None)
                if affiliations is not None:
                    break
        if affiliations is None:
            return
        match = next(
            (a for a in affiliations.by_target(society_id) if a.affiliation_type == "member_of"),
            None,
        )
        if match is not None:
            affiliations.remove(match.affiliation_id)

    def leave_society(self, actor_id: str, society_id: str) -> bool:
        """Remove actor_id's organizational membership in society_id. If
        society_id is the actor's HOME society (the one owning its
        cognition), this also unregisters it there (matching this
        codebase's pre-refactor single-membership behavior) — otherwise
        it's a pure organizational detach, cognition untouched."""
        home = self._home_society_runtime(actor_id)
        self._membership_registry.remove(actor_id, society_id)
        self._unmirror_membership_affiliation(actor_id, society_id)
        for rel in self._relationships.relationships_between(actor_id, society_id, RelationshipKind.MEMBER_OF):
            self._relationships.remove(rel.relationship_id)
        self._save_relationships()
        if home is not None and home.society.society_id == society_id:
            return self.unregister_actor(actor_id)
        return True

    def societies_for_actor(self, actor_id: str) -> tuple[str, ...]:
        """Every society actor_id is a member of — home registration plus
        every additional join_society() membership. The real, single
        source of truth, replacing the old ad-hoc "scan every
        SocietyRuntime._actors dict" pattern in api/routes/actors.py."""
        home = self._home_society_runtime(actor_id)
        ids = set(self._membership_registry.societies_for_actor(actor_id))
        if home is not None:
            ids.add(home.society.society_id)
        return tuple(ids)

    def teams_for_actor(self, actor_id: str) -> tuple[Team, ...]:
        """Every team actor_id belongs to, across every society it's a
        member of — at most one per society (SocietyRuntime.
        add_actor_to_team's existing single-team-per-society enforcement),
        so this can return more than one Team for a multi-society actor."""
        teams = []
        for society_id in self.societies_for_actor(actor_id):
            sr = self.get_society_runtime(society_id)
            if sr is None:
                continue
            team = sr.team_for_actor(actor_id)
            if team is not None:
                teams.append(team)
        return tuple(teams)

    def activate_societies(self, actor_id: str, goal: str) -> SocietyActivationResult:
        """Dynamically select which of actor_id's societies are relevant
        to `goal` and aggregate their policies — see activation.py."""
        return self._society_activation.activate_for_goal(actor_id, goal)

    @property
    def relationships(self) -> RelationshipGraph:
        return self._relationships

    @property
    def memory_manager(self) -> Any:
        return self._memory_manager

    @property
    def knowledge_graph(self) -> Any:
        return self._knowledge_graph

    @property
    def context_engine(self) -> Any:
        return self._context_engine

    def attach_semantic_memory(self, semantic_memory: Any) -> None:
        """Wire vector retrieval for SittingFace external knowledge."""
        if self._context_engine is not None and hasattr(self._context_engine, "set_external_knowledge_retriever"):
            from src.monkey_brain.kernel.knowledge.sittingface_retrieval import SittingFaceKnowledgeRetriever
            retriever = SittingFaceKnowledgeRetriever(semantic_memory=semantic_memory)
            self._context_engine.set_external_knowledge_retriever(retriever)

    @property
    def membership_registry(self) -> Any:
        return self._membership_registry

    @property
    def commerce_network(self) -> CommerceNetwork:
        return self._commerce_network

    @property
    def delegation_registry(self) -> Any:
        return self._delegation_registry

    @property
    def perturbation_queue(self) -> PerturbationQueue:
        return self._perturbation_queue

    def governance_for(self, society_id: str) -> Any:
        """The SocietyGovernanceEngine owning this society — the resolution
        entry point Membership's resolve_policies/resolve_permissions/
        resolve_capabilities/resolve_constraints/update_trust need, since
        SocietyGovernanceEngine itself has no notion of society_id (it's
        implicitly scoped by which SocietyRuntime owns the instance)."""
        sr = self.get_society_runtime(society_id)
        return sr.governance if sr is not None else None

    # ── World Operations ─────────────────────────────────────────────────

    def add_world_entity(self, entity: WorldEntity) -> None:
        self._world_model.add_entity(entity)
        self.context_stream.publish(ContextEvent(
            event_type=ContextEventType.WORLD_UPDATE,
            description=f"Entity added: {entity.name}",
            payload=entity.to_dict(),
            provenance="api:world/entities",
        ))
        self._save_world()

    def update_world_entity(self, entity_id: str, **attributes: Any) -> WorldEntity | None:
        result = self._world_model.update_entity(entity_id, **attributes)
        if result:
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                description=f"Entity updated: {entity_id}",
                payload=result.to_dict(),
                provenance="api:world/entities/{id}",
            ))
            self._save_world()
        return result

    def remove_world_entity(self, entity_id: str) -> bool:
        result = self._world_model.remove_entity(entity_id)
        if result:
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                description=f"Entity removed: {entity_id}",
                payload={"entity_id": entity_id},
                provenance="api:world/entities/{id}",
            ))
            self._save_world()
        return result

    def add_world_relationship(self, relationship: Any) -> None:
        self._world_model.add_relationship(relationship)
        self.context_stream.publish(ContextEvent(
            event_type=ContextEventType.WORLD_UPDATE,
            description=f"Relationship added: {relationship.source_id} -> {relationship.target_id}",
            payload=relationship.to_dict(),
            provenance="api:world/relationships",
        ))
        self._save_world()

    def remove_world_relationship(self, relationship_id: str) -> bool:
        result = self._world_model.remove_relationship(relationship_id)
        if result:
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                description=f"Relationship removed: {relationship_id}",
                payload={"relationship_id": relationship_id},
                provenance="api:world/relationships/{id}",
            ))
            self._save_world()
        return result

    def record_world_event(self, event: Any) -> None:
        self._world_model.record_event(event)
        self.context_stream.publish(ContextEvent(
            event_type=ContextEventType.WORLD_UPDATE,
            description=f"Event recorded: {event.description}",
            payload=event.to_dict(),
            provenance="api:world/events",
        ))
        self._save_world()

    def remove_world_event(self, event_id: str) -> bool:
        result = self._world_model.remove_event(event_id)
        if result:
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                description=f"Event removed: {event_id}",
                payload={"event_id": event_id},
                provenance="api:world/events/{id}",
            ))
            self._save_world()
        return result

    def add_world_resource(self, resource: Any) -> None:
        self._world_model.add_resource(resource)
        self.context_stream.publish(ContextEvent(
            event_type=ContextEventType.WORLD_UPDATE,
            description=f"Resource added: {resource.name}",
            payload=resource.to_dict(),
            provenance="api:world/resources",
        ))
        self._save_world()

    def remove_world_resource(self, resource_id: str) -> bool:
        result = self._world_model.remove_resource(resource_id)
        if result:
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                description=f"Resource removed: {resource_id}",
                payload={"resource_id": resource_id},
                provenance="api:world/resources/{id}",
            ))
            self._save_world()
        return result

    # ── Location Operations ──────────────────────────────────────────────

    def add_world_location(self, location: Any) -> None:
        self._world_model.add_location(location)
        self.context_stream.publish(ContextEvent(
            event_type=ContextEventType.WORLD_UPDATE,
            description=f"Location added: {location.name}",
            payload=location.to_dict(),
            provenance="api:world/locations",
        ))
        self._save_world()

    def update_world_location(self, location_id: str, **kwargs: Any) -> Any | None:
        result = self._world_model.update_location(location_id, **kwargs)
        if result:
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                description=f"Location updated: {location_id}",
                payload=result.to_dict(),
                provenance="api:world/locations/{id}",
            ))
            self._save_world()
        return result

    def remove_world_location(self, location_id: str) -> bool:
        result = self._world_model.remove_location(location_id)
        if result:
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                description=f"Location removed: {location_id}",
                payload={"location_id": location_id},
                provenance="api:world/locations/{id}",
            ))
            self._save_world()
        return result

    # ── Coordination ─────────────────────────────────────────────────────

    def coordinate(self) -> CoordinationEngine:
        return self._coordination_engine

    @property
    def game_theory(self) -> GameTheoryRuntime:
        """Strategic runtime shared by planetary and society coordination."""
        return self._game_theory

    # ── Interaction ──────────────────────────────────────────────────────

    def send_interaction(self, interaction_type: InteractionType,
                         initiator_id: str, participant_ids: tuple[str, ...],
                         topic: str = "", proposal: Any = None) -> Interaction:
        """Step 12.7: delegates to SocietyRuntime.route_interaction() (fixed
        this same sprint — was raising NameError on every call) instead of
        maintaining a second InteractionManager. route_interaction() already
        publishes the Context Stream event and records coordination, so
        this method's own job is just forwarding the call."""
        return self._society_runtime.route_interaction(
            interaction_type=interaction_type,
            initiator_id=initiator_id,
            participant_ids=participant_ids,
            topic=topic,
            proposal=proposal,
        )

    # ── Learning ─────────────────────────────────────────────────────────

    def share_experience(self, experience: SharedExperience) -> CollectiveLearningResult:
        """Step 12.8: delegates to SocietyRuntime.share_experience(), which
        already publishes the Context Stream event and records coordination
        — this method's own job is just forwarding the call, same pattern
        as send_interaction()."""
        result = self._society_runtime.share_experience(experience)
        applied = self._world_model.apply_learning(experience.world_impact)
        if applied:
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                actor_id=experience.actor_id,
                description=f"Learning refined {applied} world entr{'y' if applied == 1 else 'ies'}",
                payload={"experience_id": experience.experience_id, "applied": applied},
                provenance="planetary:learning",
            ))
            self._save_world()
        return result

    # ── Governance ───────────────────────────────────────────────────────

    def check_permission(self, actor_id: str, resource: str, action: str) -> bool:
        # A governance grant is meaningful only while the actor has an
        # active membership in the society that owns that grant.  Do not let
        # the legacy home-governance shortcut keep household wallet,
        # pantry, or grocery permissions alive after leave/termination.
        for society_id in self._membership_registry.societies_for_actor(actor_id):
            governance = self.governance_for(society_id)
            if governance is not None and governance.check_permission(actor_id, resource, action):
                return True
        return False

    def authorize(self, actor_id: str, resource: str, action: str,
                  amount: float | None = None) -> bool:
        """Membership-aware governance check with optional amount limits."""
        for society_id in self._membership_registry.societies_for_actor(actor_id):
            governance = self.governance_for(society_id)
            if governance is not None and governance.authorize(actor_id, resource, action, amount):
                return True
        return False

    # ── Society Registry (Item #9 follow-up: PlanetaryRuntime can now
    #    actually coordinate more than one SocietyRuntime) ────────────────

    def add_society(self, society_runtime: SocietyRuntime) -> None:
        """Registers an existing SocietyRuntime so federated_cycle() can
        coordinate it. Does not construct anything — the caller owns
        the SocietyRuntime's lifetime; this only makes it reachable by
        society_id for federation coordination.

        Also hosts it at the default City (see __init__) so it participates
        in the automatic planetary cycle out of the box — callers building a
        real topology reassign it via host_society()/assign_society_to_city(),
        which moves rather than duplicates."""
        self._attach_society(society_runtime)
        self._societies[society_runtime.society.society_id] = society_runtime
        if self._redis:
            society_runtime.collective_learning.set_redis(self._redis, society_runtime.society.society_id)
            society_runtime.collective_learning.load_recent()
        if getattr(self, "_default_city", None) is not None:
            self._default_city = self._geo_registry.host_society(self._default_city.entity_id, society_runtime.society.society_id)
            self._save_geography()
        self._save_societies()

    def _effective_is_member(self, actor_id: str, society_id: str) -> bool:
        """Coordination Boundary refactor: observation visibility must
        follow EFFECTIVE membership (permanent UNION temporary), not just
        permanent — a temporarily-present Actor is a real participant in
        that Society's coordination (see add_temporary_participant wiring
        below) and must be able to observe its WorldEntities too, the same
        as a permanent member."""
        return society_id in self.effective_societies(actor_id)

    def _on_temporary_membership_granted(self, actor_id: str, society_id: str) -> None:
        """MembershipGovernor callback: make actor_id a coordination
        participant in society_id's SocietyRuntime too — eligible for its
        tick()/interaction routing/message queue/governance checks, not
        only its effective_societies() governance-set entry. Shares the
        Actor's EXISTING ActorRuntimeState (from its home registration);
        never constructs a second one."""
        sr = self.get_society_runtime(society_id)
        home = self._home_society_runtime(actor_id)
        actor_state = home.get_actor(actor_id) if home is not None else None
        if sr is not None and actor_state is not None:
            sr.add_temporary_participant(actor_state)

    def _on_temporary_membership_revoked(self, actor_id: str, society_id: str) -> None:
        """MembershipGovernor callback: undo _on_temporary_membership_granted
        — UNLESS actor_id now holds an actual PERMANENT membership in
        society_id instead (reconcile() revokes a temporary entry that's
        been superseded by a new permanent one; that Actor must stay a
        participant there, just no longer via the temporary path)."""
        if self._membership_registry.is_member(actor_id, society_id):
            return
        sr = self.get_society_runtime(society_id)
        if sr is not None:
            sr.remove_temporary_participant(actor_id)

    async def connect_nats(self) -> bool:
        """Real NATS publishing for the context stream (the TODO on
        SocietyContextStream.publish() itself) — additive to the
        existing Redis durability (_save_context), not a replacement.
        Every SocietyRuntime this PlanetaryRuntime manages shares ONE
        real SocietyContextStream instance (see context_stream property
        below, and _attach_society's own society_runtime._context_stream
        = self._society_runtime.context_stream) — so there is exactly
        one stream to wire, not one per society. Non-fatal: mirrors
        kernel/resources/nats.py's own optional-resource contract
        (required=False) — logs and returns False if unreachable,
        same as every other optional dependency in this boot sequence."""
        try:
            import nats
            url = os.getenv("NATS_URL", "nats://localhost:4222")
            self._nats_client = await nats.connect(url)
            self.context_stream.set_nats(self._nats_client, "monkeybrain.context.planetary")
            logger.info("PlanetaryRuntime connected to NATS at %s", url)
            # Multi-Actor Execution Handoff: _load_actors() (boot-time
            # actor reload) runs BEFORE this method in the boot sequence
            # (kernel.py::_phase_planetary), so every actor's
            # _subscribe_actor_inbox() call at reload time found
            # self._nats_client still None and no-opped. Now that NATS is
            # confirmed live, retroactively subscribe every actor that
            # already exists — the only correct point to do this, since
            # nothing else in the boot sequence knows both "NATS is up"
            # and "here is every actor" at the same time.
            for sr in self.all_societies():
                for state in sr.all_actors():
                    self._subscribe_actor_inbox(state.actor_id, state.profile)
            return True
        except Exception as exc:
            logger.warning("PlanetaryRuntime NATS connection unavailable (non-fatal): %s", exc)
            self._nats_client = None
            return False

    def _sync_world_capabilities(self) -> None:
        """Mirror the real, live capability bus (self._execution_engine's
        wired CapabilityBus) into the world's own versioned WorldCapability
        records. CognitiveOS Constitution: "knowledge, policies and
        capabilities are versioned infrastructure" -- WorldCapability.version
        was declared (world.py) but nothing anywhere ever registered a real
        WorldCapability, so the field never carried a live value.

        Read-only with respect to the actual capability bus -- this only
        reads bus.names()/discover() after the bus is already fully built,
        never touches registration or dispatch, so it cannot affect which
        capability handles a real action. Idempotent: capability_id is the
        capability's own name, and an unchanged description is skipped
        entirely so re-attaching a society doesn't inflate version on
        every call. Non-fatal by design -- capability introspection must
        never block society attachment."""
        bus = getattr(self._execution_engine, "_capability_bus", None)
        if bus is None or not hasattr(bus, "names"):
            return
        try:
            existing_by_id = {c.capability_id: c for c in self.world.capabilities()}
            for name in bus.names():
                capability = bus.discover(name)
                description = getattr(capability, "description", "") or "".join(
                    ((capability.__doc__ or "").strip().splitlines() or [""])[:1]
                )
                current = existing_by_id.get(name)
                if current is not None and current.description == description:
                    continue
                self.world.record_capability(capability_id=name, name=name, description=description)
        except Exception:
            logger.debug("_sync_world_capabilities: capability introspection failed (non-fatal)", exc_info=True)

    def _attach_society(self, society_runtime: SocietyRuntime) -> None:
        """Attach a society to Planetary's single world and context owners."""
        # AskActorCapability (kernel/domains/grocery.py) reads
        # context["planetary_runtime"] to resolve a target actor across
        # every society — nothing ever set this key (confirmed: no other
        # assignment anywhere in the codebase), so every real AskActor call
        # failed outright with "no planetary_runtime available to resolve
        # target actor" regardless of whether the named actor was reachable.
        # Same direct-attribute-wiring idiom as every other backref below.
        society_runtime._planetary_runtime = self
        society_runtime._world = self._world_model.semantic_world
        society_runtime._observation_provider._world = self._world_model.semantic_world
        society_runtime._context_stream = self._society_runtime.context_stream
        # Society as Organizational Context refactor: every society this
        # PlanetaryRuntime manages shares the same membership registry and
        # activation engine, so add_actor_to_team's membership-only check
        # (runtime.py) and ReasoningRuntime's per-actor activation both see
        # the real, shared multi-membership state.
        society_runtime._membership_registry = self._membership_registry
        society_runtime._society_activation = self._society_activation
        society_runtime._context_engine = self._context_engine
        society_runtime._execution_engine = self._execution_engine
        society_runtime._knowledge_graph = self._knowledge_graph
        society_runtime._communication_router.affiliation_graph = self._affiliation_graph
        self._sync_world_capabilities()
        # A WorldEntity with an owner_society_id is only observable by
        # actors with an active effective Membership there (permanent OR
        # temporary — Coordination Boundary refactor; was permanent-only).
        society_runtime._observation_provider._membership_lookup = self._effective_is_member

    def create_society(
        self, name: str, description: str = "", society_type: str = "generic",
        activation_tags: tuple[str, ...] = (), always_active: bool = False,
        subscribed_events: tuple[str, ...] = (),
    ) -> SocietyRuntime:
        """Convenience: builds a new SocietyRuntime, registers it, returns
        it. The caller registers actors on the returned object directly
        (SocietyRuntime.register_actor) — PlanetaryRuntime coordinates
        societies, it does not manage individual actors on their behalf
        (Rule 4). society_type/activation_tags/always_active (Society as
        Organizational Context refactor) feed SocietyActivationEngine's
        goal-relevance matching — see activation.py. subscribed_events
        (True Multi-Actor Coordination) feeds _propagate_coordination's
        event-to-society matching below — see that method's docstring."""
        society_runtime = SocietyRuntime(Society(
            name=name, description=description, society_type=society_type,
            activation_tags=activation_tags, always_active=always_active,
            subscribed_events=subscribed_events,
        ), strategic_runtime=self._game_theory)
        self.add_society(society_runtime)
        return society_runtime

    def search_societies(self, tag: str = "", society_type: str = "") -> tuple[SocietyRuntime, ...]:
        """Society discovery: filter all managed societies by an
        activation_tags match and/or an exact society_type match. Empty
        filters match everything."""
        results = []
        for sr in self._societies.values():
            society = sr.society
            if tag and tag.lower() not in {t.lower() for t in society.activation_tags}:
                continue
            if society_type and society.society_type != society_type:
                continue
            results.append(sr)
        return tuple(results)

    def get_society_runtime(self, society_id: str) -> SocietyRuntime | None:
        return self._societies.get(society_id)

    def all_societies(self) -> tuple[SocietyRuntime, ...]:
        return tuple(self._societies.values())

    def get_actor_runtime(self, actor_id: str) -> Any | None:
        """Resolve the ActorRuntime boundary for an actor across every
        society it currently participates in. Returns None if the actor
        has no runtime registration anywhere."""
        for society_runtime in self._societies_for(actor_id):
            actor_runtime = society_runtime.get_actor_runtime(actor_id)
            if actor_runtime is not None:
                return actor_runtime
        return None

    def _get_actor_state_store(self) -> Any | None:
        """Lazy, fail-soft accessor for the canonical belief persistence
        backend. Edge-First Architecture: an edge/device/robot node (this
        process's own ACTOR_NODE_CLASS, the same signal __init__ already
        uses for the offline-safety gate) gets kernel/edge/actor_state_
        store.py::EdgeActorStateStore — SQLite via EdgeLocalStore, no
        network dependency — instead of the Mongo-backed
        persistence/actor_state_store.py::ActorStateStore every cloud
        process still uses (Step 14 — Architecture Consolidation, via the
        established persistence/db_pool.py::get_db_pool() singleton).
        EdgeActorStateStore satisfies the exact same ActorStateStoreProtocol
        (kernel/pipeline/protocols.py), so restore_actor_belief/
        checkpoint_actor_belief below need no branching of their own.
        Returns None if the chosen backend is unavailable — callers degrade
        to "continue with in-memory belief," never fail the request."""
        if self._actor_state_store is not None:
            return self._actor_state_store
        node_class_hint = os.getenv("ACTOR_NODE_CLASS", "cloud").strip().lower()
        if node_class_hint in ("edge", "device", "robot"):
            try:
                from src.monkey_brain.kernel.edge.actor_state_store import EdgeActorStateStore
                from src.monkey_brain.kernel.edge.local_store import get_edge_local_store
                self._actor_state_store = EdgeActorStateStore(get_edge_local_store())
            except Exception as exc:
                logger.warning("[planetary] EdgeActorStateStore unavailable, belief persistence disabled: %s", exc)
                return None
            return self._actor_state_store
        try:
            from src.monkey_brain.persistence.actor_state_store import ActorStateStore
            from src.monkey_brain.persistence.db_pool import get_db_pool
            self._actor_state_store = ActorStateStore(get_db_pool())
        except Exception as exc:
            logger.warning("[planetary] ActorStateStore unavailable, belief persistence disabled: %s", exc)
            return None
        return self._actor_state_store

    def restore_actor_belief(self, actor_id: str) -> bool:
        """Belief Runtime Reconstruction: load the actor's most recently
        persisted canonical belief and rebuild it on the live actor.
        """
        actor_runtime = self.get_actor_runtime(actor_id)
        if actor_runtime is None:
            return False
        actor = actor_runtime.actor
        store = self._get_actor_state_store()
        if store is None:
            return False
        try:
            tenant_id = getattr(actor, "tenant_id", None) or "default"
            persisted = store.load(actor_id, tenant_id)
            if persisted is None or not persisted.belief_state:
                return False
            from src.monkey_brain.kernel.pipeline.belief_state import BeliefState as PipelineBeliefState
            data = json.loads(persisted.belief_state.decode())
            actor.restore_pipeline_belief(PipelineBeliefState.from_dict(data))
            return True
        except Exception as exc:
            logger.warning(
                "[planetary] %s belief restore failed, continuing with fresh belief: %s",
                actor_id, exc,
            )
            return False

    def checkpoint_actor_belief(self, actor_id: str) -> None:
        """Persist the actor's canonical belief (kernel/pipeline/belief_state.py::BeliefState,
        via actor.pipeline_belief()) once its cognitive cycle has committed,
        so the NEXT request's restore_actor_belief() has a real checkpoint
        to reconstruct from. Never raises — persistence failures must not
        fail the request that already completed successfully."""
        actor_runtime = self.get_actor_runtime(actor_id)
        if actor_runtime is None:
            return
        actor = actor_runtime.actor
        store = self._get_actor_state_store()
        if store is None:
            return
        try:
            from src.monkey_brain.persistence.actor_state_store import PersistedActorState
            belief = actor.pipeline_belief()
            pipeline_actor = actor.pipeline_actor() if hasattr(actor, "pipeline_actor") else None
            tenant_id = getattr(actor, "tenant_id", None) or "default"
            # CognitiveOS Constitution: "persistent actors survive
            # interruption, restart and changing models" -- record which
            # provider/model actually reasoned about this actor this
            # cycle, so continuity across a model swap is auditable, not
            # just functionally unaffected. Never fatal: an unavailable
            # backend must not block the belief checkpoint that already
            # completed successfully.
            model_provider, model_name = "", ""
            try:
                from src.monkey_brain.kernel.execute.provider.model_backend import get_backend
                backend_stats = get_backend().stats()
                model_provider = backend_stats.get("provider", "")
                model_name = backend_stats.get("model", "")
            except Exception:
                logger.debug("[planetary] %s model backend stats unavailable (non-fatal)", actor_id, exc_info=True)
            
            # Capture complete actor metadata for rehydration on restart
            # (name, actor_type, society_id, status, etc.)
            sr = self._home_society_runtime(actor_id)
            registry_state = sr.get_actor(actor_id) if sr is not None else None

            lease_fence = int(getattr(registry_state, "last_lease_fence", 0) or 0) if registry_state else 0
            if lease_fence and self._redis is not None:
                try:
                    current_fence = int(self._redis.get(f"{_ACTOR_FENCE_KEY_PREFIX}{actor_id}") or 0)
                    if current_fence > lease_fence:
                        logger.warning(
                            "[planetary] %s belief checkpoint skipped — lease fence superseded "
                            "(current=%d tick=%d); another node may own this actor",
                            actor_id, current_fence, lease_fence,
                        )
                        return
                except Exception as exc:
                    logger.debug("[planetary] lease fence check failed (non-fatal): %s", exc)
            
            actor_metadata = {}
            if registry_state and hasattr(registry_state, "profile"):
                profile = registry_state.profile
                if hasattr(profile, "identity"):
                    actor_metadata["name"] = profile.identity.name
                    actor_metadata["actor_type"] = profile.identity.actor_type
                actor_metadata["description"] = getattr(profile, "description", "")
                actor_metadata["capabilities"] = getattr(profile, "capabilities", [])
                actor_metadata["constraints"] = getattr(profile, "constraints", [])
                actor_metadata["metadata"] = getattr(profile, "metadata", {})
            
            if sr is not None:
                actor_metadata["society_id"] = sr.society.society_id
            
            if registry_state:
                actor_metadata["status"] = str(getattr(registry_state, "status", "registered"))
                if hasattr(registry_state, "actor_runtime") and hasattr(registry_state.actor_runtime, "affiliations"):
                    try:
                        actor_metadata["affiliations"] = registry_state.actor_runtime.affiliations.to_dict()
                    except Exception:
                        pass
            
            # Capture desired state from Redis (if available)
            try:
                desired_state = self.get_actor_desired_state(actor_id)
                actor_metadata["desired_state"] = {
                    "state": desired_state.value if hasattr(desired_state, "value") else str(desired_state),
                    "reason": "Persisted from control-plane",
                }
            except Exception:
                pass

            if lease_fence:
                actor_metadata["lease_fence"] = lease_fence
            
            state = PersistedActorState(
                actor_id=actor_id,
                tenant_id=tenant_id,
                belief_state=json.dumps(belief.to_dict()).encode(),
                bellman_policy=b"",
                phi_compiled=b"",
                memory_kv=actor_metadata,  # Store metadata in memory_kv field
                last_updated=datetime.now().isoformat(),
                version=belief.version,
                cycle_count=getattr(pipeline_actor, "cycle_count", 0),
                last_cycle=getattr(pipeline_actor, "last_reasoned_at", 0.0) or 0.0,
                last_model_provider=model_provider,
                last_model_name=model_name,
            )
            store.save(state)
        except Exception as exc:
            logger.warning("[planetary] %s belief checkpoint failed (non-fatal): %s",
                           actor_id, exc)

        # Actor Registry (Deployment Architecture, Section 7): every real
        # request cycle already reaches this method after commit, so it's
        # the natural, already-live place to refresh the registry's
        # status/node_id/updated_at -- giving locate_actor()/list_registry()
        # a genuine liveness signal ("this actor was last active on THIS
        # node at THIS time") instead of a value that's only ever fresh as
        # of registration. Independent try/except: a registry-refresh
        # failure must never mask a belief checkpoint that already
        # succeeded above, or vice versa.
        try:
            sr = self._home_society_runtime(actor_id)
            registry_state = sr.get_actor(actor_id) if sr is not None else None
            if sr is not None and registry_state is not None:
                self._save_actor(registry_state, sr.society.society_id)
        except Exception as exc:
            logger.debug("[planetary] %s registry refresh failed (non-fatal): %s", actor_id, exc)

    def _societies_for(self, actor_id: str) -> tuple[SocietyRuntime, ...]:
        """Every SocietyRuntime this actor currently participates in —
        normally one (its permanent home), but temporary presence-driven
        membership (MembershipGovernor.add_temporary_participant) registers
        the SAME ActorRuntimeState into a second SocietyRuntime's _actors
        while the actor is physically present there, so this can return
        more than one."""
        return tuple(sr for sr in self._societies.values() if sr.get_actor(actor_id) is not None)

    def all_active_actors(self) -> tuple[Any, ...]:
        """Every active ActorRuntimeState across every managed society,
        deduplicated by actor_id — an actor present in more than one
        SocietyRuntime (see _societies_for) is only returned once. Used by
        kernel/pipeline/planning/deja_vu.py::replay_affected_actors to find
        which actors have a standing plan potentially worth re-evaluating
        after a Planetary Tick's World Perturbation reconciliation."""
        seen: set[str] = set()
        result: list[Any] = []
        for sr in self._societies.values():
            for state in sr.active_actors():
                actor_id = getattr(state, "actor_id", "")
                if not actor_id or actor_id in seen:
                    continue
                seen.add(actor_id)
                result.append(state)
        return tuple(result)

    def resolve_communication(
        self, sender_id: str, recipient_id: str,
        *, correlation_id: str = "", causation_id: str = "",
    ) -> CommunicationDecision:
        """Affiliation/society-governed eligibility check spanning every
        managed society, not just one. If sender and recipient currently
        share ANY SocietyRuntime — including one only the sender or
        recipient is present in temporarily — delegate to that runtime's
        own router (real shared-affiliation + temporary-membership
        semantics, unchanged); prefer a society whose router actually
        allows it, otherwise return a real denial from a shared society so
        the reason stays honest. With no shared society at all, the
        Affiliation Graph is the sole authority (kernel/affiliations/
        graph.py::AffiliationGraph.can_communicate) — the same eligibility
        semantics every attached SocietyRuntime's router already delegates
        to via _attach_society, just reached directly since there's no
        society-scoped router to go through."""
        sender_societies = self._societies_for(sender_id)
        recipient_societies = set(self._societies_for(recipient_id))
        shared_societies = [sr for sr in sender_societies if sr in recipient_societies]
        if shared_societies:
            decisions = [
                sr._communication_router.resolve(
                    sender_id, recipient_id,
                    correlation_id=correlation_id, causation_id=causation_id,
                )
                for sr in shared_societies
            ]
            for decision in decisions:
                if decision.allowed:
                    return decision
            return decisions[0]
        decision = self._affiliation_graph.can_communicate(sender_id, recipient_id)
        if correlation_id:
            decision = _dc_replace(decision, correlation_id=correlation_id)
        if causation_id:
            decision = _dc_replace(decision, causation_id=causation_id)
        return decision

    async def execute_transaction(
        self, originating_actor_id: str, objective: str, *, max_steps: int = 8,
    ) -> Any:
        """Required Transaction Execution Logic's entry point: delegates
        entirely to TransactionCoordinator (kernel/society/transaction.py)
        — this runtime facilitates (resolves societies/affiliations,
        delivers messages, streams progress) but owns no negotiation
        workflow itself, same "Rule 4" separation every other coordinator
        here (GameTheoryRuntime, CoordinationEngine) already follows."""
        return await self._transaction_coordinator.execute(
            originating_actor_id, objective, max_steps=max_steps,
        )

    def activate_society(self, society_id: str) -> bool:
        """Activate a society so it participates in planetary cycles."""
        sr = self._societies.get(society_id)
        if sr is None:
            return False
        sr.activate()
        self._save_societies()
        return True

    def deactivate_society(self, society_id: str) -> bool:
        """Deactivate a society so it's skipped during planetary cycles."""
        sr = self._societies.get(society_id)
        if sr is None:
            return False
        sr.deactivate()
        self._save_societies()
        return True

    # ── Federation Management (Step 12.9 — Rule 4: Federation is the domain
    #    object, PlanetaryRuntime MANAGES it) ────────────────────────────

    @property
    def federation_manager(self) -> FederationManager:
        return self._federation_manager

    def create_federation(self, name: str, description: str = "",
                          member_society_ids: tuple[str, ...] = ()) -> Federation:
        """Creates a federation. This society (`self.society.society_id`) is
        NOT automatically a member — call `join_federation()` to add it, same
        as any other member society, since a Federation may be created and
        managed by a coordinating society that isn't itself a member."""
        federation = self._federation_manager.create_federation(name, description, member_society_ids)
        self._commerce_network.attach_federation(federation.federation_id, federation.member_society_ids)
        return federation

    def join_federation(self, federation_id: str) -> Federation | None:
        """Adds THIS PlanetaryRuntime's own society to a federation."""
        federation = self._federation_manager.add_member(federation_id, self.society.society_id)
        if federation is not None:
            self._commerce_network.attach_federation(federation_id, federation.member_society_ids)
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                description=f"Society {self.society.society_id} joined federation {federation_id}",
                payload={"federation_id": federation_id, "society_id": self.society.society_id},
            ))
            self._society_runtime.record_coordination(f"joined federation {federation_id}")
        return federation

    async def federated_cycle(self, federation_id: str) -> FederatedCycleResult:
        """Ticks every member society of a federation that this runtime can
        actually reach (Item #9 follow-up to Step 12.9 — closes the "nothing
        actually RUNS more than one society concurrently" gap documented in
        federation.py's module docstring).

        For each `member_society_id` on the Federation:
          - if it's in `self._societies` (this runtime's registry), tick it
            for real via `SocietyRuntime.tick()` and record a cross-society
            event on the Federation summarizing what happened;
          - if it isn't (e.g. it lives on another node/process), it's
            skipped and reported in `unregistered_society_ids` — a known,
            expected state for a federation member this runtime doesn't
            locally host, not an error.

        World-state merging across societies (each society's SharedWorld
        staying genuinely separate, only coordination history syncing here)
        is explicitly NOT attempted — SharedWorld has no merge/diff
        primitive to build this on top of yet, and inventing one is a
        separate, larger feature, not this gap's scope.
        """
        start = time.time()
        federation = self._federation_manager.get_federation(federation_id)
        if federation is None:
            return FederatedCycleResult(federation_id=federation_id)

        ticked: list[str] = []
        unregistered: list[str] = []
        actors_ticked_total = 0
        interactions_routed_total = 0

        for society_id in federation.member_society_ids:
            society_runtime = self._societies.get(society_id)
            if society_runtime is None:
                unregistered.append(society_id)
                continue

            try:
                tick_result = await society_runtime.tick()
                ticked.append(society_id)
                actors_ticked_total += tick_result.actors_ticked
                interactions_routed_total += tick_result.interactions_routed

                self._federation_manager.record_cross_society_event(
                    federation_id,
                    f"society {society_id} ticked: {tick_result.actors_ticked} actor(s), "
                    f"{tick_result.interactions_routed} interaction(s)",
                )
            except Exception as e:
                logger.error("Society %s tick failed in federation %s: %s", society_id, federation_id, e)

        result = FederatedCycleResult(
            federation_id=federation_id,
            societies_ticked=tuple(ticked),
            unregistered_society_ids=tuple(unregistered),
            actors_ticked_total=actors_ticked_total,
            interactions_routed_total=interactions_routed_total,
            duration_ms=(time.time() - start) * 1000,
        )

        self.context_stream.publish(ContextEvent(
            event_type=ContextEventType.SOCIETY_TICK,
            description=(
                f"Federated cycle: {len(ticked)} society(ies) ticked, "
                f"{len(unregistered)} unregistered"
            ),
            payload={"federation_id": federation_id, "societies_ticked": ticked,
                     "unregistered_society_ids": unregistered},
        ))

        return result

    # ── Physical Geography (Planet -> Country -> State -> County -> City ->
    #    Street -> Building -> Space) ──────────────────────────────────────
    # Fully independent of Society -> Team -> Actor: a geographic entity
    # HOSTS zero or more societies (host_society), it never contains them.
    # One generic GeographicRegistry/GeographicEntityRuntime for all 8
    # tiers — see kernel/geography/{entity,registry,runtime}.py. The
    # create_country/create_city/assign_society_to_city/get_city_for_society/
    # get_country_for_society/tick_city/tick_country methods below are
    # backward-compat wrappers (this session's api/routes/planet.py routes
    # and Country/City-shaped call sites keep working unchanged); new code
    # should prefer the generic create_geographic_entity/host_society/
    # get_geographic_entity/entity_for_society/tick_geographic_entity methods
    # that follow them, which work at any of the 8 tiers uniformly.

    @property
    def geo_registry(self) -> GeographicRegistry:
        return self._geo_registry

    def create_geographic_entity(
        self, entity_type: GeographicEntityType, name: str, parent_id: str,
        description: str = "", **type_kwargs: Any,
    ) -> GeographicEntity | None:
        """Create any-tier geographic entity under an existing parent.
        Returns None if parent_id doesn't resolve or the tier pairing is
        invalid (see kernel/geography/entity.py::PARENT_TIER) — an entity
        cannot exist without its required parent, unlike Federation
        membership (which is add-after-create). Delegates construction to
        GeographicRegistry.create() — registry.py is the sole constructor
        of Planet/Country/.../Space instances."""
        entity = self._geo_registry.create(entity_type, name, description, parent_id, **type_kwargs)
        if entity is not None:
            self._save_geography()
        return entity

    def get_geographic_entity(self, entity_id: str) -> GeographicEntity | None:
        return self._geo_registry.get(entity_id)

    def space_contents(self, space_id: str) -> dict[str, Any] | None:
        """What's attached to a Space: its current actor occupants
        (kernel/timeline/presence.py::PresenceTimeline — physical location)
        and its associated societies (GeographicRegistry.societies_at_or_
        above — every society hosted at this Space or any ancestor, the
        same "associated Societies" set MembershipGovernor grants temporary
        membership from). The two are siblings, not a Space -> Society ->
        Actor nesting (see kernel/geography/entity.py module docstring):
        this method is the read-side composition of that model, combining
        PresenceTimeline (which knows actors, not societies) with
        GeographicRegistry (which knows societies, not actors) rather than
        either one importing the other. Returns None if space_id doesn't
        resolve to an actual Space."""
        space = self._geo_registry.get(space_id)
        if space is None or space.entity_type != GeographicEntityType.SPACE:
            return None
        return {
            "space_id": space_id,
            "actor_ids": self.presence.occupants(space_id),
            "society_ids": tuple(self._geo_registry.societies_at_or_above(space_id)),
        }

    @property
    def presence(self) -> "PresenceTimeline":
        """Temporal Presence & Actor Timeline Model.

        PresenceTimeline is a temporal model of actor presence in spaces
        over time. It tracks when actors enter and leave spaces, and provides
        a timeline of their presence history. This is useful for understanding
        the movement and activity of actors within the planetary runtime.

        Returns the single instance constructed in __init__ (see
        self._membership_governor) — every call must return the SAME
        object, since MembershipGovernor's subscription and the movement
        events it depends on live on that one instance.
        """
        return self._presence

    @property
    def membership_governor(self) -> MembershipGovernor:
        """Prompt 3 — Governance and Membership Model: temporary society
        membership derived live from presence, composed with permanent
        membership (self._membership_registry) into effective_societies()."""
        return self._membership_governor

    def effective_societies(self, actor_id: str) -> frozenset[str]:
        """permanent_societies UNION temporary_societies for actor_id —
        see MembershipGovernor.effective_societies."""
        return self._membership_governor.effective_societies(actor_id)

    def move_actor(self, actor_id: str, space_id: str, activity: str = "",
                    confidence: float = 1.0, source: str = "") -> bool:
        """Record actor_id's presence at space_id — closes any prior open
        Presence and opens a new one (PresenceTimeline.move_actor's own
        invariant enforcement). Returns False if space_id isn't a real
        Space in this PlanetaryRuntime's geography."""
        result = self.presence.move_actor(actor_id, space_id, activity=activity,
                                           confidence=confidence, source=source)
        if result is not None:
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                actor_id=actor_id,
                description=f"Actor {actor_id} moved to space {space_id}",
                payload={"actor_id": actor_id, "space_id": space_id, "activity": activity},
            ))
        return result is not None

    def host_society(self, entity_id: str, society_id: str) -> GeographicEntity | None:
        """Host a society at any geographic entity, any tier — the
        decoupling mechanism itself. Society must be registered with THIS
        PlanetaryRuntime, same validation style as join_federation."""
        if self.get_society_runtime(society_id) is None:
            return None
        prior_entity = self._geo_registry.entity_for_society(society_id)
        entity = self._geo_registry.host_society(entity_id, society_id)
        if entity is not None:
            if prior_entity is not None and prior_entity.entity_id != entity_id:
                for rel in self._relationships.relationships_between(
                    society_id, prior_entity.entity_id, RelationshipKind.HOSTED_BY,
                ):
                    self._relationships.remove(rel.relationship_id)
            self._relationships.add(society_id, entity_id, RelationshipKind.HOSTED_BY)
            self._save_relationships()
            self._save_geography()
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                description=f"Society {society_id} hosted by {entity_id}",
                payload={"entity_id": entity_id, "society_id": society_id},
            ))
        return entity

    def unhost_society(self, entity_id: str, society_id: str) -> GeographicEntity | None:
        """Inverse of host_society() — removes the hosting relationship so a
        deleted society doesn't leave an orphaned entry in that entity's
        hosted_society_ids (which would otherwise permanently block deleting
        that geographic entity, since delete_geo_entity refuses to remove
        anything still hosting a society)."""
        entity = self._geo_registry.unhost_society(entity_id, society_id)
        if entity is not None:
            for rel in self._relationships.relationships_between(
                society_id, entity_id, RelationshipKind.HOSTED_BY,
            ):
                self._relationships.remove(rel.relationship_id)
            self._save_relationships()
            self._save_geography()
        return entity

    def entity_for_society(self, society_id: str) -> GeographicEntity | None:
        return self._geo_registry.entity_for_society(society_id)

    def set_geo_world_location(self, entity_id: str, world_location_id: str | None) -> GeographicEntity | None:
        """Link a geographic entity (any tier) to a real WorldLocation
        (kernel/society/world.py — real latitude/longitude), the previously
        unwired half of GeographicEntity.world_location_id's own docstring.
        Validates world_location_id resolves to a real, planet-scoped
        WorldLocation (self.world, not any Society's own SharedWorld) —
        the registry itself can't do that check, it has no reference to
        PlanetaryRuntime.world."""
        if world_location_id is not None and self.world.get_location(world_location_id) is None:
            return None
        entity = self._geo_registry.set_world_location(entity_id, world_location_id)
        if entity is not None:
            self._save_geography()
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                description=f"Entity {entity_id} linked to world_location {world_location_id}",
                payload={"entity_id": entity_id, "world_location_id": world_location_id},
            ))
        return entity

    def create_geo_from_address(
        self, *, country: str, state: str = "", county: str = "", city: str = "",
        street: str = "", building_name: str = "", latitude: float, longitude: float,
        display_address: str = "", attributes: dict[str, Any] | None = None,
    ) -> GeographicEntity | None:
        """Real-world address ingestion (a geocoded search result, not
        manual tier-by-tier entity creation): finds-or-creates the real
        Country/State/County/City/Street chain under a real "Earth" Planet
        (reusing any tier that already exists by name — searching "San
        Francisco" twice must not create two San Franciscos), creates a
        NEW Building for this specific address (a street address IS the
        specific thing being added; not deduplicated by name the way
        broader tiers are), and links it to a real WorldLocation carrying
        the actual geocoded lat/lon. `attributes` passes straight through
        to WorldLocation.attributes (already free-form — no new fields
        added here) — e.g. a real polygon footprint under
        attributes["polygon"] when the geocoder returned real way/
        relation geometry, not a fabricated shape.

        Every tier from State down MUST have SOME name — GeographicRegistry.
        add_child enforces strict tier adjacency, so a real-world address
        missing an intermediate tier (many countries have no "state"/
        "county" concept the way the US does) still needs an entity to
        occupy that tier structurally. Falls back to the nearest known
        name one tier up rather than inventing "N/A" — an imperfect but
        honest compromise this rigid 8-tier hierarchy forces; documented
        here, not hidden."""
        if not country.strip():
            return None
        state = state.strip() or country
        county = county.strip() or state
        city = city.strip() or county
        street = street.strip() or f"{city} (unnamed street)"

        planet = self._geo_registry.find_or_create(GeographicEntityType.PLANET, "Earth")
        if planet is None:
            return None
        country_entity = self._geo_registry.find_or_create(GeographicEntityType.COUNTRY, country, planet.entity_id)
        state_entity = self._geo_registry.find_or_create(GeographicEntityType.STATE, state, country_entity.entity_id) if country_entity else None
        county_entity = self._geo_registry.find_or_create(GeographicEntityType.COUNTY, county, state_entity.entity_id) if state_entity else None
        city_entity = self._geo_registry.find_or_create(GeographicEntityType.CITY, city, county_entity.entity_id) if county_entity else None
        street_entity = self._geo_registry.find_or_create(GeographicEntityType.STREET, street, city_entity.entity_id) if city_entity else None
        if street_entity is None:
            return None

        building = self._geo_registry.create(
            GeographicEntityType.BUILDING, building_name.strip() or street, parent_id=street_entity.entity_id,
        )
        if building is None:
            return None

        from src.monkey_brain.kernel.society.world import WorldLocation
        location = WorldLocation(
            name=building.name, address=display_address or street,
            latitude=latitude, longitude=longitude,
            attributes=attributes or {},
        )
        self.add_world_location(location)
        building = self._geo_registry.set_world_location(building.entity_id, location.location_id)
        self._save_geography()
        self.context_stream.publish(ContextEvent(
            event_type=ContextEventType.WORLD_UPDATE,
            description=f"Created {building.name} from real address: {display_address or street}",
            payload={"entity_id": building.entity_id, "world_location_id": location.location_id},
        ))
        return building

    def _ensure_city_and_space_under(
        self, canonical_root: "GeographicEntity",
    ) -> tuple["GeographicEntity", "GeographicEntity", list[str]]:
        """Find (never duplicate) or create the minimal real City-tier
        entity under canonical_root, and a real Space under that City —
        shared by reconcile_default_geography (below) and
        ensure_default_bootstrap_space (below). Extracted from
        reconcile_default_geography's own body — same logic, same
        find-before-create discipline, now usable independently of
        whether a synthetic bootstrap chain exists to migrate away from
        (see ensure_default_bootstrap_space's own docstring for why that
        independence matters)."""
        def _find_city(root_id: str) -> GeographicEntity | None:
            stack = list(self._geo_registry.children_of(root_id))
            while stack:
                node = stack.pop()
                if node.entity_type == GeographicEntityType.CITY:
                    return node
                stack.extend(self._geo_registry.children_of(node.entity_id))
            return None

        created_ids: list[str] = []
        target_city = _find_city(canonical_root.entity_id)
        if target_city is None:
            current = canonical_root
            for tier in (GeographicEntityType.COUNTRY, GeographicEntityType.STATE,
                         GeographicEntityType.COUNTY, GeographicEntityType.CITY):
                child = next(
                    (c for c in self._geo_registry.children_of(current.entity_id) if c.entity_type == tier),
                    None,
                )
                if child is None:
                    child = self._geo_registry.create(
                        tier, f"{canonical_root.name} {tier.value.capitalize()}", parent_id=current.entity_id,
                    )
                    created_ids.append(child.entity_id)
                current = child
            target_city = current

        target_space = next(
            (c for c in self._geo_registry.children_of(target_city.entity_id)
             if c.entity_type == GeographicEntityType.SPACE),
            None,
        )
        if target_space is None:
            street = self._geo_registry.create(
                GeographicEntityType.STREET, f"{target_city.name} Street", parent_id=target_city.entity_id,
            )
            building = self._geo_registry.create(
                GeographicEntityType.BUILDING, f"{target_city.name} Building", parent_id=street.entity_id,
            )
            target_space = self._geo_registry.create(
                GeographicEntityType.SPACE, f"{target_city.name} Space", parent_id=building.entity_id,
            )
            created_ids += [street.entity_id, building.entity_id, target_space.entity_id]

        return target_city, target_space, created_ids

    def ensure_default_bootstrap_space(self, canonical_root_name: str = "Earth") -> str | None:
        """Real fix for a genuine seeding race (Qualification Gap Closure,
        BUG-001): reconcile_default_geography's only path to establishing
        _default_bootstrap_space_id is conditioned on a synthetic "Default
        Planet" bootstrap chain existing to migrate away from
        (`if self._default_planet is None: return ... performed=False`).
        That chain only ever gets created in __init__ when the
        GeographicRegistry is EMPTY at boot -- a server that boots against
        Redis holding PARTIAL geography (exactly what an earlier,
        interrupted `seed_world.py seed` run leaves behind: Earth/USA/
        California created, but the run aborted before every Actor was
        registered) never creates the synthetic chain, so
        reconcile_default_geography silently no-ops forever on that boot,
        and register_actor()'s home_space_id="" fallback
        (`_default_bootstrap_space_id is None`) keeps failing --
        reproducible given a specific partial Redis state, not timing luck.

        This method is a second, narrower readiness primitive: "ensure a
        usable default bootstrap Space exists, given a real canonical root,
        regardless of whether a synthetic chain is present to clean up."
        Idempotent (a real, already-set, still-resolving
        _default_bootstrap_space_id is returned unchanged) and side-effect-
        free until a real canonical root actually exists (returns None --
        an honest "nothing to bootstrap from yet", not an error -- rather
        than fabricating one). reconcile_default_geography's own synthetic-
        chain-cleanup job is unchanged; this does not replace it, it just
        no longer depends on it having already run."""
        if self._default_bootstrap_space_id is not None:
            existing = self._geo_registry.get(self._default_bootstrap_space_id)
            if existing is not None and existing.entity_type == GeographicEntityType.SPACE:
                return self._default_bootstrap_space_id

        canonical_root = next(
            (e for e in self._geo_registry.all(GeographicEntityType.PLANET)
             if e.name.strip().lower() == canonical_root_name.strip().lower()),
            None,
        )
        if canonical_root is None:
            return None

        _target_city, target_space, created_ids = self._ensure_city_and_space_under(canonical_root)
        self._default_bootstrap_space_id = target_space.entity_id
        if created_ids:
            self._save_geography()
        return self._default_bootstrap_space_id

    def reconcile_default_geography(self, canonical_root_name: str = "Earth") -> "GeographyReconciliationResult":
        """Explicit, idempotent cleanup of the eager bootstrap "Default
        Planet -> ... -> Default Space" chain __init__ creates the first
        time this PlanetaryRuntime boots against an empty GeographicRegistry
        (see the block right after _load_geography() above) — that chain
        exists purely so register_actor()'s "every Society needs a real
        Space" invariant holds even before any real geography exists, and
        __init__'s own eager timing means it always runs BEFORE a caller
        (e.g. scripts/seed_world.py) has a chance to create a real
        canonical root like "Earth" — so the two end up as separate sibling
        hierarchies, and any Society that got auto-hosted at Default City
        (only the bootstrap Default Society, by construction) looks like
        it's hosted somewhere synthetic and duplicated.

        NOT called automatically at boot — deliberately opt-in, so this
        does not change __init__'s existing invariant for fresh/unseeded
        environments (isolated unit tests, a dev boot before anyone has
        run the seed script). Call this once, explicitly, after a real
        canonical root exists and every real Society has already been
        hosted under it (see scripts/seed_world.py's call site).

        Finds the real Planet named canonical_root_name (never creates
        one — a missing canonical root is a genuine no-op, not something
        to fabricate); re-hosts every Society currently hosted anywhere in
        the synthetic subtree onto a real City under that root (reusing
        one if it already exists, else building only the minimal missing
        tail — never duplicating a tier that's already there); gives the
        bootstrap Space fallback (_default_bootstrap_space_id) a real
        replacement under that City, since reconciliation is about to
        delete the synthetic one it used to point at; then deletes the
        now-unreferenced synthetic chain via delete_geo_entity (which
        already refuses safely if anything still hosts a Society there)."""
        canonical_root = next(
            (e for e in self._geo_registry.all(GeographicEntityType.PLANET)
             if e.name.strip().lower() == canonical_root_name.strip().lower()),
            None,
        )
        if canonical_root is None:
            return GeographyReconciliationResult(performed=False, reason=f"no {canonical_root_name!r} root found")
        if self._default_planet is None:
            return GeographyReconciliationResult(
                performed=False, canonical_root_id=canonical_root.entity_id,
                reason="no synthetic Default Planet chain present",
            )

        def _subtree(entity_id: str) -> list[GeographicEntity]:
            entity = self._geo_registry.get(entity_id)
            if entity is None:
                return []
            out = [entity]
            for cid in entity.child_ids:
                out.extend(_subtree(cid))
            return out

        synthetic_nodes = _subtree(self._default_planet.entity_id)
        hosted_society_ids = {sid for n in synthetic_nodes for sid in n.hosted_society_ids}

        target_city, target_space, created_ids = self._ensure_city_and_space_under(canonical_root)

        for society_id in hosted_society_ids:
            self.host_society(target_city.entity_id, society_id)

        # Reconciliation is about to delete the synthetic chain that used to
        # back _default_bootstrap_space_id's implicit-Space fallback for any
        # Society created with no explicit host — _ensure_city_and_space_under
        # (above) already gave it a real replacement, or register_actor()'s
        # fallback would break for the next Society created with no explicit
        # host.
        self._default_bootstrap_space_id = target_space.entity_id

        deleted = self.delete_geo_entity(self._default_planet.entity_id)
        if deleted is None:
            return GeographyReconciliationResult(
                performed=False, canonical_root_id=canonical_root.entity_id,
                target_city_id=target_city.entity_id, migrated_society_ids=tuple(hosted_society_ids),
                created_entity_ids=tuple(created_ids),
                reason="synthetic chain still hosts a society after migration — not deleted",
            )
        self._save_geography()
        return GeographyReconciliationResult(
            performed=True, canonical_root_id=canonical_root.entity_id,
            target_city_id=target_city.entity_id, migrated_society_ids=tuple(hosted_society_ids),
            created_entity_ids=tuple(created_ids), deleted_entity_ids=deleted,
        )

    def delete_geo_entity(self, entity_id: str) -> tuple[str, ...] | None:
        """Real deletion (any tier, cascades to descendants — see
        GeographicRegistry.delete_subtree). Refuses to delete anything (at
        or below entity_id) currently hosting a real Society, so a Society
        is never silently left with no Space at all — the caller must
        re-host it elsewhere (a real address) first.

        This includes the bootstrap Default chain — no special-cased
        protection for it beyond that same "still hosts a Society" check.
        Once every Society has been re-hosted at a real location, deleting
        Default is exactly as safe as deleting anything else; keeping it
        artificially undeletable after that point would be protecting
        nothing real. (__init__'s _find_default no longer falls back to
        "the first entity of this type" when "Default X" is gone, so
        deleting it doesn't risk a real entity getting silently mistaken
        for the bootstrap default on the next restart — see that fix's
        own comment.)

        Returns None for either refusal or a nonexistent entity_id
        (the API route distinguishes the two by checking existence
        first); the tuple of removed ids on success."""
        entity = self._geo_registry.get(entity_id)
        if entity is None:
            return None

        def _subtree_ids(current_id: str) -> set[str]:
            current = self._geo_registry.get(current_id)
            if current is None:
                return set()
            ids = {current_id}
            for child_id in current.child_ids:
                ids |= _subtree_ids(child_id)
            return ids

        subtree = _subtree_ids(entity_id)
        if any(self._geo_registry.get(eid) and self._geo_registry.get(eid).hosted_society_ids for eid in subtree):
            return None

        removed = self._geo_registry.delete_subtree(entity_id)
        if removed:
            self._save_geography()
            removed_set = set(removed)
            # Drop stale references to whatever was just deleted — a live
            # PlanetaryRuntime keeps these as direct object handles, not
            # id lookups, so deletion alone wouldn't clear them.
            for attr in ("_default_planet", "_default_country", "_default_state",
                         "_default_county", "_default_city", "_default_space"):
                current = getattr(self, attr, None)
                if current is not None and current.entity_id in removed_set:
                    setattr(self, attr, None)
            if self._default_bootstrap_space_id in removed_set:
                self._default_bootstrap_space_id = None
            self.context_stream.publish(ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                description=f"Deleted {entity.name} and {len(removed) - 1} descendant(s)",
                payload={"entity_id": entity_id, "removed_ids": list(removed)},
            ))
        return removed

    async def tick_geographic_entity(self, entity_id: str) -> GeographicTickResult:
        """Tick one geographic entity: every society hosted there, then
        every child entity recursively — see GeographicEntityRuntime."""
        if self._geo_registry.get(entity_id) is None:
            return GeographicTickResult(entity_id=entity_id)
        result = await GeographicEntityRuntime(
            self._geo_registry,
            entity_id, 
            self._societies.get,
            presence=self._presence, 
            actor_ticker=self._tick_present_actor,
            membership_reconciler=self._membership_governor.reconcile,
            temporary_membership_lookup=self._temporary_membership_lookup,
            effective_membership_lookup=self._effective_membership_lookup,
        ).tick()
        self.context_stream.publish(ContextEvent(
            event_type=ContextEventType.SOCIETY_TICK,
            description=(
                f"Geographic tick ({entity_id}): {len(result.societies_ticked)} society(ies), "
                f"{len(result.children_ticked)} child entity(ies)"
            ),
            payload={"entity_id": entity_id, "societies_ticked": list(result.societies_ticked)},
        ))
        return result


    def create_country(self, name: str, description: str = "") -> Country:
        return self.create_geographic_entity(
            GeographicEntityType.COUNTRY, name, self._default_planet.entity_id, description,
        )

    def create_city(self, name: str, country_id: str, description: str = "") -> City | None:
        """
        Create a city under an existing country. Bridges the compat
        Country->City shape onto the real State->County->City tier chain by
        auto-provisioning a default State/County under the given country if
        one doesn't already exist."""
        country = self._geo_registry.get(country_id)
        if country is None or country.entity_type != GeographicEntityType.COUNTRY:
            return None
        state = next((c for c in self._geo_registry.children_of(country_id)), None)
        if state is None:
            state = self._geo_registry.create(GeographicEntityType.STATE, f"{name} State", parent_id=country_id)
            self._save_geography()
        county = next((c for c in self._geo_registry.children_of(state.entity_id)), None)
        if county is None:
            county = self._geo_registry.create(GeographicEntityType.COUNTY, f"{name} County", parent_id=state.entity_id)
            self._save_geography()
        return self.create_geographic_entity(GeographicEntityType.CITY, name, county.entity_id, description)

    def assign_society_to_city(self, society_id: str, city_id: str) -> City | None:
        return self.host_society(city_id, society_id)

    def get_city_for_society(self, society_id: str) -> City | None:
        entity = self._geo_registry.entity_for_society(society_id)
        if entity is None:
            return None
        return self._geo_registry.ancestor_of_type(entity.entity_id, GeographicEntityType.CITY)

    def get_country_for_society(self, society_id: str) -> Country | None:
        entity = self._geo_registry.entity_for_society(society_id)
        if entity is None:
            return None
        return self._geo_registry.ancestor_of_type(entity.entity_id, GeographicEntityType.COUNTRY)
    

    async def tick_city(self, city_id: str) -> CityTickResult:
        """Backward-compat wrapper: tick_geographic_entity() now does the
        real work (recursing through any children too, not just direct
        societies) — this adapts its GeographicTickResult into the
        original CityTickResult shape existing callers expect."""
        result = await self.tick_geographic_entity(city_id)
        return CityTickResult(
            city_id=city_id,
            societies_ticked=result.societies_ticked,
            actors_ticked_total=result.actors_ticked_total,
            interactions_routed_total=result.interactions_routed_total,
            duration_ms=result.duration_ms,
        )

    async def tick_country(self, country_id: str) -> CountryTickResult:
        """Backward-compat wrapper — see tick_city()."""
        result = await self.tick_geographic_entity(country_id)
        return CountryTickResult(
            country_id=country_id,
            cities_ticked=result.children_ticked,
            societies_ticked_total=len(result.societies_ticked),
            actors_ticked_total=result.actors_ticked_total,
            interactions_routed_total=result.interactions_routed_total,
            duration_ms=result.duration_ms,
        )


    async def execute_actor_request(self, actor_id: str, prompt_request: Any) -> Any:
        """
        Execute a request through the normal planetary scheduler path.
        This ensures that the actor's request is executed in the context of
        its current societies and space, and that any temporary memberships
        are reconciled before execution. It also ensures that the planetary tick lock is respected,
        so that only one tick can run at a time. The prompt endpoint must use this boundary instead of constructing a
        temporary cognitive state or invoking a cognitive engine directly.

        # the planetary runtime takes care of
            # 1. actor/society/geography resolution,
            # 2. recursive traversal,
            # 3. context/world updates,
            # 4. and actor coordination

        Single-Responsibility split: each phase below is one reason to change
        (geography rules, membership rules, locking strategy, tick execution,
        post-tick coordination/telemetry) and lives in its own method. This
        orchestrator's only job is the sequencing and lock lifecycle.
        """
        space = self._resolve_actor_space(actor_id)
        society_ids = self._resolve_actor_societies(actor_id, space)
        propagation_mode = self._resolve_propagation_mode(prompt_request)
        propagation_scope, propagation_target_actor_id = self._resolve_propagation_scope(prompt_request)
        await self._reserve_planetary_cycle(actor_id)

        try:
            outcome = await self._run_actor_tick(actor_id, prompt_request, society_ids)
            return await self._finalize_actor_execution(
                actor_id, society_ids, outcome, propagation_mode=propagation_mode,
                propagation_scope=propagation_scope,
                propagation_target_actor_id=propagation_target_actor_id,
            )
        finally:
            self._release_planetary_cycle_lock()
            # _reserve_planetary_cycle acquired self._tick_lock itself
            # (see its own docstring for why) — release it here so every
            # exit path (success, business-logic error, or an unexpected
            # exception) frees it, matching the Redis lock release right
            # above.
            if self._tick_lock.locked():
                self._tick_lock.release()

    @staticmethod
    def _resolve_propagation_mode(prompt_request: Any) -> "PropagationMode":
        """Resolve the propagation mode for this request's post-tick
        coordination fan-out. Mirrors resolve_run_type's meta-override
        pattern (api/helpers/prompt_helpers.py): callers opt in via
        ``meta.propagation_mode``. Every propagation request has a mode —
        unset or unrecognized always resolves to SYNCHRONOUS, the
        architectural default."""
        meta = getattr(prompt_request, "meta", None) or {}
        raw = meta.get("propagation_mode") if isinstance(meta, dict) else None
        if raw is None:
            return PropagationMode.SYNCHRONOUS
        try:
            return PropagationMode(str(raw).upper())
        except ValueError:
            logger.warning(
                "execute_actor_request: unknown propagation_mode %r — defaulting to SYNCHRONOUS", raw,
            )
            return PropagationMode.SYNCHRONOUS

    @staticmethod
    def _resolve_propagation_scope(prompt_request: Any) -> tuple["PropagationScope", str | None]:
        """Resolve the propagation scope (and, for POINT_TO_POINT, the
        target actor) for this request's post-tick coordination fan-out.
        Same meta-override pattern as _resolve_propagation_mode: callers
        opt in via ``meta.propagation_scope`` +
        ``meta.propagation_target_actor_id``. Every propagation request
        may specify a scope — unset, unrecognized, or a POINT_TO_POINT
        request missing its target always resolves to BROADCAST, the
        architectural default."""
        meta = getattr(prompt_request, "meta", None) or {}
        if not isinstance(meta, dict):
            return PropagationScope.BROADCAST, None
        raw = meta.get("propagation_scope")
        target_actor_id = meta.get("propagation_target_actor_id")
        if raw is None:
            return PropagationScope.BROADCAST, None
        try:
            scope = PropagationScope(str(raw).upper())
        except ValueError:
            logger.warning(
                "execute_actor_request: unknown propagation_scope %r — defaulting to BROADCAST", raw,
            )
            return PropagationScope.BROADCAST, None
        if scope is PropagationScope.POINT_TO_POINT and not target_actor_id:
            logger.warning(
                "execute_actor_request: propagation_scope=POINT_TO_POINT requires "
                "meta.propagation_target_actor_id — defaulting to BROADCAST",
            )
            return PropagationScope.BROADCAST, None
        return scope, target_actor_id

    #############################################################################################
    #                                         Actor presence                                    #
    #############################################################################################

    def _resolve_actor_space(self, actor_id: str) -> GeographicEntity:
        """Resolve and validate the Space this actor is currently present at."""
        # get the current presence of the actor
        presence = self._presence.current(actor_id)
        if presence is None or not presence.is_open():
            raise LookupError(f"Actor {actor_id!r} is not associated with a space")
        space = self._geo_registry.get(presence.space_id)
        if space is None or space.entity_type != GeographicEntityType.SPACE:
            raise LookupError(f"Actor {actor_id!r} is associated with an invalid space")
        return space

    #############################################################################################
    #                                        Actor Societies                                    #
    #############################################################################################

    def _resolve_actor_societies(self, actor_id: str, space: GeographicEntity) -> tuple[str, ...]:
        """Resolve and validate the societies this actor's request executes against."""
        # reconcile the actor's membership before executing the request
        self._membership_governor.reconcile(actor_id)

        # get the effective permenanet society memberships for the actor ,
        #  if the actor is not registered in any society, raise an error
        society_ids = tuple(self.effective_societies(actor_id))
        if not society_ids:
            raise LookupError(f"Actor {actor_id!r} is not registered in a society")

        # A society has one geographic host in the hierarchy.  Its presence
        # at a space is inherited from that host; it is not copied onto every
        # geographic entity.  Permanent memberships remain independent of
        # location, while temporary memberships must describe this actor's
        # current space exactly.
        associated_society_ids = self._geo_registry.societies_at_or_above(space.entity_id)

        # reconcile the actor's temporary membership with the associated societies,
        # if the actor has temporary membership outside its current space, raise an error
        temporary_society_ids = set(self._temporary_membership_lookup(actor_id))
        if not temporary_society_ids.issubset(associated_society_ids):
            self._membership_governor.reconcile(actor_id)
            temporary_society_ids = set(self._temporary_membership_lookup(actor_id))
        if not temporary_society_ids.issubset(associated_society_ids):
            raise LookupError(
                f"Actor {actor_id!r} has temporary membership outside its current space"
            )

        # validate that each society has a  the geographic hierarchy and has at least one
        # space associated with it, if not raise an error
        for society_id in society_ids:
            if self._geo_registry.entity_for_society(society_id) is None:
                raise LookupError(f"Society {society_id!r} is not in the geographic hierarchy")
            self._geo_registry.validate_society_has_space(society_id)

        # get the runtime for each of the soceties that the actor belongs to
        actor_societies = [
            self.get_society_runtime(society_id)
            for society_id in society_ids
        ]

        # check that society runtime is not none
        actor_societies = [sr for sr in actor_societies if sr is not None]

        # check if the actor exists in the society
        if not any(sr.get_actor(actor_id) is not None for sr in actor_societies):
            raise LookupError(f"Actor {actor_id!r} has no runtime registration")

        return society_ids

    #############################################################################################
    #                                    Planetary Cycle Lock                                   #
    #############################################################################################

    _TICK_LOCK_WAIT_SECONDS = 90.0
    _CYCLE_LOCK_WAIT_SECONDS = 60.0
    _CYCLE_LOCK_POLL_INTERVAL_SECONDS = 0.5

    async def _reserve_planetary_cycle(self, actor_id: str) -> None:
        """Claim exclusive rights to run this actor's tick: in-process first,
        then across replicas. Pairs with self._release_planetary_cycle_lock()
        AND releasing self._tick_lock (both — see execute_actor_request's
        finally), which the caller runs regardless of outcome, but ONLY
        once this method returns without raising (an exception here means
        this call never entered its caller's try/finally at all, so
        whatever this method itself acquired before failing must be
        cleaned up right here, not left for the caller to guess at).

        Real reliability fix (confirmed live, not hypothetical): this used
        to fail every real /prompt request immediately
        ("A planetary tick is already running") on ANY overlap with the
        background auto-tick loop's cycle() sweep (_auto_tick_loop, every
        5 minutes) — which ticks every active actor SEQUENTIALLY (see
        docs/adr/016-performance-gate9.md) and holds this exact
        self._tick_lock for the WHOLE sweep, easily minutes long with more
        than a handful of actors. self._tick_lock is a plain asyncio.Lock
        — awaiting its own .acquire() already queues correctly and safely
        on its own (no polling needed for the in-process side); only a
        genuine, bounded timeout should surface as an error now, not
        routine momentary contention with a background job.
        """
        try:
            await asyncio.wait_for(self._tick_lock.acquire(), timeout=self._TICK_LOCK_WAIT_SECONDS)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Actor {actor_id!r} request timed out after {self._TICK_LOCK_WAIT_SECONDS:.0f}s "
                "waiting for the in-process planetary tick lock (held by the background "
                "auto-tick cycle or another request)"
            ) from None

        # Prevent overlapping ticks ACROSS processes (distributed lock) —
        # the same lock cycle() uses, since both read/mutate the same
        # shared world state in Redis. Real bounded retry, not the
        # previous one-shot attempt: _acquire_planetary_cycle_lock's own
        # `timeout_seconds` only ever sets the Redis key's OWN ttl once
        # acquired, never how long THIS caller waits trying to acquire it
        # — a single miss used to fail the whole request immediately.
        # cycle()'s own call site is deliberately left as its existing
        # one-shot skip-if-busy (correct for a periodic job that simply
        # runs again next interval) — this retry loop is only for the
        # real, synchronous user-request path, which has no "next
        # interval" to fall back on.
        deadline = time.monotonic() + self._CYCLE_LOCK_WAIT_SECONDS
        while not self._acquire_planetary_cycle_lock(timeout_seconds=self._CYCLE_LOCK_WAIT_SECONDS):
            if time.monotonic() >= deadline:
                self._tick_lock.release()
                raise RuntimeError(
                    f"Actor {actor_id!r} request could not run — another replica held the "
                    f"planetary world-state lock for over {self._CYCLE_LOCK_WAIT_SECONDS:.0f}s"
                )
            await asyncio.sleep(self._CYCLE_LOCK_POLL_INTERVAL_SECONDS)

    async def _run_actor_tick(
        self, actor_id: str, prompt_request: Any, society_ids: tuple[str, ...],
    ) -> _ActorTickOutcome:
        """Load the latest world state and tick this actor through every
        society it belongs to. Caller (execute_actor_request) already
        holds self._tick_lock — acquired by _reserve_planetary_cycle
        before this is ever called — so this no longer acquires it a
        second time itself; asyncio.Lock is not reentrant, and this
        method has exactly one caller (confirmed), so double-acquiring
        here would just deadlock this same task against itself."""
        try:
            # load the global world state from redis before starting the tick,
            # to ensure that the world state is up to date
            self._load_world()
        except Exception:
            logger.debug("execute_actor_request: suppressed exception", exc_info=True)

        # get the realtime mutations to the world from the context stream before starting the tick,
        # to ensure that the world state is up to date

        # TODO: the context stream must be a NATS stream that is shared across all nodes in the cluster,
        # so that the world state is consistent across all nodes

        context_events_before = self.context_stream.event_count
        actor_execution_result = None
        actors_coordinated: set[str] = set()
        spaces_coordinated: set[str] = set()
        # An actor belonging to more than one effective society (e.g. a
        # store "customer" affiliation registering her as an effective
        # member of that store's Team society, alongside her real home
        # society) otherwise gets target_actor_id=actor_id passed to
        # society_runtime.tick() once per society below -- and tick()
        # (society/runtime.py) delivers prompt_request and genuinely
        # re-executes tick_one_actor() for target_actor_id every single
        # time she shows up in that society's active_actors(), with real
        # side effects. Found live: a single "buy a loaf of bread" POST
        # /prompt produced TWO real, independent orders and TWO real
        # wallet debits for the same actor. already_ticked reuses
        # tick()'s own exclude_actor_ids (built for the analogous
        # geography-traversal-overlap case) to make sure her real request
        # is delivered and executed in exactly one of her societies, while
        # every OTHER actor in the remaining societies still gets ticked
        # normally.
        already_ticked: set[str] = set()

        # Opt-in escape hatch from ticking actor_id's whole society (see
        # SocietyRuntime.tick's own single_actor_only docstring) —
        # meta.single_actor_only, same override pattern every other
        # per-request meta flag here already uses (_resolve_propagation_mode/
        # _resolve_propagation_scope above).
        meta = getattr(prompt_request, "meta", None) or {}
        single_actor_only = bool(meta.get("single_actor_only")) if isinstance(meta, dict) else False

        for society_id in society_ids:
            society_runtime = self.get_society_runtime(society_id)
            if society_runtime is None:
                continue

            # tick all the actors in the society that the actor belongs to, and get the result of the tick
            tick_result = await society_runtime.tick(
                target_actor_id=actor_id, prompt_request=prompt_request,
                exclude_actor_ids=frozenset(already_ticked) or None,
                single_actor_only=single_actor_only,
            )

            actors_coordinated.update(a.actor_id for a in society_runtime.active_actors())
            hosting_entity = self._geo_registry.entity_for_society(society_id)
            if hosting_entity is not None:
                spaces_coordinated.add(hosting_entity.entity_id)

            # if the actor execution result is not None, it means that the actor was reached and executed,
            # so we can return the result
            if tick_result.actor_execution_result is not None:
                actor_execution_result = tick_result.actor_execution_result
                already_ticked.add(actor_id)

        if actor_execution_result is None:
            raise RuntimeError(
                f"Actor {actor_id!r} was not reached by its effective societies"
            )

        return _ActorTickOutcome(
            actor_execution_result=actor_execution_result,
            actors_coordinated=actors_coordinated,
            spaces_coordinated=spaces_coordinated,
            context_events_before=context_events_before,
        )

    #############################################################################################
    #                            Actor to Actor Communication                                   #
    #############################################################################################

    async def _finalize_actor_execution(
        self, actor_id: str, society_ids: tuple[str, ...], outcome: _ActorTickOutcome,
        propagation_mode: PropagationMode = PropagationMode.SYNCHRONOUS,
        propagation_scope: PropagationScope = PropagationScope.BROADCAST,
        propagation_target_actor_id: str | None = None,
    ) -> Any:
        """Propagate coordination events to subscribed societies, build the
        negotiation trace, publish telemetry, and attach the resulting
        execution_scope/coordination_trace onto the tick's result.

        propagation_mode selects how the coordination fan-out below runs:
        SYNCHRONOUS (default) awaits it inline before returning, so the
        caller's result reflects the fully-propagated outcome — required
        for negotiation/transaction/consensus/approval workflows.
        ASYNCHRONOUS schedules it as a background task and returns the
        actor's own result immediately with a "scheduled" propagation
        stub; the real propagation stats are delivered afterward via
        _propagate_coordination_background()'s completion event.

        propagation_scope selects who receives it, independent of mode:
        BROADCAST (default) fans out to every policy-eligible actor in
        each subscribed society; POINT_TO_POINT delivers to exactly
        propagation_target_actor_id."""
        actor_execution_result = outcome.actor_execution_result
        context_events_before = outcome.context_events_before
        actors_coordinated = outcome.actors_coordinated
        spaces_coordinated = outcome.spaces_coordinated

        # True Multi-Actor Coordination: propagate whatever real
        # domain events the initiating actor's own tick(s) just
        # published to OTHER societies that subscribed to them.


        # TODO: the cordination trace should be stored in the context stream, so that it can be queried later for debugging and analysis.
        #  It should also be stored in the actor execution result, so that it can be returned to the caller for debugging and analysis.
        #  The cordination trace should be streamed via NATS to Elasticsearch for analysis and visualization.
        #  The cordination trace should be stored in a separate index in Elasticsearch, so that it can be queried independently of the context stream.  The cordination trace should be stored in a separate index in Elasticsearch, so that it can be queried independently of the actor execution result.  The cordination trace should be stored in a separate index in Elasticsearch, so that it can be queried independently of the actor execution result and the context stream.  The cordination trace should be stored in a separate index in Elasticsearch, so that it can be queried independently of the actor execution result, the context stream, and the actor's own log.
        #  cordination trace should be tsreames to websocket for real time visualization and debugging.
        #  The cordination trace should be streamed to a websocket for real time visualization and debugging.
        #  The cordination trace should be streamed to a websocket for real time visualization and debugging,
        #  and the websocket should be secured with authentication and authorization.
        #  and the websocket should be rate limited to prevent denial of service attacks.

        # builds the full negotiation and reasoning trace for the actor's execution result, if any negotiation happened

        #TODO: negotiation trace should be streamed via NATS to Elasticsearch for analysis and visualization.
        # The negotiation trace should be published to websocket for real time visualization and debugging,
        # and the websocket should be secured with authentication and authorization.
        # and the websocket should be rate limited to prevent denial of service attacks.

        negotiation_scope = self._build_negotiation_trace(actor_id, actor_execution_result)
        if negotiation_scope is not None:
            self._publish_negotiation_metrics(negotiation_scope)
            self._record_decision(
                actor_id, negotiation_scope,
                execution_id=getattr(actor_execution_result, "execution_id", ""),
            )

        execution_scope: dict[str, Any] = {
            "spaces_coordinated": len(spaces_coordinated),
            "societies_coordinated": len(society_ids),
            "actors_coordinated": len(actors_coordinated),
            "graph_nodes_traversed": len(spaces_coordinated),
            "context_events_consumed": context_events_before,
            "context_events_produced": 0,
        }
        if negotiation_scope is not None:
            execution_scope["negotiation"] = negotiation_scope

        if propagation_mode is PropagationMode.ASYNCHRONOUS:
            execution_scope["propagation"] = {
                "mode": PropagationMode.ASYNCHRONOUS.value,
                "scope": propagation_scope.value,
                "target_actor_id": propagation_target_actor_id,
                "status": "scheduled",
                "societies_coordinated": 0,
                "actors_coordinated": 0,
                "propagation_steps": 0,
                "propagation_depth": 0,
                "propagation_latency_ms": 0.0,
                "termination_reason": None,
                "domain_events_seen": [],
            }
            task = asyncio.create_task(self._propagate_coordination_background(
                actor_id=actor_id, society_ids=society_ids,
                context_events_before=context_events_before,
                actors_coordinated=set(actors_coordinated),
                propagation_scope=propagation_scope,
                propagation_target_actor_id=propagation_target_actor_id,
            ))
            self._background_propagation_tasks.add(task)
            task.add_done_callback(self._background_propagation_tasks.discard)
            coordination_trace: tuple[dict[str, Any], ...] = ()
        else:
            report = await self._propagate_and_report(
                actor_id=actor_id, society_ids=society_ids,
                context_events_before=context_events_before,
                actors_coordinated=actors_coordinated,
                propagation_scope=propagation_scope,
                propagation_target_actor_id=propagation_target_actor_id,
            )
            execution_scope["context_events_produced"] = (
                self.context_stream.event_count - context_events_before
            )
            execution_scope["propagation"] = {
                "mode": PropagationMode.SYNCHRONOUS.value,
                "status": "completed",
                **report["propagation_summary"],
            }
            coordination_trace = tuple(report["coordination_trace"])

        import dataclasses
        if dataclasses.is_dataclass(actor_execution_result):
            return dataclasses.replace(
                actor_execution_result,
                execution_scope=execution_scope,
                coordination_trace=coordination_trace,
            )
        return actor_execution_result

    async def _propagate_and_report(
        self, actor_id: str, society_ids: tuple[str, ...], context_events_before: int,
        actors_coordinated: set[str],
        propagation_scope: PropagationScope = PropagationScope.BROADCAST,
        propagation_target_actor_id: str | None = None,
    ) -> dict[str, Any]:
        """Run _propagate_coordination to completion and publish its
        coordination metrics. Shared by the SYNCHRONOUS path in
        _finalize_actor_execution (awaited inline) and the ASYNCHRONOUS
        path's background task (_propagate_coordination_background)."""
        propagation_start = time.time()
        coordination_trace: list[dict[str, Any]] = []
        (
            propagated_actors, propagated_societies, termination_reason,
            domain_events_seen,
        ) = await self._propagate_coordination(
            from_version=context_events_before,
            trace=coordination_trace,
            already_visited=set(society_ids),
            scope=propagation_scope,
            target_actor_id=propagation_target_actor_id,
            originating_actor_id=actor_id,
        )
        propagation_latency_ms = (time.time() - propagation_start) * 1000
        events_produced = self.context_stream.event_count - context_events_before
        propagation_depth = max((s["depth"] for s in coordination_trace), default=0)

        # publish the coordination metrics to the metrics system, for monitoring and alerting
        self._publish_coordination_metrics(
            societies_coordinated=len(society_ids) + len(propagated_societies),
            actors_coordinated=len(actors_coordinated) + len(propagated_actors),
            events_published=events_produced,
            events_consumed=context_events_before,
            propagation_steps=len(coordination_trace),
            propagation_depth=propagation_depth,
            propagation_latency_ms=propagation_latency_ms,
        )

        return {
            "propagated_actors": propagated_actors,
            "propagated_societies": propagated_societies,
            "coordination_trace": coordination_trace,
            "propagation_summary": {
                "scope": propagation_scope.value,
                "target_actor_id": propagation_target_actor_id,
                "societies_coordinated": len(propagated_societies),
                "actors_coordinated": len(propagated_actors),
                "propagation_steps": len(coordination_trace),
                "propagation_depth": propagation_depth,
                "propagation_latency_ms": round(propagation_latency_ms, 2),
                "termination_reason": termination_reason,
                "domain_events_seen": sorted(domain_events_seen),
            },
        }

    async def _propagate_coordination_background(
        self, actor_id: str, society_ids: tuple[str, ...],
        context_events_before: int, actors_coordinated: set[str],
        propagation_scope: PropagationScope = PropagationScope.BROADCAST,
        propagation_target_actor_id: str | None = None,
    ) -> None:
        """ASYNCHRONOUS propagation mode: runs after execute_actor_request
        has already returned its result to the caller. Delivers the
        outcome as a completion event on Redis pub/sub — the same
        non-fatal-on-failure channel _propagate_coordination itself
        already publishes per-society broadcasts to — instead of the
        original execution thread."""
        try:
            report = await self._propagate_and_report(
                actor_id=actor_id, society_ids=society_ids,
                context_events_before=context_events_before,
                actors_coordinated=actors_coordinated,
                propagation_scope=propagation_scope,
                propagation_target_actor_id=propagation_target_actor_id,
            )
        except Exception:
            logger.exception(
                "background propagation failed for actor %r (society_ids=%r)",
                actor_id, society_ids,
            )
            return

        if self._redis is not None:
            try:
                self._redis.publish(
                    f"monkeybrain.propagation.completed.{actor_id}",
                    json.dumps({
                        "actor_id": actor_id,
                        "mode": PropagationMode.ASYNCHRONOUS.value,
                        **report["propagation_summary"],
                    }),
                )
            except Exception:
                logger.debug(
                    "_propagate_coordination_background: redis publish failed (non-fatal)",
                    exc_info=True,
                )

    async def _propagate_coordination(
        self, from_version: int, trace: list[dict[str, Any]],
        already_visited: set[str], max_depth: int = 6,
        scope: PropagationScope = PropagationScope.BROADCAST,
        target_actor_id: str | None = None,
        originating_actor_id: str | None = None,
    ) -> tuple[set[str], set[str], str, set[str]]:
        """
        True Multi-Actor Coordination: propagate real world-mutation
        events to whichever Societies subscribed to them — never a full
        traversal, never polling, no hidden actor-to-actor dependency.

            World State Changes -> Relevant Society -> Determine Affected
            Actors -> Coordinate Those Actors -> Affected Actors Think ->
            Affected Actors Act -> World State Changes -> Repeat Until
            Stable

        Events propagate only to the actor and its affiliates not all the actors in the society
        This enusres that only those actors needed in the transaction know about the transaction

        scope=POINT_TO_POINT short-circuits straight to
        _propagate_point_to_point below — a single hop to target_actor_id,
        not this method's multi-round society cascade. scope=BROADCAST
        (default) is everything below, with recipients inside each
        subscribed society now filtered through resolve_communication()
        rather than ticked unconditionally.
        """
        if scope is PropagationScope.POINT_TO_POINT:
            return await self._propagate_point_to_point(
                from_version=from_version, trace=trace,
                target_actor_id=target_actor_id, originating_actor_id=originating_actor_id,
            )

        visited_society_ids: set[str] = set(already_visited)
        actors_coordinated: set[str] = set()
        all_domain_events: set[str] = set()
        version_cursor = from_version
        termination_reason = "stable"

        for depth in range(1, max_depth + 1):

            #TODO: replay latest from stored events in the event store or consume from the event stream

            #TODO: NO need to make this so complicated just consume the stream in batches
            # 1. find the relavant socities to which the actor belongs
            # 2. find the actor affiliations
            # 3. find if the affilation is in the allowed society
            # 4. send message to affiliate
            # 5. affiliate ticks on reciving message
            # 6. affiliate updates local belief
            # 7. affiliate generates negotiation trace
            # 8. affiliate returns the execution trace to actor which aggregates it
            # 9. actor manages the transaction
            # FIXED: steps 2-4 now happen per-candidate-actor below via resolve_communication()
            # (real shared-affiliation / same-society policy check) before a society's tick,
            # rather than ticking every active actor in a subscribed society unconditionally.

            new_events = self.context_stream.replay(from_version=version_cursor + 1)
            if not new_events:
                break
            version_cursor = max((e.version for e in new_events), default=version_cursor)

            domain_events = {
                e.payload.get("domain_event")
                for e in new_events
                if isinstance(e.payload, dict) and e.payload.get("domain_event")
            }
            all_domain_events |= domain_events
            if not domain_events:
                break

            round_societies = [
                sr for sr in self._societies.values()
                if sr.society.society_id not in visited_society_ids
                and set(sr.society.subscribed_events) & domain_events
            ]
            if not round_societies:
                break

            for sr in round_societies:
                matched_events = sorted(domain_events & set(sr.society.subscribed_events))
                broadcast_question = (
                    f"World event(s) occurred: {', '.join(matched_events)}. "
                    f"If one of your available actions directly addresses this, "
                    f"take it now rather than only investigating further."
                )
                if self._redis is not None:
                    try:
                        import json as _json
                        self._redis.publish(
                            f"monkeybrain.society.{sr.society.society_id}.broadcast",
                            _json.dumps({
                                "society_id": sr.society.society_id, "society_name": sr.society.name,
                                "matched_events": matched_events, "question": broadcast_question,
                            }),
                        )
                    except Exception:
                        logger.debug("_propagate_coordination: redis publish failed (non-fatal)", exc_info=True)

                # Policy-driven recipient selection: only actors
                # resolve_communication() actually authorizes to hear from
                # the originating actor (shared affiliation, or plain
                # same-society membership) get ticked — never an
                # indiscriminate whole-society blast. No originating actor
                # (e.g. a caller outside execute_actor_request) preserves
                # the old unconditional behavior.
                excluded_actor_ids: set[str] = set()
                if originating_actor_id is not None:
                    for candidate in sr.active_actors():
                        if candidate.actor_id == originating_actor_id:
                            continue
                        decision = self.resolve_communication(originating_actor_id, candidate.actor_id)
                        if not decision.allowed:
                            excluded_actor_ids.add(candidate.actor_id)

                await sr.tick(
                    broadcast_context={"question": broadcast_question},
                    exclude_actor_ids=frozenset(excluded_actor_ids) or None,
                )
                visited_society_ids.add(sr.society.society_id)
                reacted_actors = tuple(
                    a.actor_id for a in sr.active_actors() if a.actor_id not in excluded_actor_ids
                )
                actors_coordinated.update(reacted_actors)
                trace.append({
                    "depth": depth,
                    "events": matched_events,
                    "society_id": sr.society.society_id,
                    "society_name": sr.society.name,
                    "actors_ticked": list(reacted_actors),
                    "actors_excluded": sorted(excluded_actor_ids),
                })
        else:
            termination_reason = "max_depth"

        return (
            actors_coordinated,
            visited_society_ids - already_visited,
            termination_reason,
            all_domain_events,
        )

    async def _propagate_point_to_point(
        self, from_version: int, trace: list[dict[str, Any]],
        target_actor_id: str | None, originating_actor_id: str | None,
    ) -> tuple[set[str], set[str], str, set[str]]:
        """POINT_TO_POINT propagation scope: deliver directly to exactly
        one target actor — a single hop, no multi-society cascade. Gated
        by the same resolve_communication() authorization/trust/
        affiliation policy that governs broadcast recipient filtering
        above, so a direct send is never unauthorized."""
        if not target_actor_id:
            return set(), set(), "no_target", set()

        new_events = self.context_stream.replay(from_version=from_version + 1)
        domain_events = {
            e.payload.get("domain_event")
            for e in new_events
            if isinstance(e.payload, dict) and e.payload.get("domain_event")
        }
        if not domain_events:
            return set(), set(), "stable", set()

        if originating_actor_id is not None:
            decision = self.resolve_communication(originating_actor_id, target_actor_id)
            if not decision.allowed:
                return set(), set(), "not_authorized", domain_events

        target_societies = self._societies_for(target_actor_id)
        if not target_societies:
            return set(), set(), "target_unreachable", domain_events

        sr = target_societies[0]
        matched_events = sorted(domain_events)
        direct_question = (
            f"World event(s) occurred: {', '.join(matched_events)}. "
            f"If one of your available actions directly addresses this, "
            f"take it now rather than only investigating further."
        )
        if self._redis is not None:
            try:
                import json as _json
                self._redis.publish(
                    f"monkeybrain.actor.{target_actor_id}.direct",
                    _json.dumps({
                        "actor_id": target_actor_id, "from_actor_id": originating_actor_id,
                        "matched_events": matched_events, "question": direct_question,
                    }),
                )
            except Exception:
                logger.debug("_propagate_point_to_point: redis publish failed (non-fatal)", exc_info=True)

        await sr.tick(target_actor_id=target_actor_id, prompt_request={"question": direct_question})

        trace.append({
            "depth": 1,
            "events": matched_events,
            "society_id": sr.society.society_id,
            "society_name": sr.society.name,
            "actors_ticked": [target_actor_id],
        })
        return {target_actor_id}, set(), "stable", domain_events

    def _publish_coordination_metrics(
        self, societies_coordinated: int, actors_coordinated: int,
        events_published: int, events_consumed: int, propagation_steps: int,
        propagation_depth: int, propagation_latency_ms: float,
    ) -> None:
        """True Multi-Actor Coordination — Lemon metrics, published every
        request through the same _obs sink every other subsystem in this
        codebase already uses (kernel/compile/_obs.py); silent no-op
        wherever Lemon hasn't booted."""
        from src.monkey_brain.kernel.compile import _obs

        _obs.gauge("coordination.actors_coordinated", float(actors_coordinated))
        _obs.gauge("coordination.societies_coordinated", float(societies_coordinated))
        _obs.gauge("coordination.events_published", float(events_published))
        _obs.gauge("coordination.events_consumed", float(events_consumed))
        _obs.gauge("coordination.propagation_steps", float(propagation_steps))
        _obs.gauge("coordination.propagation_depth", float(propagation_depth))
        _obs.gauge("coordination.propagation_latency_ms", propagation_latency_ms)
        _obs.gauge(
            "coordination.avg_actors_per_event",
            actors_coordinated / events_published if events_published else 0.0,
        )
        _obs.gauge(
            "coordination.avg_events_per_request",
            float(events_published),
        )

    # TODO: each message that is exchanged between actors must first be intercepted and passed through this negotion engine to 
    # improve the messgae quality the negotiation trace is used by the main actor to manage the negotiation messages to be
    # sent to affiliates based in the current context so this message also needs full context from sitting face knowledge packs 
    # and from belief state 
    def _build_negotiation_trace(self, actor_id: str, actor_execution_result: Any) -> dict[str, Any] | None:
        """Game-Theoretic Reasoning: builds the explainable
        `{actor, goals, candidate_strategies, utility_evaluation"""
        plan = getattr(actor_execution_result, "plan", None)
        steps = getattr(plan, "steps", None) or ()
        actions = getattr(actor_execution_result, "actions", None) or []
        if not steps or not actions:
            return None

        candidate_strategies: list[str] = []
        utility_evaluation: list[dict[str, Any]] = []
        negotiation_outcome: str | None = None
        chosen_strategy: str | None = None
        reason: str | None = None
        negotiation_latency_ms = 0.0
        competitive = False
        cooperative = False
        agreement_recorded = False
        colleagues_involved: set[str] = set()

        for step, outcome in zip(steps, actions):
            if not isinstance(outcome, dict):
                continue
            action_name = getattr(step, "action", "")
            result = outcome.get("result")
            if not isinstance(result, dict):
                continue
            negotiation_latency_ms += float(outcome.get("latency_ms", 0.0) or 0.0)

            if action_name == "EvaluateStrategy" and outcome.get("success"):
                evaluations = result.get("evaluations") or []
                candidate_strategies = [e.get("name", "") for e in evaluations]
                utility_evaluation = evaluations
                chosen_strategy = result.get("best")
            elif action_name == "CompeteForResource" and outcome.get("success"):
                competitive = True
                negotiation_outcome = "won" if result.get("won") else "lost"
                reason = result.get("reason")
            elif action_name == "AskActor" and outcome.get("success"):
                cooperative = True
                colleagues_involved.add(result.get("target_actor", ""))
                negotiation_outcome = negotiation_outcome or "negotiated"
            elif action_name == "NegotiatePrice" and outcome.get("success"):
                cooperative = True
                if result.get("agreed"):
                    negotiation_outcome = "agreed"
                    chosen_strategy = f"price={result.get('price')}"
                else:
                    negotiation_outcome = "no_deal"
                    reason = result.get("reason")
            elif action_name == "NegotiateTerms" and outcome.get("success"):
                cooperative = True
                if result.get("agreed"):
                    negotiation_outcome = "agreed"
                    chosen_strategy = f"term={result.get('term')}"
                else:
                    negotiation_outcome = "no_deal"
                    reason = result.get("reason")
            elif action_name == "RecordAgreement" and outcome.get("success"):
                agreement_recorded = True
                negotiation_outcome = "agreement_recorded"
                reason = result.get("message")
            elif action_name == "RespondToInquiry" and outcome.get("success") and (competitive or cooperative):
                chosen_strategy = chosen_strategy or result.get("answer")
                reason = reason or result.get("answer")

        if not (candidate_strategies or negotiation_outcome):
            return None

        goal_text = str(getattr(plan, "goal", "") or "")
        return {
            "actor_id": actor_id,
            "goals": goal_text,
            "candidate_strategies": candidate_strategies,
            "utility_evaluation": utility_evaluation,
            "negotiation_outcome": negotiation_outcome,
            "chosen_strategy": chosen_strategy,
            "reason": reason,
            "negotiation_latency_ms": round(negotiation_latency_ms, 2),
            "is_competitive": competitive,
            "is_cooperative": cooperative,
            "agreement_recorded": agreement_recorded,
            "colleagues_involved": sorted(c for c in colleagues_involved if c),
        }

    def _publish_negotiation_metrics(self, negotiation_scope: dict[str, Any]) -> None:
        """Game-Theoretic Reasoning — Lemon metrics, published only for
        ticks that actually negotiated (see _build_negotiation_trace),
        through the same _obs sink every other subsystem already uses."""
        from src.monkey_brain.kernel.compile import _obs

        _obs.counter("negotiation.negotiations_started")
        if negotiation_scope.get("negotiation_outcome") or negotiation_scope.get("agreement_recorded"):
            _obs.counter("negotiation.negotiations_completed")
        if negotiation_scope.get("negotiation_outcome") == "lost":
            _obs.counter("negotiation.failed_negotiations")
        if negotiation_scope.get("agreement_recorded"):
            _obs.counter("negotiation.successful_agreements")
        if negotiation_scope.get("is_competitive"):
            _obs.counter("negotiation.competitive_decisions")
        if negotiation_scope.get("is_cooperative"):
            _obs.counter("negotiation.cooperative_decisions")

        evaluations = negotiation_scope.get("utility_evaluation") or []
        if evaluations:
            _obs.counter("negotiation.utility_evaluations")
            _obs.gauge("negotiation.strategies_considered", float(len(evaluations)))

        _obs.gauge("negotiation.average_negotiation_time_ms", negotiation_scope.get("negotiation_latency_ms", 0.0))
        _obs.gauge(
            "negotiation.avg_actors_per_negotiation",
            float(1 + len(negotiation_scope.get("colleagues_involved") or ())),
        )

    # TODO: this must update the trust nework based on sucessful execution history
    def _record_decision(
        self, actor_id: str, scope: dict[str, Any], execution_id: str = "",
        *, correlation_id: str = "", causation_id: str = "",
    ) -> None:
        from src.monkey_brain.kernel.timeline.entry import TimelineKind
        from src.monkey_brain.kernel.timeline.store import TimelineStore
        from src.monkey_brain.kernel.compile import _obs

        candidates = scope.get("utility_evaluation") or []
        chosen_strategy = scope.get("chosen_strategy") or ""
        chosen = next((c for c in candidates if c.get("name") == chosen_strategy), None)
        # execution_id already IS the correlation id for this call's only
        # existing caller (_finalize_actor_execution) — default to it so
        # that caller needs no changes and still gets a populated
        # correlation_id "for free".
        correlation_id = correlation_id or execution_id

        TimelineStore().record(
            TimelineKind.DECISION, actor_id=actor_id,
            selected_strategy=str(chosen_strategy),
            reason=str(scope.get("reason") or ""),
            utility=float(chosen.get("utility", 0.0)) if chosen else 0.0,
            evidence=tuple(scope.get("colleagues_involved") or ()),
            candidates=tuple(candidates),
            correlation_id=correlation_id,
            causation_id=causation_id,
            metadata={
                "decision_kind": "negotiation", "execution_id": execution_id,
                "negotiation_outcome": scope.get("negotiation_outcome"),
                "is_competitive": scope.get("is_competitive"),
                "is_cooperative": scope.get("is_cooperative"),
                "agreement_recorded": scope.get("agreement_recorded"),
                "candidate_strategies": scope.get("candidate_strategies"),
            },
        )
        _obs.counter("cognitive.decisions_made")
        _obs.gauge("cognitive.candidate_futures_evaluated", float(len(candidates)))


    # Full cycle runs only when planetary or world pretubtions take place 
    # TODO : add NATS stream for world level preturbtions that kick the planetary cyle off 
    
    # ── Full Planetary Cycle ─────────────────────────────────────────────

    async def cycle(self, timeout_seconds: float = 300.0) -> PlanetaryCycleResult | None:
        """Execute one complete planetary cycle by coordinating all active
        societies' execution — PlanetaryRuntime does not perform cognition
        itself.

        Iterates over all active societies and ticks each one. Each society
        coordinates its own actors' complete cognitive lifecycle.

        the society must not be associated with all geographic entities, 
        it must become a level in the geographic hierarchy, and the actor must be associated with multiple society,

        1. the societey is associated with at least one space what that means is of an actor is in a space it becomes a temporary member of the associated society to which the space is associated . 
        2. If the society is not associated with any spaces , raise an error.
        3. The actors can belong to multiple Society but only one space 
        4. ie we must have a one to many relationship between society and space, but a one to one relationship between actor and space.
        5. this ensures that the actor can be grouped by geographic location while the societes are indipendent of location.
        6. importtant to note that the societey provides membership and governance, while the space provides physical location and presence.
        7. we want to be able to track change in membership and location over time, so we need to be able to track the history of the actor's membership and location.
        
        Safeguards:
        - Tick lock prevents overlapping cycles
        - Configurable timeout (default 5 minutes)
        - Metrics for monitoring duration and actor count
        
        """
        # Prevent overlapping ticks WITHIN this process.
        if self._tick_lock.locked():
            logger.warning("Previous planetary tick still running, skipping this cycle")
            return None

        # Prevent overlapping ticks ACROSS processes (distributed lock).
        if not self._acquire_planetary_cycle_lock(timeout_seconds):
            logger.warning("Planetary cycle skipped — another replica holds the distributed lock this cycle")
            return None

        try:
            async with self._tick_lock:
                return await self._run_cycle_with_timeout(timeout_seconds)
        finally:
            # Release as soon as THIS cycle actually finishes, instead of
            # always holding the lock for its full worst-case TTL
            self._release_planetary_cycle_lock()

    async def _run_cycle_with_timeout(self, timeout_seconds: float) -> PlanetaryCycleResult:
        """Run cycle with timeout protection."""
        try:
            return await asyncio.wait_for(
                self._run_cycle(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error("Planetary cycle timed out after %ds", timeout_seconds)
            return PlanetaryCycleResult(
                cycle_number=self._cycle_count,
                actors_observed=0,
                beliefs_updated=0,
                interactions_routed=0,
                context_events=self.context_stream.event_count,
                duration_ms=timeout_seconds * 1000,
                error="timeout",
            )

    async def _run_cycle(self) -> PlanetaryCycleResult:
    
        """Internal cycle execution."""
        start = time.time()
        # Performance analysis instrumentation only (measurement, not a
        # behavior change) -- Runtime Performance Audit: perf_counter is
        # monotonic and higher-resolution than the wall-clock time.time()
        # already used above/below for duration_ms (kept unchanged for
        # backward compatibility with PlanetaryCycleResult's existing
        # consumers); this parallel measurement feeds CyclePerformanceReport.
        cycle_started = time.perf_counter()
        self._cycle_actor_timing_ms = {}
        self._cycle_count += 1

        actors_observed = 0
        beliefs_updated = 0
        interactions_routed = 0
        context_events_before = self.context_stream.event_count

        # Refresh world from Redis before each cycle (catches external mutations)
        try:
            self._load_world()
        except Exception:
            logger.debug("_run_cycle: suppressed exception", exc_info=True)  # Non-fatal — use existing world state

        recent_events = self.context_stream.replay(
            from_version=self._world_perturbation_context_version + 1,
        )
        self._world_perturbation_context_version = self.context_stream.version

        severity_total = 0.0
        severity_count = 0
        world_signal_count = 0
        for event in recent_events:
            if event.event_type not in (
                ContextEventType.WORLD_UPDATE,
                ContextEventType.OBSERVATION,
                ContextEventType.ACTION,
                ContextEventType.INTERACTION,
                ContextEventType.LEARNING,
            ):
                continue
            world_signal_count += 1
            payload = event.payload if isinstance(event.payload, dict) else {}
            for key in ("severity", "impact", "urgency", "magnitude"):
                value = payload.get(key)
                if isinstance(value, (int, float)):
                    severity_total += max(0.0, min(1.0, float(value)))
                    severity_count += 1

        severity = severity_total / severity_count if severity_count else 0.0
        perturbation_magnitude = min(0.35, 0.15 + severity * 0.20)
        perturbation_chance = min(
            0.60, 0.30 + min(world_signal_count, 10) * 0.02 + severity * 0.20,
        )
        _world_reconciliation_started = time.perf_counter()
        perturbations = self._world_model.perturb(
            magnitude=perturbation_magnitude,
            event_chance=perturbation_chance,
        )
        if perturbations:
            logger.info("World perturbed: %d changes", len(perturbations))

        # Apply movement perturbations to actors based on the world model's perturbations.
        # This simulates actors evacuating or moving due to environmental changes or other external factors.
        movement_perturbations = self._movement_perturbation.perturb(event_chance=0.05)
        if movement_perturbations:
            logger.info("Movement perturbation: %d actor(s) evacuated", len(movement_perturbations))
            perturbations.extend(movement_perturbations)
        world_reconciliation_ms = (time.perf_counter() - _world_reconciliation_started) * 1000

        # World Perturbation Queue: reconcile every operator/actor-reported
        # change queued since the last cycle (POST /planet/perturbations,
        # ReportWorldPerturbationCapability) into the Global World State --
        # additive to the simulated noise above, not a replacement for it
        # (see kernel/society/perturbation_queue.py). Also feeds Deja Vu:
        # any actor with a standing plan that references one of these
        # entity_ids gets its reasoning replayed under the updated state.
        _perturbation_queue_started = time.perf_counter()
        touched_entity_ids: set[str] = set()
        for queued in self._perturbation_queue.drain():
            self._world_model.record_event(WorldEvent(
                event_type=EventType.WORLD_UPDATE,
                entity_id=queued.entity_id,
                description=queued.description,
                attributes=dict(queued.attributes),
                source_actor_id=queued.source,
            ))
            if queued.entity_id:
                touched_entity_ids.add(queued.entity_id)
        perturbation_queue_ms = (time.perf_counter() - _perturbation_queue_started) * 1000

        deja_vu_ms = 0.0
        if touched_entity_ids:
            from src.monkey_brain.kernel.pipeline.planning.deja_vu import replay_affected_actors
            _deja_vu_started = time.perf_counter()
            try:
                replayed = await asyncio.to_thread(replay_affected_actors, self, touched_entity_ids)
                if replayed:
                    logger.info(
                        "Deja Vu: replayed %d actor(s) after world perturbation reconciliation",
                        len(replayed),
                    )
            except Exception:
                logger.warning("_run_cycle: Deja Vu replay pass failed (non-fatal)", exc_info=True)
            finally:
                deja_vu_ms = (time.perf_counter() - _deja_vu_started) * 1000

        # Tick every society reachable through the FULL physical geography
        # hierarchy — Planet -> Country -> State -> County -> City -> Street
        # -> Building -> Space — each socities 's own tick() handles its actors' cognition and coordination.
        # how ever the space makes sure that the actor can occupy only a single physical location at a time, while the actor can belong to multiple societies.
        # The societies are independent of the physical location, but the actor can only be in one space at a time.
        # This ensures that the actor can be grouped by geographic location while the societes are indipendent of location.
        #
        # Walks from the real Planet root ("Earth" — the same root every
        # real address resolves under via create_geo_from_address), not
        # the bootstrap _default_planet: that's None whenever no "Default
        # Planet" bootstrap scaffolding exists, which is now the normal
        # case on real data (see _find_default's docstring above).
        _scheduler_started = time.perf_counter()
        # _default_planet is only ever non-None while the synthetic
        # "Default Planet" bootstrap chain (created in __init__ when the
        # GeographicRegistry was empty at boot) hasn't yet been merged onto
        # a real "Earth" root via reconcile_default_geography() — walking
        # "Earth" unconditionally in that state finds a brand-new,
        # disconnected Planet (find_or_create creates one) with nothing
        # hosted under it, so no Society/Actor synthetic-bootstrapped via
        # register_actor()'s default_bootstrap_space_id fallback is ever
        # ticked. Once reconciled (production's normal path, see
        # reconcile_default_geography's docstring), _default_planet is
        # None and this always walks the real "Earth" root as before.
        planet_root = (
            self._default_planet
            if self._default_planet is not None
            else self._geo_registry.find_or_create(GeographicEntityType.PLANET, "Earth")
        )
        geo_result = await GeographicEntityRuntime(
            self._geo_registry,
            planet_root.entity_id,
            self._societies.get,
            presence=self._presence,
            actor_ticker=self._tick_present_actor,
            membership_reconciler=self._membership_governor.reconcile,
            temporary_membership_lookup=self._temporary_membership_lookup,
            effective_membership_lookup=self._effective_membership_lookup,
        ).tick()
        # Includes every actor's own tick time -- GeographicEntityRuntime
        # awaits each present actor's ticker serially (see
        # docs/adr/016-performance-gate9.md), not alongside this call.
        scheduler_ms = (time.perf_counter() - _scheduler_started) * 1000

        actors_observed += geo_result.actors_ticked_total
        beliefs_updated += geo_result.actors_ticked_total
        interactions_routed += geo_result.interactions_routed_total

        _cleanup_started = time.perf_counter()
        actor_reports: list[ActorPerformanceReport] = []

        # Publish Context Stream events for observed actors

        # NOTE: where are the published events consumed?  
        # They are not consumed by the planetary runtime itself, 
        # but they are consumed by the observability engine and the context stream.  
        # The context stream is a pub/sub system that allows other systems to subscribe to events and react to them.  
        # The observability engine consumes the events to build a trace of the society's activity over time.

        # GeographicEntityRuntime returns the deduplicated, presence-based
        # actor IDs observed during this cycle;
        for actor_id in geo_result.active_actors:
            home = self._home_society_runtime(actor_id)
            actor_state = home.get_actor(actor_id) if home is not None else None
            if actor_state is not None:

                # publish an observation event for each actor observed in this cycle, including their belief state if available
               
                # NOTE: we have no idea where the subscriber is, but we are publishing the event to the context stream so that any subscriber can consume it.  
                # The subscriber could be a logging system, a monitoring system, or any other system that wants to react to the actors observations.
                # this makes the planetary runtime observable and allows other systems to react to the actors observations.
                # 1. an observation is a snapshot of the actor's observation of its known world at a given point in time.  it is the actors snapshot of the global world and is immutable 
                # 2. a belief is the state of the actor's knowledge about the world, it is mutable and can change over time as the actor learns new information so an actors observations update its learnings and beliefs 
                # a context event must update the local belief system based on the actor policies and prefrences This allows other systems to react to the actor's observations and make decisions based on them.

                self.context_stream.publish(ContextEvent(
                    event_type=ContextEventType.OBSERVATION,
                    actor_id=actor_state.actor_id,
                    description=f"Actor {actor_state.actor_id} observed and coordinated",
                    payload={
                        "actor_id": actor_state.actor_id,
                        "name": actor_state.profile.identity.name,
                        "cycle_count": actor_state.cycle_count,
                        "status": actor_state.status.value,
                    },
                ))
                if actor_state.belief_state is not None:
                    beliefs = {
                        b.subject: 
                            {
                                "confidence": b.confidence, 
                                "predicate": b.best_hypothesis.predicate if b.best_hypothesis else ""
                             }
                               for b in actor_state.belief_state.beliefs}
                    
                    # publish a belief update event for each actor observed in this cycle, including their belief state if available
                    # this must be done after the observation event so that the belief update is based on the latest observation
                    # each actor must take its own observations and update its own beliefs based on its own policies and preferences, 
                    # this is the core of the planetary runtime's cognitive engine so actors subscribe to the context stream and update their beliefs.

                    self.context_stream.publish(ContextEvent(
                        event_type=ContextEventType.BELIEF_UPDATE,
                        actor_id=actor_state.actor_id,
                        description=f"Beliefs updated: {len(actor_state.belief_state.beliefs)} beliefs",
                        payload={
                            "actor_id": actor_state.actor_id,
                            "belief_count": len(actor_state.belief_state.beliefs),
                            "beliefs": beliefs,
                            "uncertainty_level": actor_state.belief_state.uncertainty_level,
                        },
                    ))

                # Performance analysis instrumentation only (measurement,
                # not a behavior change): last_tick_result is set by
                # SocietyRuntime._coordinate_actor() during this cycle's
                # scheduler pass above (kernel/society/runtime.py) --
                # None if the tick failed before the cognitive engine ran
                # (e.g. belief fusion raised in tick_one_actor).
                last_result = actor_state.last_tick_result
                actor_reports.append(ActorPerformanceReport.from_stage_timings(
                    actor_id=actor_state.actor_id,
                    actor_name=actor_state.profile.identity.name,
                    total_ms=self._cycle_actor_timing_ms.get(actor_id, 0.0),
                    stage_timings_ms=getattr(last_result, "stage_timings_ms", None) or {},
                    belief_updated=bool(getattr(last_result, "belief_updated", False)),
                    ticked=last_result is not None,
                ))

        cleanup_ms = (time.perf_counter() - _cleanup_started) * 1000

        duration_ms = (time.time() - start) * 1000
        self._last_tick_duration_ms = duration_ms
        self._last_tick_timestamp = time.time()
        self._publish_lemon_metrics(
            geo_result, perturbations,
            actors_observed,
            interactions_routed,
            duration_ms
        )

        context_events_published = self.context_stream.event_count - context_events_before
        logger.info(
            "Planetary cycle %d completed: %d societies, %d actors, %d interactions, %d context events, %.1fms",
            self._cycle_count,
            geo_result.societies_ticked_total,
            actors_observed,
            interactions_routed,
            context_events_published,
            duration_ms,
        )

        # Performance analysis instrumentation only (measurement, not a
        # behavior change) -- Runtime Performance Audit.
        cycle_report = CyclePerformanceReport(
            cycle_number=self._cycle_count,
            total_ms=round((time.perf_counter() - cycle_started) * 1000, 3),
            scheduler_ms=round(scheduler_ms, 3),
            perturbation_queue_ms=round(perturbation_queue_ms, 3),
            world_reconciliation_ms=round(world_reconciliation_ms, 3),
            deja_vu_ms=round(deja_vu_ms, 3),
            cleanup_ms=round(cleanup_ms, 3),
            actors=tuple(actor_reports),
        )
        self._last_cycle_report = cycle_report
        logger.info("Runtime Performance Audit — cycle %d:\n%s", self._cycle_count, cycle_report.format_summary())

        return PlanetaryCycleResult(
            cycle_number=self._cycle_count,
            actors_observed=actors_observed,
            beliefs_updated=beliefs_updated,
            interactions_routed=interactions_routed,
            context_events=self.context_stream.event_count,
            duration_ms=duration_ms,
        )



    # ── Auto-Tick Scheduler ──────────────────────────────────────────────
    # planetary runtime can be configured to automatically tick every N seconds, with a default of 5 minutes (300 seconds). 
    # This is useful for running the planetary runtime in a background task or service.

    def _acquire_planetary_cycle_lock(self, timeout_seconds: float = 300.0) -> bool:
        """Try to become the replica that runs this planetary cycle —
        covers BOTH the auto-tick loop and manual POST /planet/tick calls,
        since both go through cycle().

        self._redis is None (never configured — see _init_persistence)
        is a static, boot-time-known degraded mode: with no Redis at all,
        there is no distributed coordination happening for ANY replica, so
        proceeding as sole owner is correct, not "failing open" — it isn't
        a real replica-vs-replica race case. A Redis call that RAISES
        (Redis WAS reachable and is now erroring/unreachable mid-request)
        is the genuinely dangerous case: some other replica may currently
        hold this lock and be mid-cycle, and we simply can't tell. That
        must fail CLOSED (refuse to proceed) — the old behavior treated
        both cases identically and returned True either way, meaning a
        transient Redis blip made every replica independently believe it
        was the sole owner and run concurrently, exactly when the
        cross-replica guarantee matters most.
        """
        if self._redis is None:
            return True
        token = f"{os.getpid()}:{uuid4().hex}"
        try:
            acquired = bool(self._redis.set(
                _PLANETARY_CYCLE_LOCK_KEY, token,
                nx=True, ex=max(1, int(timeout_seconds) + 30),
            ))
        except Exception as exc:
            logger.error(
                "Planetary cycle lock check failed (%s) — refusing to proceed: an "
                "unreachable Redis must not be treated as proof no other replica "
                "currently holds the lock", exc,
            )
            return False
        if acquired:
            self._cycle_lock_token = token
        return acquired

    def _release_planetary_cycle_lock(self) -> None:
        """Release the distributed lock as soon as this cycle actually
        finishes, instead of always holding it for the full TTL — but only
        if it's still OUR lock. An unconditional DELETE here would let a
        slow/orphaned holder (e.g. one whose cancellation didn't actually
        stop its in-flight work — see _run_cycle_with_timeout) wipe out a
        DIFFERENT replica's lock that legitimately acquired the key after
        this one's TTL had already expired. See
        _RELEASE_LOCK_IF_OWNER_SCRIPT.
        """
        if self._redis is None:
            return
        token = getattr(self, "_cycle_lock_token", None)
        if token is None:
            return
        try:
            self._redis.eval(_RELEASE_LOCK_IF_OWNER_SCRIPT, 1, _PLANETARY_CYCLE_LOCK_KEY, token)
        except Exception as exc:
            logger.warning("Planetary cycle lock release failed (%s) — TTL will expire it eventually", exc)
        finally:
            self._cycle_lock_token = None

    #################################################################################################
    #                                       Actor Ownership Lease                                  #
    #################################################################################################
    # Deployment Architecture, Section 7 (ownership/lease gap): the planetary
    # cycle lock above answers "which node runs the next whole-planet tick."
    # This answers a narrower, per-actor question: two nodes can each have
    # the SAME actor_id loaded (both reconciled it from the shared Redis
    # actor registry — see _load_actors()/reconcile_actors_from_redis()),
    # and nothing previously stopped both from ticking it concurrently, a
    # split-brain risk on one identity's belief. Same SET NX EX + Lua
    # compare-and-delete pattern as the cycle lock, generalized to a
    # per-actor key so many actors can be leased independently (and
    # concurrently, across nodes, for DIFFERENT actor_ids) rather than one
    # global lock serializing every actor behind a single key.

    def acquire_actor_lease(self, actor_id: str,
                            ttl_seconds: float = _ACTOR_LEASE_DEFAULT_TTL_SECONDS) -> str | None:
        """Try to become the node that runs this actor's next cognitive
        cycle. Returns a token to pass to release_actor_lease() on success,
        or None if another node currently holds the lease (skip this tick)
        or the lease check itself failed.

        self._redis is None (never configured) is a static, boot-time-known
        degraded mode: with no Redis at all there is no second process
        sharing this actor's state to race against, so proceeding without a
        real lease is correct, not "failing open" — same reasoning as
        _acquire_planetary_cycle_lock. A Redis call that RAISES (Redis WAS
        reachable and is erroring now) is the dangerous case: another node
        may genuinely hold this actor's lease and we can't tell — that must
        fail CLOSED (return None, skip the tick) rather than assume it's
        safe to proceed."""
        token = f"{self._node_id}:{os.getpid()}:{uuid4().hex}"
        if self._redis is None:
            return token
        try:
            acquired = bool(self._redis.set(
                f"{_ACTOR_LEASE_KEY_PREFIX}{actor_id}", token,
                nx=True, ex=max(1, int(ttl_seconds)),
            ))
            if acquired:
                fence = int(self._redis.incr(f"{_ACTOR_FENCE_KEY_PREFIX}{actor_id}"))
                self._actor_lease_fences[actor_id] = fence
        except Exception as exc:
            if self._lease_fail_open_single_node():
                # Explicit operator opt-in (ACTOR_LEASE_FAIL_OPEN_SINGLE_NODE
                # — Horizontal Scheduler Scaling, Section 22: "existing
                # Actors should not immediately cease cognition merely
                # because the Registry is temporarily unavailable"). This
                # is a deliberate, named trade-off, not a default: it
                # reopens the exact split-brain race the fail-closed
                # default above exists to close, and is only safe when the
                # operator can guarantee no OTHER node could possibly also
                # be ticking this same actor_id right now (e.g. a genuine
                # one-actor-per-process edge deployment) -- default OFF,
                # see docs/CLOUD_EDGE_ACTOR_ARCHITECTURE.md and
                # docs/HORIZONTAL_SCHEDULER_SCALING.md for exactly when
                # this is and isn't safe to enable.
                logger.warning(
                    "Actor lease check failed for %r (%s) — "
                    "ACTOR_LEASE_FAIL_OPEN_SINGLE_NODE is set, proceeding "
                    "WITHOUT a lease (operator-accepted split-brain risk)",
                    actor_id, exc,
                )
                return token
            logger.warning(
                "Actor lease check failed for %r (%s) — refusing to tick: an "
                "unreachable Redis must not be treated as proof no other node "
                "currently owns this actor", actor_id, exc,
            )
            return None
        return token if acquired else None

    def get_actor_lease_fence(self, actor_id: str) -> int | None:
        """Monotonic fence acquired with the current actor lease (if any)."""
        return self._actor_lease_fences.get(actor_id)

    @staticmethod
    def _lease_fail_open_single_node() -> bool:
        return os.getenv("ACTOR_LEASE_FAIL_OPEN_SINGLE_NODE", "false").lower() not in ("false", "0", "no")

    def release_actor_lease(self, actor_id: str, token: str | None) -> None:
        """Release actor_id's lease as soon as its tick actually finishes,
        instead of always holding it for the full TTL. Only releases if
        `token` still matches what's stored (the same compare-and-delete
        safety _RELEASE_LOCK_IF_OWNER_SCRIPT already gives the cycle lock —
        an unconditional DELETE could otherwise wipe out a DIFFERENT node's
        lease that legitimately acquired the key after this one's TTL had
        already expired). No-op, not an error, when token is None/empty
        (the "no Redis, no real lease was ever taken" case from
        acquire_actor_lease) or Redis is unavailable."""
        if self._redis is None or not token:
            return
        try:
            self._redis.eval(_RELEASE_LOCK_IF_OWNER_SCRIPT, 1, f"{_ACTOR_LEASE_KEY_PREFIX}{actor_id}", token)
        except Exception as exc:
            logger.debug("Actor lease release failed for %r (non-fatal, TTL will expire it): %s", actor_id, exc)
        finally:
            self._actor_lease_fences.pop(actor_id, None)

    #################################################################################################
    #                                   Actor Messaging (durable inbox)                            #
    #################################################################################################
    # Deployment Architecture, Section 8: SocietyRuntime._message_queue was
    # a plain in-process list — send_message/broadcast_message only ever
    # worked between actors registered in the SAME process's _actors dict,
    # by construction. These three methods give each actor_id a durable,
    # per-actor Redis inbox any process can push into and only the
    # process currently ticking that actor drains, closing the gap
    # without changing send_message's fire-and-forget, trust-weighted-
    # belief-injection-on-next-tick semantics at all — only WHERE the
    # queue lives changes. SocietyRuntime falls back to its own in-process
    # list when these return False/[]/[] (Redis unavailable, or no
    # PlanetaryRuntime attached at all — the standalone/unit-test case),
    # exactly the same degrade-gracefully contract every other store here
    # already has.

    def push_actor_message(self, actor_id: str, message: dict[str, Any]) -> bool:
        """Queue one message into actor_id's durable inbox. Returns True
        if durably queued; False (never raises) means the caller must
        fall back to a process-local queue."""
        if self._redis is None:
            return False
        try:
            key = f"{_MESSAGE_INBOX_KEY_PREFIX}{actor_id}"
            pipe = self._redis.pipeline()
            pipe.rpush(key, json.dumps(message))
            pipe.expire(key, _MESSAGE_INBOX_TTL_SECONDS)
            pipe.execute()
            return True
        except Exception as exc:
            logger.warning("push_actor_message(%r) failed: %s", actor_id, exc)
            return False

    def drain_actor_inbox(self, actor_id: str) -> list[dict[str, Any]]:
        """Atomically read and clear actor_id's durable inbox — the
        delivery path (SocietyRuntime._deliver_messages), called once per
        resident actor at the start of every real tick. Never raises;
        returns [] on any failure, same as an empty inbox."""
        if self._redis is None:
            return []
        try:
            raw_list = self._redis.eval(_DRAIN_INBOX_SCRIPT, 1, f"{_MESSAGE_INBOX_KEY_PREFIX}{actor_id}")
            return [json.loads(raw) for raw in (raw_list or [])]
        except Exception as exc:
            logger.warning("drain_actor_inbox(%r) failed: %s", actor_id, exc)
            return []

    def peek_actor_inbox(self, actor_id: str) -> list[dict[str, Any]]:
        """Non-destructive read of actor_id's durable inbox — for
        introspection (SocietyRuntime.get_messages_for), never delivery.
        Unlike drain_actor_inbox, does not clear the inbox."""
        if self._redis is None:
            return []
        try:
            raw_list = self._redis.lrange(f"{_MESSAGE_INBOX_KEY_PREFIX}{actor_id}", 0, -1)
            return [json.loads(raw) for raw in (raw_list or [])]
        except Exception as exc:
            logger.debug("peek_actor_inbox(%r) failed: %s", actor_id, exc)
            return []

    #################################################################################################
    #                              Actor Lifecycle Controller — Reconciliation Loop                #
    #################################################################################################
    # Same shape as start_auto_tick/_auto_tick_loop directly below — a
    # second, independent background loop, not folded into the tick loop,
    # because they answer different questions on different cadences:
    # auto-tick runs actor COGNITION; this loop only reconciles actor
    # LIFECYCLE (desired vs. observed state) and never touches planning,
    # belief, or capability dispatch.

    def start_actor_lifecycle_reconciliation(self, interval_seconds: float = 300.0,
                                             queue_interval_seconds: float = 2.0,
                                             queue_batch_size: int = 50,
                                             queue_concurrency: int = 10,
                                             scope_actor_id: str | None = None) -> None:
        """Start the Actor Lifecycle Controller's background reconciliation.

        scope_actor_id (Gap Remediation audit fix): a single-actor Actor
        Runtime pod (actor_runtime.py — the canonical per-actor Kubernetes
        deployment unit, see deploy/k8s/actor-deployment.yaml) only ever
        needs its OWN actor reconciled, but the backstop sweep below
        previously always ran reconcile_all() — every pod in an N-actor
        cluster doing O(N) reconcile() calls every interval, O(N^2) total
        Redis round-trips cluster-wide for no benefit (each pod's sweep
        redundantly re-decides every OTHER actor, which its own owning
        pod already covers). When set, the backstop sweep reconciles only
        this actor_id instead of the full registry. None (default)
        preserves the exact prior full-sweep behavior for every existing
        caller (multi-actor Society/control-plane processes, tests) —
        only actor_runtime.py's single-actor start() path now passes its
        own config.actor_id here.

        Two independent loops (Horizontal Scheduler Scaling, Section 26/27),
        both idempotent to start twice (same contract as start_auto_tick):

        - The FAST, event-driven queue-drain loop (queue_interval_seconds,
          default 2s): pops up to queue_batch_size actor_ids the moment
          something actually changed (_enqueue_reconciliation, called from
          register_actor/set_actor_desired_state/set_actor_desired_node)
          and reconciles only those, with at most queue_concurrency
          reconcile() calls in flight at once (Section 25: backpressure —
          a burst of thousands of registrations fills the queue instantly,
          but this loop drains it at a bounded rate rather than firing
          thousands of concurrent reconcile() calls, each its own Redis
          round trips, all at once).
        - The SLOW, full-table backstop sweep (interval_seconds, default
          300s — 5x its pre-Section-26 default of 60s, to make the "this is
          a backstop, not the primary mechanism" intent explicit): the
          existing reconcile_all() sweep, kept only for what the queue
          can't precisely target (Section 27 explicitly allows this) — a
          node's capacity freeing up doesn't identify which specific
          unschedulable actors might now fit, and a possible lost/dropped
          enqueue (Redis blip during the write) still self-heals within
          one backstop interval.

        interval_seconds' default changed from 60.0 (pre-Section-26): a
        caller that passes it explicitly is unaffected; a caller relying
        on the old default now gets the intended "rare backstop" cadence
        instead, matching Section 27's guidance.
        """
        if self._lifecycle_reconciliation_task is not None and not self._lifecycle_reconciliation_task.done():
            logger.warning("Actor lifecycle reconciliation already running")
            return
        self._lifecycle_reconciliation_interval = interval_seconds
        self._reconcile_queue_interval = queue_interval_seconds
        self._reconcile_queue_batch_size = queue_batch_size
        self._reconcile_queue_semaphore = asyncio.Semaphore(max(1, queue_concurrency))
        self._reconciliation_scope_actor_id = scope_actor_id
        # Self-register (and, every loop iteration below, heartbeat) this
        # process as an execution node -- this is what lets the
        # Scheduler run in "managed" mode at all (Actor Scheduler spec,
        # Section 6). An operator that never wants scheduling involved
        # can set SCHEDULER_SELF_REGISTER=false; ActorScheduler.schedule()
        # already degrades to unconstrained placement when zero nodes are
        # registered anywhere, so this is opt-out, not required for
        # correctness.
        #
        # Edge Deployment Validation finding (P1, confirmed live): this
        # call previously always passed zero args, meaning it silently
        # OVERWROTE any node_class/capacity a caller had already
        # correctly registered moments earlier (e.g. actor_runtime.py's
        # own start() calling register_self_as_node(node_class=EDGE,
        # capacity=self.config.node_capacity, ...) BEFORE calling this
        # method) with the generic SCHEDULER_NODE_CLASS/_CAPACITY env-var
        # defaults (cloud/1000) -- confirmed live: an edge actor's
        # correct node_class=edge registration flipped back to "cloud"
        # on this exact call, immediately failing every subsequent
        # "required_node_class=edge" placement with a genuinely
        # confusing "no healthy node satisfies" error. Invisible for the
        # common cloud/default-capacity case (the overwrite happens to
        # match what was already there), but a real, silent placement-
        # constraint violation for any actor using a non-default
        # node_class or capacity. Fix: preserve an already-existing
        # registration for this exact node_id instead of blindly
        # re-deriving from env-var defaults; only a genuinely new node_id
        # (no prior registration at all) falls back to those.
        if os.getenv("SCHEDULER_SELF_REGISTER", "true").lower() not in ("false", "0", "no"):
            try:
                existing = self.get_node(self._node_id)
                if existing is not None:
                    self.register_self_as_node(
                        node_class=existing.node_class, capacity=existing.capacity,
                        capabilities=existing.capabilities, region=existing.region,
                    )
                else:
                    self.register_self_as_node()
            except Exception as exc:
                logger.warning("register_self_as_node() at reconciliation startup failed: %s", exc)
        self._lifecycle_reconciliation_task = asyncio.create_task(self._actor_lifecycle_reconciliation_loop())
        self._reconcile_queue_task = asyncio.create_task(self._reconcile_queue_loop())
        logger.info(
            "Actor lifecycle reconciliation started: backstop sweep every %ds, "
            "event-driven queue drain every %.1fs (batch=%d, concurrency=%d)",
            interval_seconds, queue_interval_seconds, queue_batch_size, queue_concurrency,
        )

    async def stop_actor_lifecycle_reconciliation(self) -> None:
        for attr in ("_lifecycle_reconciliation_task", "_reconcile_queue_task"):
            task = getattr(self, attr, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                setattr(self, attr, None)
        logger.info("Actor lifecycle reconciliation stopped")

    async def _reconcile_one_bounded(self, actor_id: str) -> None:
        """One queue-drained actor_id's reconcile() call, gated by the
        shared semaphore (Section 25: backpressure) -- never more than
        queue_concurrency reconcile() calls in flight from this loop at
        once, regardless of how many actor_ids were just drained."""
        async with self._reconcile_queue_semaphore:
            try:
                result = await asyncio.to_thread(self.lifecycle.reconcile, actor_id)
                if result.action not in ("none", "skipped_lease_held", "skipped_unknown_actor"):
                    logger.info("Actor lifecycle reconciliation (event-driven): %s -> %s", actor_id, result.action)
            except Exception as exc:
                logger.error("Event-driven reconcile(%r) raised: %s", actor_id, exc, exc_info=True)

    async def _reconcile_queue_loop(self) -> None:
        """The fast, event-driven path (Section 26). Drains in batches
        and dispatches each item through the bounded semaphore
        concurrently within a batch — items in one batch don't serialize
        behind each other, but the loop never has more than
        queue_concurrency truly in flight system-wide."""
        while True:
            try:
                await asyncio.sleep(self._reconcile_queue_interval)
                batch = await asyncio.to_thread(self._drain_reconcile_queue_batch, self._reconcile_queue_batch_size)
                if batch:
                    await asyncio.gather(*(self._reconcile_one_bounded(aid) for aid in batch))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Reconcile queue loop error: %s", exc, exc_info=True)

    async def _actor_lifecycle_reconciliation_loop(self) -> None:
        """The slow, full-table backstop sweep (Section 27) — correctness
        net, not the primary scaling mechanism; see start_actor_lifecycle_
        reconciliation's docstring."""
        while True:
            try:
                await asyncio.sleep(self._lifecycle_reconciliation_interval)
                try:
                    current_actor_count = sum(len(sr.all_actors()) for sr in self._societies.values())
                    self.heartbeat_node(self._node_id, current_actor_count=current_actor_count)
                except Exception as exc:
                    logger.debug("node heartbeat failed (non-fatal): %s", exc)
                scope_actor_id = self._reconciliation_scope_actor_id
                if scope_actor_id:
                    results = [await asyncio.to_thread(self.lifecycle.reconcile, scope_actor_id)]
                else:
                    results = await asyncio.to_thread(self.lifecycle.reconcile_all)
                acted = [r for r in results if r.action not in ("none", "skipped_lease_held", "skipped_unknown_actor")]
                if acted:
                    logger.info(
                        "Actor lifecycle reconciliation (backstop sweep): %d actor(s) acted on (%s)",
                        len(acted), ", ".join(f"{r.actor_id}:{r.action}" for r in acted),
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Actor lifecycle reconciliation loop error: %s", exc, exc_info=True)

    def start_auto_tick(self, interval_seconds: float = 300.0) -> None:
        """Start the automatic tick scheduler."""
        if self._auto_tick_task is not None and not self._auto_tick_task.done():
            logger.warning("Auto-tick scheduler already running")
            return
        self._auto_tick_interval = interval_seconds
        self._auto_tick_task = asyncio.create_task(self._auto_tick_loop())
        logger.info("Auto-tick scheduler started: every %ds", interval_seconds)

    async def stop_auto_tick(self) -> None:
        """Stop the automatic tick scheduler."""
        if self._auto_tick_task is not None and not self._auto_tick_task.done():
            self._auto_tick_task.cancel()
            try:
                await self._auto_tick_task
            except asyncio.CancelledError:
                pass
            self._auto_tick_task = None
            logger.info("Auto-tick scheduler stopped")

    async def _auto_tick_loop(self) -> None:
        """Background loop that ticks every interval_seconds.
        """
        while True:
            try:
                await asyncio.sleep(self._auto_tick_interval)
                logger.info("Auto-tick triggered (every %ds)", self._auto_tick_interval)
                # cycle() itself now acquires the distributed lock (covers
                # this loop AND manual /planet/tick calls) — no separate
                # check needed here anymore.
                result = await self.cycle()
                if result is not None:
                    logger.info(
                        "Auto-tick cycle %d completed: %d actors, %.1fms",
                        result.cycle_number, result.actors_observed, result.duration_ms,
                    )
                    self._save_context()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Auto-tick error: %s", e)

    # ── Observability ────────────────────────────────────────────────────

    def trace(self) -> SocietyTrace:
        events = self.context_stream.events(limit=10000)
        return self._observability.build_society_trace(
            events,
            total_ticks=self._cycle_count,
            active_actors=len(self._society_runtime.active_actors()),
            total_policies=len(self.governance.policies()),
        )

    # ── Shutdown ──────────────────────────────────────────────────────────

    async def shutdown(self, app: Any) -> None:
        """Shutdown the planetary runtime and its societies."""
        await self.stop_auto_tick()
        await self.stop_actor_lifecycle_reconciliation()
        try:
            # Clean shutdown deregisters this node immediately rather than
            # waiting out _NODE_STALE_SECONDS of heartbeat silence -- the
            # Scheduler stops offering it for new placements right away.
            self.deregister_node(self._node_id)
        except Exception as exc:
            logger.debug("deregister_node(%r) at shutdown failed (non-fatal): %s", self._node_id, exc)
        for task in list(self._background_propagation_tasks):
            if not task.done():
                task.cancel()
        for task in list(self._background_propagation_tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._background_propagation_tasks.clear()
        for task in list(self._inbox_subscription_tasks):
            if not task.done():
                task.cancel()
        for task in list(self._inbox_subscription_tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._inbox_subscription_tasks.clear()
        # _init_persistence's Redis client was never closed here — same
        # leaked-connection class of bug as SemanticGraph's Neo4j driver
        # (RuntimeBootstrap.shutdown) — harmless at low volume but real
        # under this session's sequential test-boot-cycle volume.
        if getattr(self, "_redis", None) is not None:
            try:
                self._redis.close()
            except Exception as exc:
                logger.warning("Redis client close failed: %s", exc)
        if getattr(app.state, "planetary_runtime", None) is self:
            app.state.planetary_runtime = None
        from src.monkey_brain.api.routes.payments import get_default_planetary_runtime, set_default_planetary_runtime
        if get_default_planetary_runtime() is self:
            set_default_planetary_runtime(None)
        logger.info("PlanetaryRuntime shutdown complete")

    # ── Serialization ────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "society_id": self.society.society_id,
            "society_name": self.society.name,
            "world_version": self._world.version,
            "actor_count": len(self._society_runtime.all_actors()),
            "active_actors": len(self._society_runtime.active_actors()),
            "cycle_count": self._cycle_count,
            "context_events": self.context_stream.event_count,
            "interactions": len(self._society_runtime.all_interactions()),
            "reputations": len(self._society_runtime.collective_learning.all_reputations()),
            "policies": len(self._society_runtime._society.policies),
            "federations": len(self._federation_manager.federations_for_society(self.society.society_id)),
            "society_registry_count": len(self._societies),
        }
