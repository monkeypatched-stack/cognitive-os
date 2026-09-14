"""ActorState persistence layer — saves/loads actor beliefs, policies, and memory.

Architectural invariant:
    Actor state persists across requests.
    Belief, Bellman, Φ are all persisted to MongoDB.
"""

from __future__ import annotations

import logging
import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger("agentos.persistence.actor_state_store")


@dataclass
class PersistedActorState:
    """Serializable actor state for persistence.

    belief_state: Step 14 — Architecture Consolidation. Renamed from
    belief_tensor; holds json.dumps(BeliefState.to_dict()).encode()
    (kernel/pipeline/belief_state.py — the canonical, live actor belief),
    not a pickled SparseTransitionTensor. bellman_policy/phi_compiled
    remain unpopulated by that consolidation (Decision-layer/PolicyStore
    concern, out of scope) — left here, ready for a future step."""

    actor_id: str
    tenant_id: str
    belief_state: bytes
    bellman_policy: bytes
    phi_compiled: bytes
    memory_kv: dict
    last_updated: str
    version: int
    is_active: bool = True
    cycle_count: int = 0
    last_cycle: float = 0.0
    world_snapshot: bytes = b""  # Phase 1: World snapshot (pickled)
    world_version: int = 0  # Phase 1: World version number
    last_model_provider: str = ""
    """CognitiveOS Constitution: "persistent actors survive interruption,
    restart and changing models." Actor state itself was already
    provider-agnostic (belief/plan, never raw model output, is the
    persisted substrate), so a model swap never broke continuity
    functionally -- but nothing recorded WHICH provider/model last
    reasoned about this actor, so there was no way to audit continuity
    across a model change, only across a same-model process restart.
    Populated from ModelBackend.stats()["provider"] at the same
    checkpoint call that persists belief (see PlanetaryRuntime.
    checkpoint_actor_belief)."""
    last_model_name: str = ""
    """Same provenance as last_model_provider, e.g. "claude-sonnet-4-6"."""


class ActorStateStore:
    """Persistence layer for actor state using MongoDB.

    Stores:
    - Belief state (base64 encoded JSON — kernel/pipeline/belief_state.py::BeliefState.to_dict())
    - Bellman policy (base64 encoded, unpopulated — see PersistedActorState docstring)
    - Compiled Φ operator (base64 encoded, unpopulated — see PersistedActorState docstring)
    - Memory KV store (JSON)

    Queries:
    - Load actor by ID
    - Save actor state
    - List actors by tenant
    - Delete actor
    """

    def __init__(self, db_connection_pool: Any) -> None:
        """Initialize with MongoDB connection pool.

        Args:
            db_connection_pool: MongoDB connection pool
        """
        self._db = db_connection_pool
        self._schema_initialized = False  # Phase 4: Cache schema init state
        self._collection_name = "actor_state"
        self._init_schema()

    def _init_schema(self) -> None:
        """Create MongoDB collection and indexes if not exists (cached)."""
        # Phase 4: Skip if already initialized
        if self._schema_initialized:
            return

        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            # Create indexes for efficient queries
            collection.create_index([("tenant_id", 1)])
            collection.create_index([("tenant_id", 1), ("actor_id", 1)], unique=True)
            collection.create_index([("last_updated", -1)])
            collection.create_index([("world_version", -1)])

            self._schema_initialized = True
            logger.info(
                "[actor_state_store] MongoDB collection initialized: %s",
                self._collection_name,
            )
        except Exception as e:
            logger.error("[actor_state_store] Schema init failed: %s", e)
            raise

    def save(self, actor_state: PersistedActorState) -> None:
        """Save actor state to MongoDB.

        Phase 2.3: Tenant isolation via document filtering.

        Args:
            actor_state: State to persist (includes world snapshot)
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
                "last_model_provider": actor_state.last_model_provider,
                "last_model_name": actor_state.last_model_name,
            }

            # Upsert (insert or update)
            collection.replace_one({"_id": document["_id"]}, document, upsert=True)

            logger.debug(
                "[actor_state_store] Saved actor %s (tenant=%s, v%d, world_v%d)",
                actor_state.actor_id,
                actor_state.tenant_id,
                actor_state.version,
                actor_state.world_version,
            )
        except Exception as e:
            logger.error("[actor_state_store] Save failed for %s: %s", actor_state.actor_id, e)
            raise

    def load(self, actor_id: str, tenant_id: str) -> PersistedActorState | None:
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
                    "[actor_state_store] Actor %s not found in tenant %s",
                    actor_id,
                    tenant_id,
                )
                return None

            # Decode base64-encoded binary fields
            result = PersistedActorState(
                actor_id=doc["actor_id"],
                tenant_id=doc["tenant_id"],
                belief_state=base64.b64decode(doc["belief_state"]),
                bellman_policy=base64.b64decode(doc["bellman_policy"]),
                phi_compiled=base64.b64decode(doc["phi_compiled"]),
                memory_kv=doc.get("memory_kv", {}),
                world_snapshot=base64.b64decode(doc.get("world_snapshot", "")),
                world_version=doc.get("world_version", 0),
                last_updated=doc["last_updated"],
                version=doc["version"],
                is_active=doc.get("is_active", True),
                cycle_count=doc.get("cycle_count", 0),
                last_cycle=doc.get("last_cycle", 0.0),
                last_model_provider=doc.get("last_model_provider", ""),
                last_model_name=doc.get("last_model_name", ""),
            )

            logger.debug(
                "[actor_state_store] Loaded actor %s (tenant=%s, v%d, world_v%d)",
                actor_id,
                tenant_id,
                result.version,
                result.world_version,
            )
            return result
        except Exception as e:
            logger.error("[actor_state_store] Load failed for %s: %s", actor_id, e)
            return None

    def list_actors(self, tenant_id: str, active_only: bool = True) -> list[str]:
        """List all actors in a tenant.

        Args:
            tenant_id: Tenant identifier
            active_only: If True, only return active actors

        Returns:
            List of actor IDs
        """
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            # MongoDB query with tenant isolation
            query = {"tenant_id": tenant_id}
            if active_only:
                query["is_active"] = True

            docs = collection.find(query).sort("last_updated", -1)
            actors = [doc["actor_id"] for doc in docs]

            logger.debug(
                "[actor_state_store] Listed %d actors in tenant %s",
                len(actors),
                tenant_id,
            )
            return actors
        except Exception as e:
            logger.error("[actor_state_store] List failed for tenant %s: %s", tenant_id, e)
            return []

    def delete(self, actor_id: str, tenant_id: str) -> bool:
        """Delete actor state (soft delete — marks inactive).

        Args:
            actor_id: Actor identifier
            tenant_id: Tenant identifier

        Returns:
            True if successful
        """
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            # Soft delete: mark inactive
            result = collection.update_one(
                {"_id": f"{tenant_id}:{actor_id}", "tenant_id": tenant_id},
                {
                    "$set": {
                        "is_active": False,
                        "last_updated": datetime.now().isoformat(),
                    }
                },
            )

            if result.matched_count > 0:
                logger.debug(
                    "[actor_state_store] Deleted actor %s (tenant=%s)",
                    actor_id,
                    tenant_id,
                )
                return True
            return False
        except Exception as e:
            logger.error("[actor_state_store] Delete failed for %s: %s", actor_id, e)
            return False

    def increment_cycle_count(self, actor_id: str, tenant_id: str) -> None:
        """Increment actor's cycle count.

        Args:
            actor_id: Actor identifier
            tenant_id: Tenant identifier
        """
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            collection.update_one(
                {"_id": f"{tenant_id}:{actor_id}", "tenant_id": tenant_id},
                {
                    "$inc": {"cycle_count": 1},
                    "$set": {"last_cycle": datetime.now().timestamp()},
                },
            )
        except Exception as e:
            logger.error("[actor_state_store] Cycle increment failed: %s", e)

    def batch_load(self, actor_ids: list[str], tenant_id: str) -> dict[str, PersistedActorState]:
        """Phase 4: Load multiple actors in single query (avoid N+1).

        Args:
            actor_ids: List of actor IDs to load
            tenant_id: Tenant identifier

        Returns:
            Dict mapping actor_id → PersistedActorState
        """
        if not actor_ids:
            return {}

        result = {}
        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            # MongoDB batch query with tenant isolation
            docs = collection.find({"tenant_id": tenant_id, "actor_id": {"$in": actor_ids}})

            for doc in docs:
                state = PersistedActorState(
                    actor_id=doc["actor_id"],
                    tenant_id=doc["tenant_id"],
                    belief_state=base64.b64decode(doc["belief_state"]),
                    bellman_policy=base64.b64decode(doc["bellman_policy"]),
                    phi_compiled=base64.b64decode(doc["phi_compiled"]),
                    memory_kv=doc.get("memory_kv", {}),
                    world_snapshot=base64.b64decode(doc.get("world_snapshot", "")),
                    world_version=doc.get("world_version", 0),
                    last_updated=doc["last_updated"],
                    version=doc["version"],
                    is_active=doc.get("is_active", True),
                    cycle_count=doc.get("cycle_count", 0),
                    last_cycle=doc.get("last_cycle", 0.0),
                    last_model_provider=doc.get("last_model_provider", ""),
                    last_model_name=doc.get("last_model_name", ""),
                )
                result[doc["actor_id"]] = state

            logger.debug(
                "[actor_state_store] Batch loaded %d/%d actors (tenant=%s)",
                len(result),
                len(actor_ids),
                tenant_id,
            )
        except Exception as e:
            logger.error("[actor_state_store] Batch load failed: %s", e)

        return result

    def batch_save(self, states: list[PersistedActorState]) -> None:
        """Phase 4: Save multiple actors in bulk (more efficient than one-by-one).

        Args:
            states: List of PersistedActorState objects to save
        """
        if not states:
            return

        try:
            db = self._db.get_db()
            collection = db[self._collection_name]

            # Prepare bulk operations
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
                    "last_model_provider": actor_state.last_model_provider,
                    "last_model_name": actor_state.last_model_name,
                }

                from pymongo import ReplaceOne

                operations.append(ReplaceOne({"_id": document["_id"]}, document, upsert=True))

            # Batch write
            if operations:
                collection.bulk_write(operations)
                tenant_id = states[0].tenant_id if states else "unknown"
                logger.debug(
                    "[actor_state_store] Batch saved %d actors (tenant=%s)",
                    len(states),
                    tenant_id,
                )
        except Exception as e:
            logger.error("[actor_state_store] Batch save failed: %s", e)
