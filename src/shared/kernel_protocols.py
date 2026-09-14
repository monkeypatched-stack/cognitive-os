"""Kernel Protocols — abstractions for kernel-related types.

These Protocols break the circular import between kernel and actor.
Actor imports these Protocols instead of importing from kernel directly.
"""

from __future__ import annotations

from typing import Protocol, Any, runtime_checkable


@runtime_checkable
class EntityProtocol(Protocol):
    """Protocol for entities in the cognitive system."""

    @property
    def entity_id(self) -> str:
        """Unique entity identifier."""
        ...

    @property
    def entity_type(self) -> str:
        """Entity type (e.g., 'person', 'robot', 'car')."""
        ...

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        ...


@runtime_checkable
class EntityTypeProtocol(Protocol):
    """Protocol for entity type classifications."""

    @property
    def name(self) -> str:
        """Type name."""
        ...


@runtime_checkable
class IntentIRProtocol(Protocol):
    """Protocol for intent intermediate representation."""

    @property
    def intent_ir_id(self) -> str:
        """Unique intent IR identifier."""
        ...

    @property
    def intent_type(self) -> str:
        """Type of intent."""
        ...

    @property
    def goal(self) -> dict[str, Any]:
        """Goal specification."""
        ...

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        ...


@runtime_checkable
class CognitionEngineProtocol(Protocol):
    """Protocol for cognition engines that process cognitive workloads."""

    async def execute_cognitive_workload(self, context: Any, **kwargs: Any) -> Any:
        """Execute a cognitive workload."""
        ...

    def compile_intent(self, question: str, **kwargs: Any) -> Any:
        """Compile a question into an intent."""
        ...


@runtime_checkable
class ExecutionGraphProtocol(Protocol):
    """Protocol for execution graphs that track step execution."""

    @property
    def graph_id(self) -> str:
        """Graph identifier."""
        ...

    def add_node(self, node: Any) -> None:
        """Add a node to the graph."""
        ...

    def add_edge(self, edge: Any) -> None:
        """Add an edge to the graph."""
        ...

    def get_runnable_nodes(self) -> list[Any]:
        """Get nodes that are ready to execute."""
        ...
