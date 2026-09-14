"""Episodic Memory Querying - Single Responsibility.

Responsibility: Search and filter episodic memories.
Depends on: MongoDB database, in-memory cache
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.compile.solid_interfaces import RepositoryInterface

if TYPE_CHECKING:
    pass

logger = logging.getLogger("agentos.persistence.storage_layers.memory_querying")


class MemoryQuerying(RepositoryInterface):
    """Search and filter episodic memories.

    Responsibility: Find memories by key, type, timestamp.
    Does NOT store/delete - that's persistence layer.
    """

    def __init__(
        self,
        actor_id: str,
        tenant_id: str,
        db_connection_pool: Any | None = None,
        collection_name: str = "",
        in_memory_cache: dict[str, Any] | None = None,
    ) -> None:
        self._actor_id = actor_id
        self._tenant_id = tenant_id
        self._db = db_connection_pool
        self._collection_name = collection_name or f"episodic_memory_{actor_id}"
        self._cache = in_memory_cache or {}

    def find_by_id(self, entity_id: str) -> Any | None:
        """Find memory by key.

        Args:
            entity_id: Memory key

        Returns:
            Memory entry if found, None otherwise
        """
        if entity_id in self._cache:
            return self._cache[entity_id]

        if self._db is not None:
            return self._find_in_db(entity_id)

        return None

    def find_all(self) -> list[Any]:
        """Get all memories.

        Returns:
            List of all memory entries
        """
        return list(self._cache.values())

    def save(self, entity: Any) -> None:
        """Not implemented (use MemoryPersistence)."""
        raise NotImplementedError("Use MemoryPersistence for saving")

    def delete(self, entity_id: str) -> bool:
        """Not implemented (use MemoryPersistence)."""
        raise NotImplementedError("Use MemoryPersistence for deleting")

    def find_by_type(self, episode_type: str) -> list[Any]:
        """Find memories by type.

        Args:
            episode_type: Memory type (observation, action, outcome, fact, etc.)

        Returns:
            List of matching memories
        """
        results = []

        # Search cache
        for memory in self._cache.values():
            if memory.get("episode_type") == episode_type:
                results.append(memory)

        # Search DB
        if self._db is not None:
            db_results = self._find_by_type_in_db(episode_type)
            results.extend(db_results)

        logger.debug(
            "[memory_querying] Found %d memories of type %s",
            len(results),
            episode_type,
        )
        return results

    def find_recent(self, limit: int = 10) -> list[Any]:
        """Find most recent memories.

        Args:
            limit: Maximum number of results

        Returns:
            List of recent memories (newest first)
        """
        results = []

        # Get recent from cache (assuming cache is relatively small)
        results.extend(self._cache.values())

        # Sort by timestamp (most recent first)
        results.sort(
            key=lambda m: m.get("timestamp", ""),
            reverse=True,
        )

        return results[:limit]

    def count_all(self) -> int:
        """Count all memories."""
        count = len(self._cache)

        if self._db is not None:
            try:
                db = self._db.get_db()
                collection = db[self._collection_name]
                db_count = collection.count_documents({"actor_id": self._actor_id})
                count = max(count, db_count)  # Use larger count
            except Exception as e:
                logger.warning("[memory_querying] Failed to count in DB: %s", e)

        return count

    def exists(self, key: str) -> bool:
        """Check if memory exists.

        Args:
            key: Memory key

        Returns:
            True if memory exists, False otherwise
        """
        if key in self._cache:
            return True

        if self._db is not None:
            return self._exists_in_db(key)

        return False

    # ── Private DB query methods ────────────────────────────────────────────

    def _find_in_db(self, key: str) -> Any | None:
        """Find memory in MongoDB."""
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            doc = collection.find_one(
                {
                    "_id": f"{self._tenant_id}:{self._actor_id}:{key}",
                    "actor_id": self._actor_id,
                }
            )

            if doc:
                return {
                    "value": doc.get("memory_value"),
                    "episode_type": doc.get("episode_type"),
                    "timestamp": doc.get("timestamp"),
                }

            return None
        except Exception as e:
            logger.warning("[memory_querying] Failed to find in DB: %s", e)
            return None

    def _find_by_type_in_db(self, episode_type: str) -> list[Any]:
        """Find memories by type in MongoDB."""
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            docs = collection.find(
                {
                    "actor_id": self._actor_id,
                    "episode_type": episode_type,
                }
            )

            results = []
            for doc in docs:
                results.append(
                    {
                        "value": doc.get("memory_value"),
                        "episode_type": doc.get("episode_type"),
                        "timestamp": doc.get("timestamp"),
                    }
                )

            return results
        except Exception as e:
            logger.warning("[memory_querying] Failed to find by type in DB: %s", e)
            return []

    def _exists_in_db(self, key: str) -> bool:
        """Check if memory exists in MongoDB."""
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            exists = (
                collection.count_documents(
                    {
                        "_id": f"{self._tenant_id}:{self._actor_id}:{key}",
                    },
                    limit=1,
                )
                > 0
            )

            return exists
        except Exception as e:
            logger.warning("[memory_querying] Failed to check existence in DB: %s", e)
            return False
