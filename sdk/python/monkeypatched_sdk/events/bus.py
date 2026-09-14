"""
Event Bus Adapter
=================

Wraps the platform's event bus and exposes a clean SDK interface
for adapter-to-platform pub/sub.

If no platform bus is injected the adapter degrades gracefully:
publish() becomes a no-op and subscribe() stores callbacks locally
for in-process delivery only.

Predefined namespaces
---------------------
workflow.*      – workflow lifecycle events
capability.*    – capability execution events
adapter.*       – adapter lifecycle events (from LifecycleManager)
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventBusAdapter:
    """
    SDK-layer event bus.

    The platform injects its own event bus at construction time.
    When no platform bus is provided the adapter falls back to
    in-process routing only.

    Usage inside an adapter::

        await self.emit_event("work_order_created", {"work_order_id": "WO-001"})

    Or directly::

        await self.event_bus.publish(
            event_type="inventory_updated",
            data={"sku": "ABC123"},
            source=self.adapter_id,
        )
    """

    def __init__(self, platform_event_bus: Optional[Any] = None) -> None:
        self._platform_bus = platform_event_bus
        self._subscribers: Dict[str, Dict[str, Callable]] = {}  # {event_type: {sub_id: cb}}

    # ------------------------------------------------------------------
    # Core publish / subscribe
    # ------------------------------------------------------------------

    async def publish(
        self,
        event_type: str,
        data: Dict[str, Any],
        source: str = "adapter",
    ) -> None:
        """
        Publish an event.

        If a platform bus is available the event is forwarded there.
        Registered SDK-local subscribers are always notified.
        """
        event: Dict[str, Any] = {
            "type": event_type,
            "data": data,
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_id": str(uuid.uuid4()),
        }

        # 1. Forward to platform event bus
        if self._platform_bus is not None:
            try:
                await self._platform_bus.publish(event)
            except Exception as exc:
                logger.warning(
                    "Failed to publish event '%s' to platform bus: %s",
                    event_type,
                    exc,
                )

        # 2. Notify SDK-local subscribers
        await self._dispatch_local(event_type, event)

    def subscribe(
        self,
        event_type: str,
        callback: Callable[[Dict[str, Any]], Any],
    ) -> str:
        """
        Subscribe to events of a given type.

        Returns a subscription ID that can be passed to unsubscribe().
        ``event_type`` supports a single wildcard:  ``"workflow.*"``
        """
        sub_id = str(uuid.uuid4())
        if event_type not in self._subscribers:
            self._subscribers[event_type] = {}
        self._subscribers[event_type][sub_id] = callback
        logger.debug("Subscribed to '%s' with id %s", event_type, sub_id)
        return sub_id

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription by ID."""
        for event_type in list(self._subscribers):
            subs = self._subscribers[event_type]
            if subscription_id in subs:
                del subs[subscription_id]
                if not subs:
                    del self._subscribers[event_type]
                logger.debug("Unsubscribed %s", subscription_id)
                return

    # ------------------------------------------------------------------
    # Convenience publishers
    # ------------------------------------------------------------------

    async def emit_workflow_event(
        self,
        workflow_id: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Emit a workflow-namespaced event."""
        await self.publish(
            event_type=f"workflow.{event_type}",
            data={**data, "workflow_id": workflow_id},
            source="adapter",
        )

    async def emit_capability_event(
        self,
        capability_id: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Emit a capability-namespaced event."""
        await self.publish(
            event_type=f"capability.{event_type}",
            data={**data, "capability_id": capability_id},
            source="adapter",
        )

    async def emit_adapter_event(
        self,
        adapter_id: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Emit an adapter-namespaced event."""
        await self.publish(
            event_type=f"adapter.{event_type}",
            data={**data, "adapter_id": adapter_id},
            source="lifecycle_manager",
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _dispatch_local(self, event_type: str, event: Dict[str, Any]) -> None:
        """Notify in-process subscribers."""
        callbacks: List[Callable] = []

        # Exact match
        for sub_id, cb in self._subscribers.get(event_type, {}).items():
            callbacks.append(cb)

        # Wildcard match: "workflow.*" matches "workflow.started"
        namespace = event_type.split(".")[0] if "." in event_type else None
        if namespace:
            wildcard = f"{namespace}.*"
            for sub_id, cb in self._subscribers.get(wildcard, {}).items():
                callbacks.append(cb)

        for cb in callbacks:
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning("Event subscriber raised: %s", exc)
