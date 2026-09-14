"""Actor State Schema Management - Single Responsibility.

Responsibility: Create and maintain MongoDB schema for actor state.
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

logger = logging.getLogger("agentos.persistence.storage_layers.actor_schema")


class ActorSchemaManager(BootInterface, HealthMonitorInterface):
    """Manage MongoDB schema for actor state.

    Responsibility: Create collections, indexes, and maintain schema.
    Does NOT read/write data - only schema operations.
    """

    def __init__(self, db_connection_pool: Any) -> None:
        self._db = db_connection_pool
        self._schema_initialized = False
        self._collection_name = "actor_state"

    async def boot(self, app: Any, **kwargs: Any) -> None:
        """Initialize schema on boot."""
        self._init_schema()

    async def shutdown(self, app: Any) -> None:
        """Cleanup on shutdown."""
        self._schema_initialized = False

    @property
    def name(self) -> str:
        """Component name."""
        return "ActorSchemaManager"

    @property
    def health(self) -> dict[str, Any]:
        """Health status."""
        return {
            "name": self.name,
            "status": "healthy" if self._schema_initialized else "uninitialized",
            "schema_initialized": self._schema_initialized,
            "collection": self._collection_name,
        }

    def _init_schema(self) -> None:
        """Create MongoDB collection and indexes if not exists (cached).

        Phase 4: Skip if already initialized.
        """
        if self._schema_initialized:
            return

        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            # Create indexes for efficient queries
            self._create_indexes(collection)

            self._schema_initialized = True
            logger.info(
                "[actor_schema] MongoDB collection initialized: %s",
                self._collection_name,
            )
        except Exception as e:
            logger.error("[actor_schema] Schema init failed: %s", e)
            raise

    def _create_indexes(self, collection: Any) -> None:
        """Create all required indexes."""
        # Tenant isolation index
        collection.create_index([("tenant_id", 1)])

        # Unique actor per tenant
        collection.create_index([("tenant_id", 1), ("actor_id", 1)], unique=True)

        # Query by update time
        collection.create_index([("last_updated", -1)])

        # Query by world version
        collection.create_index([("world_version", -1)])

        logger.debug("[actor_schema] Indexes created")

    def is_initialized(self) -> bool:
        """Check if schema is initialized."""
        return self._schema_initialized

    def reset(self) -> None:
        """Reset schema state (for testing)."""
        self._schema_initialized = False
        logger.info("[actor_schema] Schema state reset")
