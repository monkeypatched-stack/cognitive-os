"""
CapabilityAdapter – the primary SDK abstraction
================================================

All adapters extend this class and implement exactly four methods:

    initialize()    one-time startup setup
    execute()       the capability logic
    health_check()  external system connectivity check
    shutdown()      graceful teardown

The SDK (LifecycleManager) injects four services at startup:

    self.config     dict built from AdapterConfig for this adapter
    self.logger     stdlib logging.Logger scoped to this adapter
    self.event_bus  EventBusAdapter for platform pub/sub
    self.telemetry  TelemetryManager for metrics and traces
"""

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional

from .context import AdapterContext, AdapterResponse

if TYPE_CHECKING:
    from ..events.bus import EventBusAdapter
    from ..telemetry.manager import TelemetryManager


class AdapterStatus(Enum):
    """Adapter lifecycle state machine values."""

    INITIALIZED = "initialized"
    READY = "ready"
    EXECUTING = "executing"
    IDLE = "idle"
    DEGRADED = "degraded"
    FAILED = "failed"
    SHUTDOWN = "shutdown"


class CapabilityAdapter(ABC):
    """
    Abstract base class every SDK adapter must inherit from.

    Subclass this, decorate with @capability_adapter, implement the
    four abstract methods, and the SDK takes care of everything else.

    Injected attributes (set by LifecycleManager before initialize())
    ------------------------------------------------------------------
    config      : dict  – merged from YAML + env overrides for this adapter
    logger      : logging.Logger
    event_bus   : EventBusAdapter | None
    telemetry   : TelemetryManager | None
    """

    def __init__(self, adapter_id: str, capability_id: str) -> None:
        self.adapter_id = adapter_id
        self.capability_id = capability_id
        self.status = AdapterStatus.INITIALIZED

        # Injected by LifecycleManager
        self.config: Dict[str, Any] = {}
        self.logger: Optional[logging.Logger] = None
        self.event_bus: Optional["EventBusAdapter"] = None
        self.telemetry: Optional["TelemetryManager"] = None

    # ------------------------------------------------------------------
    # Required lifecycle methods
    # ------------------------------------------------------------------

    @abstractmethod
    async def initialize(self) -> None:
        """
        One-time startup.

        Validate configuration, establish connections, and verify the
        external system is reachable.  Raise AdapterInitializationError
        on unrecoverable failure.
        """

    @abstractmethod
    async def execute(
        self,
        context: AdapterContext,
        inputs: Dict[str, Any],
    ) -> AdapterResponse:
        """
        Execute the external capability.

        Contract:
        - Always return AdapterResponse.  Never raise.
        - Use AdapterResponse.ok(result) on success.
        - Use AdapterResponse.fail(error) on failure.
        - context.world_state is read-only.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Return True if the adapter and external system are healthy.

        Called periodically by LifecycleManager.  Return False (not
        raise) when the external system is unreachable.
        """

    @abstractmethod
    async def shutdown(self) -> None:
        """
        Graceful teardown.

        Close connections, flush pending operations, and release
        any held resources.
        """

    # ------------------------------------------------------------------
    # Optional hooks (override as needed)
    # ------------------------------------------------------------------

    async def on_event(self, event: Dict[str, Any]) -> None:
        """Called when the platform emits an event this adapter subscribed to."""

    async def on_configuration_reload(self) -> None:
        """Called after a runtime configuration reload."""

    # ------------------------------------------------------------------
    # SDK-provided helpers (do not override)
    # ------------------------------------------------------------------

    async def emit_event(
        self,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Publish an event to the platform event bus."""
        if self.event_bus is not None:
            await self.event_bus.publish(
                event_type=event_type,
                data=data,
                source=self.adapter_id,
            )

    async def emit_metric(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        """Emit a gauge metric to the configured telemetry backend."""
        if self.telemetry is not None:
            await self.telemetry.emit_metric(
                metric_name=metric_name,
                value=value,
                tags=tags or {},
                source=self.adapter_id,
            )

    def log(self, level: str, message: str, **kwargs: Any) -> None:
        """
        Structured log via the injected logger.

        Falls back to stdlib root logger when no logger is injected.
        """
        _logger = self.logger or logging.getLogger(__name__)
        log_fn = getattr(_logger, level.lower(), _logger.info)
        log_fn(message, extra={"adapter": self.adapter_id, **kwargs})

    def set_status(self, status: AdapterStatus) -> None:
        """Update adapter status (synchronous; no event emitted)."""
        self.status = status

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(adapter_id={self.adapter_id!r}, status={self.status.value!r})"
