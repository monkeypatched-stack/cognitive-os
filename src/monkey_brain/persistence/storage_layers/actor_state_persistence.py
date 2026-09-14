"""Actor State Persistence - Single Responsibility.

Responsibility: Save and load individual actor states.
Depends on: MongoDB database, PersistedActorState dataclass
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.compile.solid_interfaces import PersistenceInterface

if TYPE_CHECKING:
    pass

logger = logging.getLogger("agentos.persistence.storage_layers.actor_persistence")


class ActorStatePersistence(PersistenceInterface):
    """Persist and retrieve individual actor states.

    Responsibility: Save/load actor state to/from MongoDB.
    Does NOT list, query, or delete - only basic persistence.
    """

    def __init__(self, db_connection_pool: Any) -> None:
        self._db = db_connection_pool
        self._collection_name = "actor_state"

    def save(self, actor_state: Any) -> None:
        """Save actor state to MongoDB.

        Args:
            actor_state: PersistedActorState to persist

        Phase 2.3: Tenant isolation via document filtering.
        """
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            # MongoDB document with base64-encoded binary fields
            document = {
                "_id": f"{actor_state.tenant_id}:{actor_state.actor_id}",
                "actor_id": actor_state.actor_id,
                "tenant_id": actor_state.tenant_id,
                "belief_state": base64.b64encode(actor_state.belief_state).decode(),
                "bellman_policy": base64.b64encode(actor_state.bellman_policy).decode(),
                "phi_compiled": base64.b64encode(actor_state.phi_compiled).decode(),
                "memory_kv": actor_state.memory_kv,
                "world_snapshot": base64.b64encode(actor_state.world_snapshot).decode(),
                "world_version": actor_state.world_version,
                "last_updated": actor_state.last_updated,
                "version": actor_state.version,
                "is_active": actor_state.is_active,
                "cycle_count": actor_state.cycle_count,
                "last_cycle": actor_state.last_cycle,
            }

            # Upsert (insert or update)
            collection.replace_one(
                {"_id": document["_id"]},
                document,
                upsert=True,
            )

            logger.debug(
                "[actor_persistence] Saved actor %s (tenant=%s, v%d, world_v%d)",
                actor_state.actor_id,
                actor_state.tenant_id,
                actor_state.version,
                actor_state.world_version,
            )
        except Exception as e:
            logger.error("[actor_persistence] Save failed for %s: %s", actor_state.actor_id, e)
            raise

    def load(self, actor_id: str, tenant_id: str) -> Any | None:
        """Load actor state from MongoDB.

        Args:
            actor_id: Actor identifier
            tenant_id: Tenant identifier

        Returns:
            PersistedActorState if exists, None otherwise
        """
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            # MongoDB query with tenant isolation
            doc = collection.find_one(
                {
                    "_id": f"{tenant_id}:{actor_id}",
                    "tenant_id": tenant_id,
                    "actor_id": actor_id,
                }
            )

            if doc is None:
                logger.debug(
                    "[actor_persistence] No persisted state for %s (tenant=%s)",
                    actor_id,
                    tenant_id,
                )
                return None

            # Reconstruct PersistedActorState from document
            from src.monkey_brain.persistence.actor_state_store import (
                PersistedActorState,
            )

            state = PersistedActorState(
                actor_id=doc["actor_id"],
                tenant_id=doc["tenant_id"],
                belief_state=base64.b64decode(doc["belief_state"]),
                bellman_policy=base64.b64decode(doc["bellman_policy"]),
                phi_compiled=base64.b64decode(doc["phi_compiled"]),
                memory_kv=doc.get("memory_kv", {}),
                world_snapshot=base64.b64decode(doc.get("world_snapshot", b"")),
                world_version=doc.get("world_version", 0),
                last_updated=doc["last_updated"],
                version=doc["version"],
                is_active=doc.get("is_active", True),
                cycle_count=doc.get("cycle_count", 0),
                last_cycle=doc.get("last_cycle", 0.0),
            )

            logger.debug(
                "[actor_persistence] Loaded actor %s (tenant=%s, v%d)",
                actor_id,
                tenant_id,
                state.version,
            )
            return state

        except Exception as e:
            logger.error("[actor_persistence] Load failed for %s: %s", actor_id, e)
            raise

    def delete(self, actor_id: str, tenant_id: str) -> bool:
        """Delete actor state from MongoDB.

        Args:
            actor_id: Actor identifier
            tenant_id: Tenant identifier

        Returns:
            True if deleted, False if not found
        """
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            result = collection.delete_one({"_id": f"{tenant_id}:{actor_id}"})

            if result.deleted_count > 0:
                logger.info(
                    "[actor_persistence] Deleted actor %s (tenant=%s)",
                    actor_id,
                    tenant_id,
                )
                return True

            logger.debug(
                "[actor_persistence] Actor not found: %s (tenant=%s)",
                actor_id,
                tenant_id,
            )
            return False

        except Exception as e:
            logger.error("[actor_persistence] Delete failed for %s: %s", actor_id, e)
            raise
