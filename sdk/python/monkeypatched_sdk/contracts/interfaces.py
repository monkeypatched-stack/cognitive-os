"""SDK Interfaces — abstraction layer for core SDK components.

This module defines the contracts that enable:
- Dependency injection
- Mocking for testing
- Interchangeable implementations
- Clear architectural boundaries
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..events.bus import EventBusAdapter
    from ..telemetry.manager import TelemetryManager


@runtime_checkable
class IConfigurationManager(Protocol):
    """Interface for configuration management."""

    @abstractmethod
    def load_configuration(self, adapter_id: str) -> Dict[str, Any]:
        """Load configuration for a specific adapter."""
        ...

    @abstractmethod
    def validate_configuration(self, config: Dict[str, Any]) -> bool:
        """Validate adapter configuration."""
        ...

    @abstractmethod
    def get_global_config(self) -> Dict[str, Any]:
        """Get global SDK configuration."""
        ...


@runtime_checkable
class IEventBus(Protocol):
    """Interface for event bus functionality."""

    @abstractmethod
    async def publish(self, event_type: str, data: Dict[str, Any], source: str) -> None:
        """Publish an event to the bus."""
        ...

    @abstractmethod
    async def subscribe(self, event_type: str, callback: callable) -> None:
        """Subscribe to specific event types."""
        ...

    @abstractmethod
    async def unsubscribe(self, event_type: str, callback: callable) -> None:
        """Unsubscribe from event types."""
        ...


@runtime_checkable
class ITelemetryManager(Protocol):
    """Interface for telemetry and observability."""

    @abstractmethod
    async def emit_metric(self, metric_name: str, value: float, tags: Dict[str, str], source: str) -> None:
        """Emit a metric to the telemetry backend."""
        ...

    @abstractmethod
    async def start_span(self, name: str, context: Dict[str, Any]) -> Any:
        """Start a tracing span."""
        ...

    @abstractmethod
    async def end_span(self, span: Any, status: str) -> None:
        """End a tracing span."""
        ...


@runtime_checkable
class IAdapterRegistry(Protocol):
    """Interface for adapter registration and discovery."""

    @abstractmethod
    def register_adapter(self, adapter_class: type, capability_id: str) -> None:
        """Register an adapter class."""
        ...

    @abstractmethod
    def get_adapter(self, capability_id: str) -> Optional[type]:
        """Get adapter class by capability ID."""
        ...

    @abstractmethod
    def list_adapters(self) -> Dict[str, type]:
        """List all registered adapters."""
        ...


@runtime_checkable
class ILifecycleManager(Protocol):
    """Interface for adapter lifecycle management."""

    @abstractmethod
    async def startup(self) -> None:
        """Initialize all registered adapters."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Shutdown all registered adapters."""
        ...

    @abstractmethod
    async def execute_capability(
        self, capability_id: str, context: Dict[str, Any], inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a capability by ID."""
        ...

    @abstractmethod
    def get_adapter_status(self, capability_id: str) -> str:
        """Get current status of an adapter."""
        ...


class SDKDependencies:
    """Dependency container for SDK components."""

    def __init__(
        self,
        config_manager: Optional[IConfigurationManager] = None,
        event_bus: Optional[IEventBus] = None,
        telemetry: Optional[ITelemetryManager] = None,
        adapter_registry: Optional[IAdapterRegistry] = None,
        lifecycle_manager: Optional[ILifecycleManager] = None,
    ):
        self.config_manager = config_manager
        self.event_bus = event_bus
        self.telemetry = telemetry
        self.adapter_registry = adapter_registry
        self.lifecycle_manager = lifecycle_manager

    def with_config_manager(self, config_manager: IConfigurationManager) -> SDKDependencies:
        """Create new container with updated config manager."""
        return SDKDependencies(
            config_manager=config_manager,
            event_bus=self.event_bus,
            telemetry=self.telemetry,
            adapter_registry=self.adapter_registry,
            lifecycle_manager=self.lifecycle_manager,
        )

    def with_event_bus(self, event_bus: IEventBus) -> SDKDependencies:
        """Create new container with updated event bus."""
        return SDKDependencies(
            config_manager=self.config_manager,
            event_bus=event_bus,
            telemetry=self.telemetry,
            adapter_registry=self.adapter_registry,
            lifecycle_manager=self.lifecycle_manager,
        )

    def with_telemetry(self, telemetry: ITelemetryManager) -> SDKDependencies:
        """Create new container with updated telemetry."""
        return SDKDependencies(
            config_manager=self.config_manager,
            event_bus=self.event_bus,
            telemetry=telemetry,
            adapter_registry=self.adapter_registry,
            lifecycle_manager=self.lifecycle_manager,
        )

    def with_adapter_registry(self, adapter_registry: IAdapterRegistry) -> SDKDependencies:
        """Create new container with updated adapter registry."""
        return SDKDependencies(
            config_manager=self.config_manager,
            event_bus=self.event_bus,
            telemetry=self.telemetry,
            adapter_registry=adapter_registry,
            lifecycle_manager=self.lifecycle_manager,
        )

    def with_lifecycle_manager(self, lifecycle_manager: ILifecycleManager) -> SDKDependencies:
        """Create new container with updated lifecycle manager."""
        return SDKDependencies(
            config_manager=self.config_manager,
            event_bus=self.event_bus,
            telemetry=self.telemetry,
            adapter_registry=self.adapter_registry,
            lifecycle_manager=lifecycle_manager,
        )
