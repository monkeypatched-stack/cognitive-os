"""Sync Manager — orchestrates edge-cloud synchronization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4


@dataclass
class SyncMessage:
    """Message for edge-cloud sync."""

    message_id: str = field(default_factory=lambda: f"msg-{uuid4().hex[:8]}")
    source: str = ""
    destination: str = ""
    message_type: str = ""  # metrics | events | knowledge | heartbeat
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SyncManager:
    """Orchestrates edge-cloud synchronization.

    Uses NATS or Kafka for message transport.
    Edge nodes sync metrics, events, knowledge to cloud.
    Cloud provides fleet view and control plane.
    """

    def __init__(self, transport: str = "nats", transport_url: str = "nats://localhost:4222"):
        self._transport = transport
        self._transport_url = transport_url
        self._handlers: dict[str, Callable] = {}
        self._message_buffer: list[SyncMessage] = []
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon

    def register_handler(self, message_type: str, handler: Callable) -> None:
        """Register a handler for a message type."""
        self._handlers[message_type] = handler

    async def publish(self, message: SyncMessage) -> None:
        """Publish a sync message."""
        self._message_buffer.append(message)

        if self._lemon:
            self._lemon.counter("sync.messages_published", type=message.message_type)

        # In production, this would use NATS/Kafka
        # For now, store in buffer
        handler = self._handlers.get(message.message_type)
        if handler:
            await handler(message)

    async def subscribe(self, message_type: str, callback: Callable) -> None:
        """Subscribe to a message type."""
        self._handlers[message_type] = callback

    def get_messages(self, message_type: str | None = None, limit: int = 100) -> list[SyncMessage]:
        """Get recent messages."""
        messages = self._message_buffer
        if message_type:
            messages = [m for m in messages if m.message_type == message_type]
        return messages[-limit:]

    def summary(self) -> dict:
        return {
            "transport": self._transport,
            "messages": len(self._message_buffer),
            "handlers": len(self._handlers),
        }
