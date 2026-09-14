"""Batch Operation Executor - Single Responsibility.

Responsibility: Execute batch load/save operations efficiently.
Depends on: MongoDB database, PersistedActorState dataclass
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.compile.solid_interfaces import ExecutorInterface

if TYPE_CHECKING:
    pass

logger = logging.getLogger("agentos.persistence.storage_layers.batch_executor")


class BatchOperationExecutor(ExecutorInterface):
    """Execute batch persistence operations efficiently.

    Responsibility: Batch load/save to minimize round-trips.
    Uses MongoDB bulk operations for efficiency.
    """

    def __init__(self, db_connection_pool: Any) -> None:
        self._db = db_connection_pool
        self._collection_name = "actor_state"

    async def execute(self, work: Any) -> Any:
        """Execute batch operation (supports ExecutorInterface)."""
        if isinstance(work, dict):
            if "batch_load" in work:
                return self.batch_load(
                    work["actor_ids"],
                    work["tenant_id"],
                )
            elif "batch_save" in work:
                return self.batch_save(work["states"])

        raise TypeError(f"Unsupported batch work: {type(work)}")

    def batch_load(
        self,
        actor_ids: list[str],
        tenant_id: str,
    ) -> dict[str, Any]:
        """Load multiple actors in one query.

        Args:
            actor_ids: List of actor IDs
            tenant_id: Tenant identifier

        Returns:
            Dict mapping actor_id → PersistedActorState
        """
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            # Load multiple actors with single query
            docs = collection.find(
                {
                    "_id": {"$in": [f"{tenant_id}:{aid}" for aid in actor_ids]},
                    "tenant_id": tenant_id,
                }
            )

            # Reconstruct states
            from src.monkey_brain.persistence.actor_state_store import (
                PersistedActorState,
            )

            states = {}
            for doc in docs:
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
                states[doc["actor_id"]] = state

            logger.info(
                "[batch_executor] Batch loaded %d/%d actors (tenant=%s)",
                len(states),
                len(actor_ids),
                tenant_id,
            )
            return states

        except Exception as e:
            logger.error("[batch_executor] Batch load failed: %s", e)
            raise

    def batch_save(self, states: list[Any]) -> None:
        """Save multiple actors in one batch operation.

        Args:
            states: List of PersistedActorState objects
        """
        if not states:
            logger.debug("[batch_executor] No states to save")
            return

        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            # Prepare documents
            operations = []
            for actor_state in states:
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

                # Replace or insert
                from pymongo import ReplaceOne

                operations.append(ReplaceOne({"_id": document["_id"]}, document, upsert=True))

            # Execute bulk write
            if operations:
                result = collection.bulk_write(operations)
                logger.info(
                    "[batch_executor] Batch saved %d actors (inserted=%d, modified=%d)",
                    len(states),
                    result.inserted_count,
                    result.modified_count,
                )

        except Exception as e:
            logger.error("[batch_executor] Batch save failed: %s", e)
            raise
