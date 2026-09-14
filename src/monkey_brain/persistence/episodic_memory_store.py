"""Episodic Memory Store — persists actor memories across requests.

Phase 1 Deliverable 1.4: Integrate actor memory persistence.

Stores episodic memories in MongoDB:
- Previous observations (state transitions)
- Learned facts
- Action outcomes
- Experience summaries
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger("agentos.episodic_memory_store")


class EpisodicMemoryStore:
    """Per-actor episodic memory storage using MongoDB.

    Stores:
    - Episodes (action → observation → outcome)
    - Facts (learned knowledge)
    - History (chronological events)
    """

    def __init__(self, actor_id: str, tenant_id: str, db_connection_pool: Any = None):
        """Initialize episodic memory store.

        Args:
            actor_id: Actor identifier
            tenant_id: Tenant identifier
            db_connection_pool: MongoDB pool (optional; uses memory if None)
        """
        self._actor_id = actor_id
        self._tenant_id = tenant_id
        self._db = db_connection_pool
        self._in_memory: dict[str, Any] = {}
        self._collection_name = f"episodic_memory_{actor_id}"
        self._initialized = False

    def _init_schema(self) -> None:
        """Create MongoDB collection if not exists."""
        if self._db is None:
            logger.debug("[episodic_memory] Using in-memory storage (no DB)")
            return

        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            # Create indexes
            collection.create_index([("tenant_id", 1), ("actor_id", 1)])
            collection.create_index([("timestamp", -1)])

            logger.debug(
                "[episodic_memory] MongoDB collection initialized: %s",
                self._collection_name,
            )
            self._initialized = True
        except Exception as e:
            logger.warning("[episodic_memory] Schema init failed: %s", e)

    def remember(self, key: str, value: Any, episode_type: str = "observation") -> None:
        """Store an episodic memory.

        Args:
            key: Memory key (e.g., "episode_1", "visited_states")
            value: Memory value (serializable)
            episode_type: Type of memory (observation, action, outcome, fact, etc.)
        """
        # Store in-memory
        self._in_memory[key] = {
            "value": value,
            "episode_type": episode_type,
            "timestamp": datetime.now().isoformat(),
        }

        # Optionally persist to MongoDB
        if self._db and self._initialized:
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

                collection.replace_one({"_id": document["_id"]}, document, upsert=True)
            except Exception as e:
                logger.debug("[episodic_memory] DB store failed: %s", e)

    def recall(self, key: str) -> Any | None:
        """Retrieve an episodic memory.

        Args:
            key: Memory key

        Returns:
            Memory value if exists, None otherwise
        """
        # Check in-memory first
        if key in self._in_memory:
            return self._in_memory[key]["value"]

        # Check MongoDB
        if self._db and self._initialized:
            try:
                db = self._db.get_db()
                collection = db[self._collection_name]

                doc = collection.find_one(
                    {
                        "_id": f"{self._tenant_id}:{self._actor_id}:{key}",
                        "tenant_id": self._tenant_id,
                        "actor_id": self._actor_id,
                    }
                )

                if doc:
                    return doc.get("memory_value")
            except Exception as e:
                logger.debug("[episodic_memory] DB recall failed: %s", e)

        return None

    def all_memories(self) -> dict[str, Any]:
        """Get all episodic memories.

        Returns:
            Dict of {key: value}
        """
        result = {}
        for key, mem in self._in_memory.items():
            result[key] = mem["value"]
        return result

    def forget(self, key: str) -> None:
        """Remove an episodic memory.

        Args:
            key: Memory key to forget
        """
        if key in self._in_memory:
            del self._in_memory[key]

        if self._db and self._initialized:
            try:
                db = self._db.get_db()
                collection = db[self._collection_name]

                collection.delete_one(
                    {
                        "_id": f"{self._tenant_id}:{self._actor_id}:{key}",
                        "tenant_id": self._tenant_id,
                        "actor_id": self._actor_id,
                    }
                )
            except Exception as e:
                logger.debug("[episodic_memory] DB forget failed: %s", e)

    def clear(self) -> None:
        """Clear all episodic memories."""
        self._in_memory.clear()

        if self._db and self._initialized:
            try:
                db = self._db.get_db()
                collection = db[self._collection_name]

                collection.delete_many({"tenant_id": self._tenant_id, "actor_id": self._actor_id})
            except Exception as e:
                logger.debug("[episodic_memory] DB clear failed: %s", e)

    def statistics(self) -> dict[str, Any]:
        """Get memory statistics.

        Returns:
            Dict with memory info
        """
        return {
            "actor_id": self._actor_id,
            "tenant_id": self._tenant_id,
            "memory_count": len(self._in_memory),
            "keys": list(self._in_memory.keys()),
        }
