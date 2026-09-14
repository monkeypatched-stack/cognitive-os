"""Episodic Memory Schema Management - Single Responsibility.

Responsibility: Create and maintain MongoDB schema for episodic memory.
Depends on: MongoDB database connection
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.compile.solid_interfaces import (
    BootInterface,
    HealthMonitorInterface,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("agentos.persistence.storage_layers.memory_schema")


class MemorySchemaManager(BootInterface, HealthMonitorInterface):
    """Manage MongoDB schema for episodic memory.

    Per-actor episodic memory collection.
    Responsibility: Create collections, indexes, maintain schema.
    """

    def __init__(self, actor_id: str, tenant_id: str, db_connection_pool: Any | None = None) -> None:
        self._actor_id = actor_id
        self._tenant_id = tenant_id
        self._db = db_connection_pool
        self._collection_name = f"episodic_memory_{actor_id}"
        self._initialized = False

    async def boot(self, app: Any, **kwargs: Any) -> None:
        """Initialize schema on boot."""
        if self._db is not None:
            self._init_schema()

    async def shutdown(self, app: Any) -> None:
        """Cleanup on shutdown."""
        self._initialized = False

    @property
    def name(self) -> str:
        """Component name."""
        return f"MemorySchemaManager({self._actor_id})"

    @property
    def health(self) -> dict[str, Any]:
        """Health status."""
        status = "healthy" if (self._initialized or self._db is None) else "uninitialized"
        return {
            "name": self.name,
            "status": status,
            "schema_initialized": self._initialized,
            "collection": self._collection_name,
            "has_db": self._db is not None,
        }

    def _init_schema(self) -> None:
        """Create MongoDB collection if not exists."""
        if self._initialized or self._db is None:
            return

        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            # Create indexes for efficient queries
            collection.create_index([("tenant_id", 1), ("actor_id", 1)])
            collection.create_index([("timestamp", -1)])
            collection.create_index([("episode_type", 1)])

            self._initialized = True
            logger.info(
                "[memory_schema] MongoDB collection initialized: %s",
                self._collection_name,
            )
        except Exception as e:
            logger.warning("[memory_schema] Schema init failed: %s", e)

    def is_initialized(self) -> bool:
        """Check if schema is initialized."""
        return self._initialized

    def get_collection_name(self) -> str:
        """Get collection name."""
        return self._collection_name

    def reset(self) -> None:
        """Reset schema state (for testing)."""
        self._initialized = False
        logger.info("[memory_schema] Schema state reset")
