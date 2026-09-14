"""Runtime Protocols — abstractions for runtime-related types.

These Protocols break the circular import between kernel and runtime.
Runtime imports these Protocols instead of importing from kernel directly.
"""

from __future__ import annotations

from typing import Protocol, Any, runtime_checkable


@runtime_checkable
class ICapabilityProtocol(Protocol):
    """Protocol for capabilities that can be executed."""

    @property
    def name(self) -> str:
        """Capability name."""
        ...

    async def execute(self, state: dict[str, Any]) -> Any:
        """Execute the capability with given state."""
        ...


@runtime_checkable
class WorkloadProtocol(Protocol):
    """Protocol for workloads that contain execution steps."""

    @property
    def workload_id(self) -> str:
        """Unique workload identifier."""
        ...

    @property
    def steps(self) -> list[Any]:
        """List of workload steps."""
        ...

    def step_names(self) -> list[str]:
        """Get names of all steps."""
        ...


@runtime_checkable
class ExecutionStateProtocol(Protocol):
    """Protocol for execution state that tracks question and intent."""

    @property
    def question(self) -> str:
        """The original question."""
        ...

    @property
    def intent(self) -> str:
        """The classified intent."""
        ...

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        ...


@runtime_checkable
class AgentResultProtocol(Protocol):
    """Protocol for results returned by agent execution."""

    @property
    def reward(self) -> float:
        """RL reward signal."""
        ...

    @property
    def payload(self) -> dict[str, Any]:
        """Result payload."""
        ...

    @property
    def success(self) -> bool:
        """Whether execution succeeded."""
        ...


@runtime_checkable
class ExecutionOutcomeProtocol(Protocol):
    """Protocol for execution outcomes published to subscribers."""

    @property
    def goal(self) -> str:
        """The goal being executed."""
        ...

    @property
    def workflow_id(self) -> str:
        """Workflow identifier."""
        ...

    @property
    def result(self) -> Any:
        """The execution result."""
        ...


@runtime_checkable
class OutcomeSubscriberProtocol(Protocol):
    """Protocol for subscribers that receive execution outcomes."""

    async def on_outcome(self, outcome: Any) -> None:
        """Handle an execution outcome."""
        ...


@runtime_checkable
class WorkloadStepProtocol(Protocol):
    """Protocol for workload steps that have metadata and capability names."""

    @property
    def step_id(self) -> str:
        """Step identifier."""
        ...

    @property
    def capability_name(self) -> str:
        """Capability name for this step."""
        ...

    @property
    def metadata(self) -> dict[str, Any] | None:
        """Step metadata (may be None)."""
        ...


@runtime_checkable
class ResolverResultProtocol(Protocol):
    """Protocol for results from agent resolution."""

    @property
    def response(self) -> Any:
        """The response object (may have .answer)."""
        ...

    @property
    def metadata(self) -> dict[str, Any]:
        """Result metadata."""
        ...

    @property
    def success(self) -> bool:
        """Whether resolution succeeded."""
        ...


# NOTE: the public Planet -> Society -> Actor runtime hierarchy contract
# lives in kernel/society/runtime_api.py (Planet/Society/Actor Protocols),
# not here — that module is the single, real, already-wired definition
# (imported by kernel/society/__init__.py, validated by
# tests/test_runtime_boundaries.py). An earlier pass of this session added
# a second, parallel PlanetRuntimeProtocol/SocietyRuntimeProtocol/
# ActorRuntimeProtocol here, unaware runtime_api.py already existed; that
# duplication has been removed. See runtime_api.py's own docstring for the
# hierarchy's rationale ("deliberately small... narrow ownership-facing
# interfaces").
