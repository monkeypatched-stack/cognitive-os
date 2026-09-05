"""Protocol interfaces for pipeline contracts.

Defines the abstract interfaces that RuntimeContext and CompiledRequest
fields must satisfy. These are the type-safe alternatives to `Any`.

Dependency direction:
    contracts  ◀──  runtime
    protocols  ◀──  contracts
    protocols  ◀──  runtime

Protocols never import runtime implementations.
Runtime implementations satisfy protocols without importing them.
"""
from __future__ import annotations

from typing import Protocol, Any, runtime_checkable


@runtime_checkable
class WorldView(Protocol):
    """Read-only view of the world that actors reason over."""

    def states(self) -> list[str]:
        """Return all world states."""
        ...

    def domains(self) -> list[str]:
        """Return all world domains."""
        ...

    def nnz(self) -> int:
        """Return number of non-zero transitions."""
        ...

    def successors(self, state: str) -> list:
        """Return successor states for a given state."""
        ...

    def domain_of(self, state: str) -> str:
        """Return the domain of a state."""
        ...

    def feature(self, src: str, dst: str, feature_type: Any) -> float:
        """Return a feature value for a transition."""
        ...


@runtime_checkable
class ActorHandle(Protocol):
    """Handle to an actor registry or actor instance."""

    def get(self, actor_id: str) -> Any:
        """Retrieve an actor by ID."""
        ...


@runtime_checkable
class CapabilityBus(Protocol):
    """Bus for discovering and invoking capabilities."""

    def discover(self, name: str) -> Any:
        """Discover a capability by name."""
        ...


@runtime_checkable
class KnowledgeStore(Protocol):
    """Store for querying knowledge."""

    def query(self, question: str) -> Any:
        """Query the knowledge store."""
        ...


@runtime_checkable
class ContextStreamProtocol(Protocol):
    """Pub/sub stream for world updates."""

    def publish(self, event: Any) -> None:
        """Publish an event."""
        ...

    def subscribe(self, callback: Any) -> None:
        """Subscribe to events."""
        ...


@runtime_checkable
class ExecutionEngine(Protocol):
    """Engine for executing workloads."""

    async def execute(self, context: Any, **kwargs: Any) -> Any:
        """Execute a workload."""
        ...


@runtime_checkable
class LearningEngine(Protocol):
    """Engine for updating policies from outcomes."""

    def apply_learning(self, delta: Any, graph: Any) -> dict:
        """Apply learning delta and return metrics."""
        ...


@runtime_checkable
class MetricsCollector(Protocol):
    """Collector for metrics and telemetry."""

    def counter(self, name: str, **tags: Any) -> None:
        """Increment a counter."""
        ...

    def histogram(self, name: str, value: float, **tags: Any) -> None:
        """Record a histogram value."""
        ...

    def gauge(self, name: str, value: float, **tags: Any) -> None:
        """Record a gauge value."""
        ...


@runtime_checkable
class BeliefRuntimeProtocol(Protocol):
    """Protocol for belief formation and fusion."""

    def accept_proposal(self, proposal: Any) -> None:
        """Accept a belief proposal."""
        ...

    def fuse_observations(self) -> dict:
        """Fuse observations into belief."""
        ...


@runtime_checkable
class TrustNetworkProtocol(Protocol):
    """Protocol for trust scoring and weighting."""

    def trust(self, source: str, target: str) -> float:
        """Get trust score between source and target."""
        ...


@runtime_checkable
class AuditServiceProtocol(Protocol):
    """Protocol for audit logging."""

    def record(self, **kwargs: Any) -> None:
        """Record an audit entry."""
        ...


@runtime_checkable
class EventBusProtocol(Protocol):
    """Protocol for event publishing."""

    async def publish(self, event_type: str, payload: dict) -> None:
        """Publish an event."""
        ...


@runtime_checkable
class ActorStateStoreProtocol(Protocol):
    """Architecture Boundary Hardening (Section 1): the interface
    kernel/society/integration.py::PlanetaryRuntime.checkpoint_actor_belief/
    restore_actor_belief already depend on via _get_actor_state_store() --
    made explicit here rather than left as an implicit convention. Matches
    persistence/actor_state_store.py::ActorStateStore's real, existing
    method surface exactly (no renaming) so that class satisfies this
    Protocol with zero changes. kernel/edge/actor_state_store.py::
    EdgeActorStateStore is the second, edge-local implementation --
    portable, but never authoritative in place of whichever store the
    control plane designates (see that module's own docstring)."""

    def save(self, actor_state: Any) -> None:
        """Persist a persistence.actor_state_store.PersistedActorState."""
        ...

    def load(self, actor_id: str, tenant_id: str) -> Any:
        """Returns a PersistedActorState, or None if not found."""
        ...

    def delete(self, actor_id: str, tenant_id: str) -> bool:
        ...

    def list_actors(self, tenant_id: str, active_only: bool = True) -> list[str]:
        ...


@runtime_checkable
class WorldStateStore(Protocol):
    """Architecture Boundary Hardening (Section 2): the interface the
    cognitive/capability layer already exclusively uses to read/mutate
    world entities and relationships -- kernel/knowledge_graph.py::
    KnowledgeGraph already satisfies this with zero changes (confirmed:
    no capability or cognitive-loop module imports a neo4j driver
    directly; see tests/architecture/test_dependency_direction.py).
    kernel/knowledge_graph_neo4j.py::Neo4jBackedKnowledgeGraph is a second
    real implementation, used today for a narrower, distinct concern
    (durable household/organization-role and delegation facts queried by
    kernel/domains/domain_security.py and delegation-related API routes)
    -- NOT a drop-in replacement for the default, Redis-persisted,
    in-process KnowledgeGraph every grocery/commerce capability actually
    reasons over; both are real, both satisfy this Protocol, they serve
    different scopes by design, not by oversight."""

    def get_entity(self, entity_id: str) -> Any: ...

    def add_entity(self, entity: Any) -> None: ...

    def get_relationship(self, relationship_id: str) -> Any: ...


@runtime_checkable
class ExecutionAdapterProtocol(Protocol):
    """Architecture Boundary Hardening (Section 4): the shape every
    execution substrate behind kernel/pipeline/action_executor.py::
    ActionExecutor's ensure_governed boundary already satisfies --
    kernel/edge/ros_integration.py::RosExecutionAdapter (Fake and Rclpy)
    already matches this exactly; CapabilityBus.discover(name).handle(...)
    (the API/domain-capability path) satisfies the same shape structurally
    even though nothing forces it through this literal Protocol today
    (see Section 12's dependency-direction test and this task's own
    warning against introducing abstraction with no enforcement value --
    a capability's `.handle()` signature varies per capability by design,
    so this Protocol documents the COMMON shape without forcing every
    capability to formally implement it). An ExecutionAdapter must never
    itself decide policy/approval/authority -- see
    run_ros_action_if_governed's own docstring, which is the enforcement
    point, not this Protocol."""

    async def invoke(self, *, capability: str, parameters: dict) -> dict:
        ...
