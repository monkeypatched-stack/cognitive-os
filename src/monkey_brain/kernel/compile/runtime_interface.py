"""Runtime Abstraction Interface — Dependency Inversion Principle.

Defines the contract that all runtimes must implement.
Both CognitiveRuntime and SocietyRuntime depend on this abstraction,
eliminating direct coupling.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional
from dataclasses import dataclass


@dataclass
class RequestContext:
    """Context passed between runtimes."""

    request_id: str
    tenant_id: str = "default"
    metadata: dict[str, Any] | None = None


class RuntimeInterface(ABC):
    """Abstract interface all runtimes implement.

    Defines how runtimes boot, execute work, and coordinate.
    """

    @abstractmethod
    async def boot(self, app: Any, **kwargs) -> "RuntimeInterface":
        """Boot the runtime with kernel-injected dependencies.

        Args:
            app: FastAPI app instance
            **kwargs: Runtime-specific dependencies

        Returns:
            Initialized runtime instance
        """
        pass

    @abstractmethod
    async def execute(self, context: RuntimeContext, work: Any) -> Any:
        """Execute work within this runtime's context.

        Args:
            context: Execution context
            work: Work to execute

        Returns:
            Execution result
        """
        pass

    @abstractmethod
    async def shutdown(self, app: Any) -> None:
        """Gracefully shutdown the runtime.

        Args:
            app: FastAPI app instance
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Runtime name identifier."""
        pass

    @property
    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Get runtime health status."""
        pass


class CognitiveRuntimeInterface(RuntimeInterface):
    """Cognitive reasoning engine interface.

    Responsible for:
    - Question compilation
    - Intent/Goal resolution
    - Execution planning
    - Policy enforcement
    """

    @abstractmethod
    async def compile_intent(self, question: str) -> Any:
        """Compile a question into executable intent."""
        pass

    @abstractmethod
    async def compile_goal(self, intent: Any) -> Any:
        """Compile intent into actionable goal."""
        pass

    @abstractmethod
    def get_society_runtime(self) -> Optional["SocietyRuntimeInterface"]:
        """Get the coordinated SocietyRuntime instance.

        Returns:
            SocietyRuntime if initialized, None otherwise
        """
        pass


class SocietyRuntimeInterface(RuntimeInterface):
    """Multi-actor coordination interface.

    Responsible for:
    - Actor lifecycle management
    - Multi-actor coordination
    - Belief synchronization
    - Cross-actor communication
    """

    @abstractmethod
    async def register_actor(self, actor_id: str, entity: Any, tenant_id: str = "default") -> Any:
        """Register an actor in the society."""
        pass

    @abstractmethod
    async def unregister_actor(self, actor_id: str) -> bool:
        """Unregister an actor from the society."""
        pass

    @abstractmethod
    def get_cognitive_runtime(self) -> Optional["CognitiveRuntimeInterface"]:
        """Get the coordinating CognitiveRuntime instance.

        Returns:
            CognitiveRuntime if initialized, None otherwise
        """
        pass

    @abstractmethod
    def actors_by_tenant(self, tenant_id: str) -> list[Any]:
        """Get all actors for a tenant."""
        pass


class RuntimeCoordinator(ABC):
    """Coordinates execution between multiple runtimes.

    Orchestrates the execution flow:
    CognitiveRuntime → SocietyRuntime → EntityRuntime → ActorRuntime
    """

    @abstractmethod
    async def coordinate(self, cognitive: CognitiveRuntimeInterface, society: SocietyRuntimeInterface) -> None:
        """Establish bidirectional coordination between runtimes.

        Args:
            cognitive: CognitiveRuntime instance
            society: SocietyRuntime instance
        """
        pass
