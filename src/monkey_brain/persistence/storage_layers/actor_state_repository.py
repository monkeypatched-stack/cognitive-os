"""Actor State Repository - Single Responsibility.

Responsibility: Query and list actor states.
Depends on: MongoDB database
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.compile.solid_interfaces import RepositoryInterface

if TYPE_CHECKING:
    pass

logger = logging.getLogger("agentos.persistence.storage_layers.actor_repository")


class ActorStateRepository(RepositoryInterface):
    """Query and list actor states.

    Responsibility: Find actors, list by tenant, count, exists checks.
    Does NOT create or delete - that's persistence layer.
    """

    def __init__(self, db_connection_pool: Any) -> None:
        self._db = db_connection_pool
        self._collection_name = "actor_state"

    def find_by_id(self, entity_id: str) -> Any | None:
        """Find actor by ID (with tenant context).

        Args:
            entity_id: ID string (format: "tenant_id:actor_id")

        Returns:
            Actor state dict if found, None otherwise
        """
        try:
            if ":" not in entity_id:
                logger.warning("[actor_repository] Invalid entity_id format: %s", entity_id)
                return None

            db = self._db.get_db()
            collection = db[self._collection_name]

            doc = collection.find_one({"_id": entity_id})

            if doc:
                logger.debug("[actor_repository] Found actor: %s", entity_id)
            else:
                logger.debug("[actor_repository] Actor not found: %s", entity_id)

            return doc

        except Exception as e:
            logger.error("[actor_repository] find_by_id failed: %s", e)
            raise

    def find_all(self) -> list[Any]:
        """Find all actors (WARNING: may be expensive).

        Returns:
            List of all actor state documents
        """
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            docs = list(collection.find({}))
            logger.debug("[actor_repository] Found %d actors", len(docs))
            return docs

        except Exception as e:
            logger.error("[actor_repository] find_all failed: %s", e)
            raise

    def save(self, entity: Any) -> None:
        """Not implemented (use ActorStatePersistence)."""
        raise NotImplementedError("Use ActorStatePersistence for saving")

    def delete(self, entity_id: str) -> bool:
        """Not implemented (use ActorStatePersistence)."""
        raise NotImplementedError("Use ActorStatePersistence for deleting")

    def find_by_tenant(self, tenant_id: str, active_only: bool = True) -> list[str]:
        """List actor IDs for a tenant.

        Args:
            tenant_id: Tenant identifier
            active_only: Only return active actors

        Returns:
            List of actor IDs
        """
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            query = {"tenant_id": tenant_id}
            if active_only:
                query["is_active"] = True

            docs = collection.find(query)
            actor_ids = [doc["actor_id"] for doc in docs]

            logger.debug(
                "[actor_repository] Found %d actors for tenant %s",
                len(actor_ids),
                tenant_id,
            )
            return actor_ids

        except Exception as e:
            logger.error("[actor_repository] find_by_tenant failed: %s", e)
            raise

    def count_by_tenant(self, tenant_id: str) -> int:
        """Count actors for a tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Number of actors
        """
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            count = collection.count_documents({"tenant_id": tenant_id})

            logger.debug(
                "[actor_repository] Tenant %s has %d actors",
                tenant_id,
                count,
            )
            return count

        except Exception as e:
            logger.error("[actor_repository] count_by_tenant failed: %s", e)
            raise

    def exists(self, actor_id: str, tenant_id: str) -> bool:
        """Check if actor exists.

        Args:
            actor_id: Actor identifier
            tenant_id: Tenant identifier

        Returns:
            True if actor exists, False otherwise
        """
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            exists = (
                collection.count_documents(
                    {"_id": f"{tenant_id}:{actor_id}"},
                    limit=1,
                )
                > 0
            )

            return exists

        except Exception as e:
            logger.error("[actor_repository] exists check failed: %s", e)
            raise
