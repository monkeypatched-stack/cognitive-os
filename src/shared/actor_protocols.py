"""Actor Protocols — abstractions for actor-related types.

These Protocols break the circular import between kernel and actor.
Kernel imports these Protocols instead of importing from src.actor directly.
"""

from __future__ import annotations

from typing import Protocol, Any, runtime_checkable


@runtime_checkable
class AutonomousActorProtocol(Protocol):
    """Protocol for autonomous actors that can be scheduled and ticked."""

    @property
    def actor_id(self) -> str:
        """Unique actor identifier."""
        ...

    @property
    def name(self) -> str:
        """Human-readable actor name."""
        ...

    async def tick(self, world: Any, **kwargs: Any) -> Any:
        """Execute one cognitive cycle."""
        ...

    def get_state(self) -> dict[str, Any]:
        """Get actor state for serialization."""
        ...


@runtime_checkable
class ContextStreamBrokerProtocol(Protocol):
    """Protocol for context stream brokers that handle event distribution."""

    def publish(self, event: Any) -> None:
        """Publish an event to the stream."""
        ...

    def subscribe(self, callback: Any) -> None:
        """Subscribe to events."""
        ...


@runtime_checkable
class TenantContextProtocol(Protocol):
    """Protocol for tenant context that provides isolation boundaries."""

    @property
    def tenant_id(self) -> str:
        """Tenant identifier."""
        ...

    @property
    def permissions(self) -> list[str]:
        """List of permissions granted to this tenant."""
        ...


@runtime_checkable
class CognitiveActorProtocol(Protocol):
    """Protocol for cognitive actors with belief and planning capabilities."""

    @property
    def entity_id(self) -> str:
        """Entity identifier."""
        ...

    async def cognitive_cycle(self, world: Any, **kwargs: Any) -> Any:
        """Execute a full cognitive cycle."""
        ...

    def get_belief(self) -> Any:
        """Get the actor's belief state."""
        ...


@runtime_checkable
class ActorRuntimeProtocol(Protocol):
    """Protocol for actor runtime that manages actor lifecycle."""

    def register_actor(self, actor: Any) -> None:
        """Register an actor with the runtime."""
        ...

    def get_actor(self, actor_id: str) -> Any:
        """Get an actor by ID."""
        ...

    async def tick_actor(self, actor_id: str, world: Any) -> Any:
        """Tick a specific actor."""
        ...
