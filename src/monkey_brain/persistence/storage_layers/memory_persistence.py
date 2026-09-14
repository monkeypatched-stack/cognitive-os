"""Episodic Memory Persistence - Single Responsibility.

Responsibility: Store and retrieve individual episodic memories.
Depends on: MongoDB database, in-memory cache
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.compile.solid_interfaces import PersistenceInterface

if TYPE_CHECKING:
    pass

logger = logging.getLogger("agentos.persistence.storage_layers.memory_persistence")


class MemoryPersistence(PersistenceInterface):
    """Persist and retrieve episodic memories.

    Hybrid storage: in-memory cache + MongoDB persistence.
    Responsibility: Store/retrieve memories, hybrid sync.
    """

    def __init__(
        self,
        actor_id: str,
        tenant_id: str,
        db_connection_pool: Any | None = None,
        collection_name: str = "",
    ) -> None:
        self._actor_id = actor_id
        self._tenant_id = tenant_id
        self._db = db_connection_pool
        self._collection_name = collection_name or f"episodic_memory_{actor_id}"
        self._in_memory: dict[str, Any] = {}

    def save(self, key: str, value: Any, episode_type: str = "observation") -> None:
        """Store an episodic memory.

        Args:
            key: Memory key (e.g., "episode_1")
            value: Memory value (serializable)
            episode_type: Type (observation, action, outcome, fact, etc.)
        """
        # Store in-memory
        self._in_memory[key] = {
            "value": value,
            "episode_type": episode_type,
            "timestamp": datetime.now().isoformat(),
        }

        # Optionally persist to MongoDB
        if self._db is not None:
            self._persist_to_db(key, value, episode_type)

        logger.debug(
            "[memory_persistence] Saved memory: key=%s, type=%s",
            key,
            episode_type,
        )

    def load(self, key: str) -> Any | None:
        """Load episodic memory from cache.

        Args:
            key: Memory key

        Returns:
            Memory value if found, None otherwise
        """
        if key in self._in_memory:
            logger.debug("[memory_persistence] Retrieved memory from cache: %s", key)
            return self._in_memory[key].get("value")

        # Try loading from MongoDB
        if self._db is not None:
            value = self._load_from_db(key)
            if value is not None:
                logger.debug("[memory_persistence] Retrieved memory from DB: %s", key)
                return value

        logger.debug("[memory_persistence] Memory not found: %s", key)
        return None

    def delete(self, key: str) -> bool:
        """Delete episodic memory.

        Args:
            key: Memory key

        Returns:
            True if deleted, False if not found
        """
        deleted = False

        if key in self._in_memory:
            del self._in_memory[key]
            deleted = True

        if self._db is not None:
            deleted = self._delete_from_db(key) or deleted

        if deleted:
            logger.debug("[memory_persistence] Deleted memory: %s", key)

        return deleted

    def _persist_to_db(self, key: str, value: Any, episode_type: str) -> None:
        """Persist memory to MongoDB."""
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            document = {
                "_id": f"{self._tenant_id}:{self._actor_id}:{key}",
                "actor_id": self._actor_id,
                "tenant_id": self._tenant_id,
                "memory_key": key,
                "memory_value": value,
                "episode_type": episode_type,
                "timestamp": datetime.now().isoformat(),
            }

            collection.replace_one(
                {"_id": document["_id"]},
                document,
                upsert=True,
            )
        except Exception as e:
            logger.warning("[memory_persistence] Failed to persist to DB: %s", e)

    def _load_from_db(self, key: str) -> Any | None:
        """Load memory from MongoDB."""
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
                # Cache in memory
                self._in_memory[key] = {
                    "value": doc["memory_value"],
                    "episode_type": doc.get("episode_type", "unknown"),
                    "timestamp": doc.get("timestamp"),
                }
                return doc.get("memory_value")

            return None
        except Exception as e:
            logger.warning("[memory_persistence] Failed to load from DB: %s", e)
            return None

    def _delete_from_db(self, key: str) -> bool:
        """Delete memory from MongoDB."""
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            result = collection.delete_one({"_id": f"{self._tenant_id}:{self._actor_id}:{key}"})

            return result.deleted_count > 0
        except Exception as e:
            logger.warning("[memory_persistence] Failed to delete from DB: %s", e)
            return False

    def clear_all(self) -> None:
        """Clear all memories (both cache and DB)."""
        self._in_memory.clear()

        if self._db is not None:
            try:
                db = self._db.get_db()
                collection = db[self._collection_name]
                collection.delete_many({"actor_id": self._actor_id})
                logger.info("[memory_persistence] Cleared all memories")
            except Exception as e:
                logger.warning("[memory_persistence] Failed to clear memories: %s", e)

    def get_all(self) -> dict[str, Any]:
        """Get all cached memories."""
        return self._in_memory.copy()
