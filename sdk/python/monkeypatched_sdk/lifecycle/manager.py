"""
Lifecycle Manager
=================

Orchestrates the full adapter lifecycle:

    1. Startup  – discover, configure, inject, initialize all adapters
    2. Monitor  – run periodic health checks in the background
    3. Execute  – route capability calls to the correct adapter
    4. Shutdown – cancel health loop, call shutdown() on each adapter

Startup sequence
----------------
For each registered adapter class:

    a. Instantiate the class (adapter_id = capability_id)
    b. Inject: logger, event_bus, telemetry, config dict
    c. Call adapter.initialize()
    d. Set status → READY
    e. Emit adapter_ready event

Health loop
-----------
Every ``health_check_interval`` seconds:

    - Call adapter.health_check() on each READY adapter
    - Emit adapter_degraded / adapter_recovered as state changes

Shutdown sequence
-----------------
    a. Cancel the health-check background task
    b. Call adapter.shutdown() on each adapter
    c. Emit adapter_shutdown event
"""

import asyncio
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..contracts.adapter import AdapterStatus, CapabilityAdapter
from ..contracts.context import AdapterContext, AdapterResponse
from ..contracts.interfaces import (
    ILifecycleManager,
    IConfigurationManager,
    IEventBus,
    ITelemetryManager,
)
from .registry import AdapterRegistry

if TYPE_CHECKING:
    from ..configuration.manager import ConfigurationManager
    from ..events.bus import EventBusAdapter
    from ..telemetry.manager import TelemetryManager

logger = logging.getLogger(__name__)


class LifecycleEvent(Enum):
    """Events emitted by the LifecycleManager to the platform event bus."""

    ADAPTER_INITIALIZED = "adapter_initialized"
    ADAPTER_READY = "adapter_ready"
    ADAPTER_EXECUTING = "adapter_executing"
    ADAPTER_DEGRADED = "adapter_degraded"
    ADAPTER_FAILED = "adapter_failed"
    ADAPTER_RECOVERED = "adapter_recovered"
    ADAPTER_SHUTDOWN = "adapter_shutdown"


class LifecycleManager(ILifecycleManager):
    """
    Central coordinator for all registered adapters.

    Implements ILifecycleManager interface for dependency injection and testing.

    Parameters
    ----------
    config_manager:         Optional ConfigurationManager – used to
                            inject adapter config dicts at startup.
    event_bus:              Optional EventBusAdapter – used for lifecycle
                            event publication and injection into adapters.
    telemetry:              Optional TelemetryManager – injected into adapters.
    health_check_interval:  Seconds between background health checks.
    """

    def __init__(
        self,
        config_manager: Optional["ConfigurationManager"] = None,
        event_bus: Optional["EventBusAdapter"] = None,
        telemetry: Optional["TelemetryManager"] = None,
        health_check_interval: int = 30,
    ) -> None:
        self._config_manager = config_manager
        self._event_bus = event_bus
        self._telemetry = telemetry
        self.health_check_interval = health_check_interval

        self.adapters: Dict[str, CapabilityAdapter] = {}
        self.health_checks: Dict[str, bool] = {}
        self._health_check_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public lifecycle API
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """
        Initialise every adapter in AdapterRegistry and start the
        background health-check loop.
        """
        adapter_classes = AdapterRegistry.get_all()
        logger.info("Starting LifecycleManager with %d adapter(s).", len(adapter_classes))

        for adapter_class in adapter_classes:
            await self._start_adapter(adapter_class)

        # Start periodic health monitoring
        self._health_check_task = asyncio.create_task(self._health_check_loop(), name="sdk-health-check")

        logger.info(
            "LifecycleManager ready. %d/%d adapter(s) started.",
            len(self.adapters),
            len(adapter_classes),
        )

    async def shutdown(self) -> None:
        """Gracefully shut down all adapters."""
        logger.info("LifecycleManager shutting down.")

        # Stop health monitoring
        if self._health_check_task is not None:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        # Shutdown adapters in reverse startup order
        for adapter_id, adapter in reversed(list(self.adapters.items())):
            try:
                await adapter.shutdown()
                adapter.set_status(AdapterStatus.SHUTDOWN)
                await self._emit_lifecycle_event(LifecycleEvent.ADAPTER_SHUTDOWN, adapter_id)
                logger.info("Adapter '%s' shut down.", adapter_id)
            except Exception as exc:
                logger.error("Error shutting down adapter '%s': %s", adapter_id, exc)

    async def execute(
        self,
        capability_id: str,
        context: AdapterContext,
        inputs: Dict[str, Any],
    ) -> AdapterResponse:
        """
        Route a capability call to the registered adapter.

        Returns AdapterResponse.fail() if no adapter is registered.
        """
        adapter = self.adapters.get(capability_id)
        if adapter is None:
            return AdapterResponse.fail(f"No adapter registered for capability '{capability_id}'")

        adapter.set_status(AdapterStatus.EXECUTING)
        await self._emit_lifecycle_event(LifecycleEvent.ADAPTER_EXECUTING, capability_id)

        try:
            response = await adapter.execute(context, inputs)
        except Exception as exc:
            logger.error("Unhandled exception in adapter '%s': %s", capability_id, exc)
            response = AdapterResponse.fail(f"Adapter raised unexpectedly: {exc}")
        finally:
            adapter.set_status(AdapterStatus.READY)

        return response

    async def health_check(self) -> Dict[str, bool]:
        """Run health checks on all adapters immediately (not background)."""
        results: Dict[str, bool] = {}
        for adapter_id, adapter in list(self.adapters.items()):
            try:
                results[adapter_id] = await adapter.health_check()
            except Exception as exc:
                logger.error("Health check raised for '%s': %s", adapter_id, exc)
                results[adapter_id] = False
        return results

    async def reload_configuration(self) -> None:
        """
        Reload configuration from disk and notify all adapters.

        Does not restart adapters; calls on_configuration_reload() hook.
        """
        if self._config_manager is not None:
            self._config_manager.reload()
            for adapter_id, adapter in self.adapters.items():
                cfg = self._config_manager.get_adapter_config(adapter_id)
                if cfg is not None:
                    adapter.config = self._build_config_dict(cfg)
                try:
                    await adapter.on_configuration_reload()
                except Exception as exc:
                    logger.warning(
                        "on_configuration_reload raised for '%s': %s",
                        adapter_id,
                        exc,
                    )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_adapter(self, adapter_id: str) -> Optional[CapabilityAdapter]:
        """Return adapter by ID."""
        return self.adapters.get(adapter_id)

    def get_all_adapters(self) -> Dict[str, CapabilityAdapter]:
        """Return a snapshot of all adapters."""
        return dict(self.adapters)

    def get_health_status(self) -> Dict[str, bool]:
        """Return the latest health status for each adapter."""
        return dict(self.health_checks)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _start_adapter(self, adapter_class) -> None:
        """Instantiate, inject, initialize one adapter class."""
        metadata: Dict[str, Any] = getattr(adapter_class, "_adapter_metadata", {})
        capability_id: str = metadata.get("capability_id", adapter_class.__name__)

        try:
            adapter: CapabilityAdapter = adapter_class(
                adapter_id=capability_id,
                capability_id=capability_id,
            )

            # Inject SDK services
            adapter.logger = logging.getLogger(f"sdk.adapter.{capability_id}")
            adapter.event_bus = self._event_bus
            adapter.telemetry = self._telemetry

            # Inject configuration
            if self._config_manager is not None:
                cfg = self._resolve_config(adapter_class, capability_id)
                if cfg is not None:
                    adapter.config = self._build_config_dict(cfg)

            await adapter.initialize()
            adapter.set_status(AdapterStatus.READY)

            self.adapters[capability_id] = adapter
            self.health_checks[capability_id] = True

            await self._emit_lifecycle_event(LifecycleEvent.ADAPTER_READY, capability_id)
            logger.info(
                "Adapter '%s' ready (v%s).",
                capability_id,
                metadata.get("version", "?"),
            )

        except Exception as exc:
            logger.error("Failed to start adapter '%s': %s", adapter_class.__name__, exc)
            await self._emit_lifecycle_event(LifecycleEvent.ADAPTER_FAILED, capability_id)

    def _resolve_config(self, adapter_class, capability_id: str):
        """Find the best-matching AdapterConfig for an adapter."""
        if self._config_manager is None:
            return None

        # 1. Exact match by capability_id
        cfg = self._config_manager.get_adapter_config(capability_id)
        if cfg:
            return cfg

        # 2. Match by lower-cased class name without "adapter" suffix
        class_key = adapter_class.__name__.lower().removesuffix("adapter")
        return self._config_manager.get_adapter_config(class_key)

    @staticmethod
    def _build_config_dict(cfg) -> Dict[str, Any]:
        """Flatten an AdapterConfig into the dict adapters receive."""
        return {
            "endpoint": cfg.endpoint,
            "timeout": cfg.timeout,
            "retry_count": cfg.retry_count,
            "credentials": cfg.credentials or {},
            **(cfg.extra or {}),
        }

    async def _health_check_loop(self) -> None:
        """Background task: periodically check adapter health."""
        while True:
            try:
                await asyncio.sleep(self.health_check_interval)

                for adapter_id, adapter in list(self.adapters.items()):
                    try:
                        healthy = await adapter.health_check()
                        was_healthy = self.health_checks.get(adapter_id, True)

                        if healthy and not was_healthy:
                            adapter.set_status(AdapterStatus.READY)
                            await self._emit_lifecycle_event(LifecycleEvent.ADAPTER_RECOVERED, adapter_id)
                            logger.info("Adapter '%s' recovered.", adapter_id)

                        elif not healthy and was_healthy:
                            adapter.set_status(AdapterStatus.DEGRADED)
                            await self._emit_lifecycle_event(LifecycleEvent.ADAPTER_DEGRADED, adapter_id)
                            logger.warning("Adapter '%s' is degraded.", adapter_id)

                        self.health_checks[adapter_id] = healthy

                    except Exception as exc:
                        logger.error("Health check error for '%s': %s", adapter_id, exc)
                        self.health_checks[adapter_id] = False
                        adapter.set_status(AdapterStatus.DEGRADED)

            except asyncio.CancelledError:
                break

    async def _emit_lifecycle_event(self, event: LifecycleEvent, adapter_id: str) -> None:
        """Emit a lifecycle event to the platform bus (best-effort)."""
        if self._event_bus is not None:
            try:
                await self._event_bus.publish(
                    event_type=event.value,
                    data={"adapter_id": adapter_id},
                    source="lifecycle_manager",
                )
            except Exception as exc:
                logger.debug("Lifecycle event emit failed: %s", exc)

    # ------------------------------------------------------------------
    # ILifecycleManager interface implementation
    # ------------------------------------------------------------------

    def get_adapter_status(self, capability_id: str) -> str:
        """Get current status of an adapter (interface method)."""
        adapter = self.get_adapter(capability_id)
        if adapter is None:
            return "unknown"
        return adapter.status.value

    async def execute_capability(
        self, capability_id: str, context: Dict[str, Any], inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a capability by ID (interface method)."""
        adapter = self.get_adapter(capability_id)
        if adapter is None:
            return {"success": False, "error": f"Adapter {capability_id} not found"}

        try:
            # Convert context to AdapterContext
            from ..contracts.context import AdapterContext

            adapter_context = AdapterContext(
                world_state=context.get("world_state", {}),
                execution_id=context.get("execution_id", ""),
                trace_id=context.get("trace_id", ""),
            )

            # Execute the capability
            result = await adapter.execute(adapter_context, inputs)

            return {
                "success": result.success,
                "data": result.data if result.success else None,
                "error": result.error if not result.success else None,
            }

        except Exception as exc:
            logger.error("Capability execution failed for %s: %s", capability_id, exc)
            return {"success": False, "error": str(exc)}
