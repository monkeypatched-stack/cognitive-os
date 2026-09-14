"""Store Adapters — interface for persistence stores.

Each store adapter implements the same interface.
The Persistence Manager routes to the appropriate adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IStoreAdapter(ABC):
    """Interface for persistence store adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Store name (mongodb, neo4j, redis, etc.)."""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to store."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to store."""
        ...

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """Check store health."""
        ...

    @abstractmethod
    async def persist(self, event: Any) -> dict[str, Any]:
        """Persist an event to the store."""
        ...

    @abstractmethod
    async def query(self, entity_type: str, entity_id: str | None = None) -> Any:
        """Query entities from the store."""
        ...

    def is_connected(self) -> bool:
        """Check if store is connected."""
        return False
