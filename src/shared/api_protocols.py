"""API Protocols — abstractions for API-layer types.

These Protocols replace hasattr duck-typing in API routes with typed interfaces.
"""

from __future__ import annotations

from typing import Protocol, Any, runtime_checkable


@runtime_checkable
class WorldProtocol(Protocol):
    """Protocol for world objects that expose entities, relationships, events, resources."""

    @property
    def version(self) -> str:
        """World version identifier."""
        ...

    def entities(self) -> list[Any]:
        """Get all entities."""
        ...

    def relationships(self) -> list[Any]:
        """Get all relationships."""
        ...

    def events(self, limit: int = 100) -> list[Any]:
        """Get events with optional limit."""
        ...

    def resources(self) -> list[Any]:
        """Get all resources."""
        ...

    def locations(self) -> list[Any]:
        """Get all locations."""
        ...

    def get_entity(self, entity_id: str) -> Any:
        """Get entity by ID."""
        ...

    def get_resource(self, resource_id: str) -> Any:
        """Get resource by ID."""
        ...

    def update_resource(self, resource_id: str, **kwargs: Any) -> Any:
        """Update a resource."""
        ...

    def get_location(self, location_id: str) -> Any:
        """Get location by ID."""
        ...


@runtime_checkable
class ContextStreamProtocol(Protocol):
    """Protocol for context streams that expose events."""

    def events(self, limit: int = 100) -> list[Any]:
        """Get events with optional limit."""
        ...

    @property
    def event_count(self) -> int:
        """Total event count."""
        ...


@runtime_checkable
class BeliefStateProtocol(Protocol):
    """Protocol for belief states that expose beliefs."""

    @property
    def beliefs(self) -> list[Any]:
        """List of belief entries."""
        ...


@runtime_checkable
class SerializableProtocol(Protocol):
    """Protocol for objects that can be serialized to dict."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        ...


@runtime_checkable
class ActorStateProtocol(Protocol):
    """Protocol for actor runtime state."""

    @property
    def actor_id(self) -> str:
        """Actor identifier."""
        ...

    @property
    def belief_state(self) -> Any:
        """Belief state (may be None)."""
        ...


@runtime_checkable
class PlanetaryRuntimeProtocol(Protocol):
    """Protocol for planetary runtime that exposes world, context, societies."""

    @property
    def world(self) -> Any:
        """The shared world."""
        ...

    @property
    def context_stream(self) -> Any:
        """The context stream."""
        ...

    def all_societies(self) -> list[Any]:
        """Get all society runtimes."""
        ...

    def update_world_location(self, location_id: str, **kwargs: Any) -> Any:
        """Update a world location."""
        ...

    def remove_world_location(self, location_id: str) -> bool:
        """Remove a world location."""
        ...


@runtime_checkable
class TickResultProtocol(Protocol):
    """Protocol for actor tick results that may have various optional fields."""

    @property
    def plan(self) -> Any:
        """The plan (may be None or dict)."""
        ...

    @property
    def actions(self) -> list[Any]:
        """List of actions taken."""
        ...

    @property
    def belief_updated(self) -> bool:
        """Whether beliefs were updated."""
        ...

    @property
    def learned(self) -> bool:
        """Whether learning occurred."""
        ...

    @property
    def error(self) -> str | None:
        """Error message if any."""
        ...

    @property
    def predicted_outcome(self) -> Any:
        """Predicted outcome."""
        ...

    @property
    def outcome(self) -> Any:
        """Actual outcome."""
        ...
