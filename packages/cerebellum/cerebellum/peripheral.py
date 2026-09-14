"""Peripheral — hardware or service integration point.

A Peripheral represents a connection to an external system:
- Database (MongoDB, Neo4j, InfluxDB)
- Message Queue (MQTT, Kafka, NATS)
- API (REST, GraphQL, gRPC)
- File System
- Device (OPC-UA, Modbus)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class PeripheralType(str, Enum):
    DATABASE = "database"
    MESSAGE_QUEUE = "message_queue"
    API = "api"
    FILE_SYSTEM = "file_system"
    DEVICE = "device"
    STREAM = "stream"
    CACHE = "cache"


@dataclass
class PeripheralHealth:
    """Health status of a peripheral."""

    status: str = "healthy"  # healthy | degraded | unhealthy
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class Peripheral:
    """A connection to an external system.

    Responsibilities:
    - Manage connection lifecycle
    - Report health status
    - Provide access to external resources

    The Peripheral never:
    - Makes execution decisions
    - Transforms data
    - Manages capabilities
    """

    def __init__(
        self,
        name: str,
        type: PeripheralType,
        config: dict[str, Any] | None = None,
    ):
        self.name = name
        self.type = type
        self.config = config or {}
        self._connected = False
        self._id = f"periph-{uuid4().hex[:8]}"

    @property
    def id(self) -> str:
        return self._id

    async def connect(self) -> None:
        """Establish connection to peripheral."""
        self._connected = True

    async def disconnect(self) -> None:
        """Close connection to peripheral."""
        self._connected = False

    async def health(self) -> PeripheralHealth:
        """Check peripheral health."""
        return PeripheralHealth(
            status="healthy" if self._connected else "unhealthy",
            metadata={"name": self.name, "type": self.type.value},
        )

    def is_connected(self) -> bool:
        return self._connected

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self._id,
            "name": self.name,
            "type": self.type.value,
            "connected": self._connected,
            "config": {k: v for k, v in self.config.items() if k != "password"},
        }
