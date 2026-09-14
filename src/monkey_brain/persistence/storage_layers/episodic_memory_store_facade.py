"""EpisodicMemoryStore Facade - Coordinates memory storage components.

Delegates to specialized components:
- SchemaManager: Schema creation and indexing
- Persistence: Store and retrieve individual memories
- Querying: Search and filter operations
- Lifecycle: Clear and cleanup

Maintains backward compatibility with existing EpisodicMemoryStore interface.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.monkey_brain.persistence.storage_layers.memory_schema_manager import (
    MemorySchemaManager,
)
from src.monkey_brain.persistence.storage_layers.memory_persistence import (
    MemoryPersistence,
)
from src.monkey_brain.persistence.storage_layers.memory_querying import MemoryQuerying

if TYPE_CHECKING:
    pass

logger = logging.getLogger("agentos.persistence.storage_layers.episodic_facade")


class EpisodicMemoryStoreFacade:
    """Facade coordinating episodic memory storage components.

    Per-actor episodic memory management.
    Maintains backward compatibility while delegating to focused components.
    """

    def __init__(self, actor_id: str, tenant_id: str, db_connection_pool: Any | None = None) -> None:
        self._actor_id = actor_id
        self._tenant_id = tenant_id
        self._db = db_connection_pool

        # Layer 1: Schema Management
        self._schema_manager = MemorySchemaManager(actor_id, tenant_id, db_connection_pool)

        # Layer 2: Persistence
        self._persistence = MemoryPersistence(
            actor_id,
            tenant_id,
            db_connection_pool,
            self._schema_manager.get_collection_name(),
        )

        # Layer 3: Querying
        self._querying = MemoryQuerying(
            actor_id,
            tenant_id,
            db_connection_pool,
            self._schema_manager.get_collection_name(),
            self._persistence._in_memory,
        )

        # Initialize schema
        self._schema_manager._init_schema()

    # ── Backward Compatibility Methods ──────────────────────────────────────

    def remember(self, key: str, value: Any, episode_type: str = "observation") -> None:
        """Store an episodic memory (delegates to Persistence layer)."""
        self._persistence.save(key, value, episode_type=episode_type)

    def recall(self, key: str) -> Any | None:
        """Retrieve episodic memory (delegates to Persistence layer)."""
        return self._persistence.load(key)

    def forget(self, key: str) -> bool:
        """Delete episodic memory (delegates to Persistence layer)."""
        return self._persistence.delete(key)

    def find_type(self, episode_type: str) -> list[Any]:
        """Find memories by type (delegates to Querying layer)."""
        return self._querying.find_by_type(episode_type)

    def all_memories(self) -> dict[str, Any]:
        """Get all memories in cache (delegates to Persistence layer)."""
        return self._persistence.get_all()

    def clear(self) -> None:
        """Clear all memories (delegates to Persistence layer)."""
        self._persistence.clear_all()

    # ── Extended Methods ────────────────────────────────────────────────────

    def find_recent(self, limit: int = 10) -> list[Any]:
        """Find most recent memories."""
        return self._querying.find_recent(limit=limit)

    def count_all(self) -> int:
        """Count all memories."""
        return self._querying.count_all()

    def exists(self, key: str) -> bool:
        """Check if memory exists."""
        return self._querying.exists(key)

    # ── Component Access (for testing/advanced use) ────────────────────────

    @property
    def schema_manager(self) -> MemorySchemaManager:
        """Access schema manager."""
        return self._schema_manager

    @property
    def persistence(self) -> MemoryPersistence:
        """Access persistence layer."""
        return self._persistence

    @property
    def querying(self) -> MemoryQuerying:
        """Access querying layer."""
        return self._querying

    # ── Health and Status ───────────────────────────────────────────────────

    @property
    def health(self) -> dict[str, Any]:
        """Get health status of memory storage."""
        return {
            "schema": self._schema_manager.health,
            "initialized": self._schema_manager.is_initialized(),
            "memory_count": self.count_all(),
        }

    def is_ready(self) -> bool:
        """Check if memory storage is ready."""
        return self._schema_manager.is_initialized() or self._db is None

    def get_actor_id(self) -> str:
        """Get actor ID."""
        return self._actor_id

    def get_tenant_id(self) -> str:
        """Get tenant ID."""
        return self._tenant_id
