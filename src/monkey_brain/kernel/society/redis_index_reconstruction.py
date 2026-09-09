"""Redis Index Reconstruction — Rebuild Registry from MongoDB after Redis loss.

Context:
    Redis serves as the operational Actor Registry (ephemeral index with
    actor profiles, lifecycle status, node ownership). On Redis restart
    (pod crash, emptyDir recreation, etc.), all registry data is lost but
    actors remain persisted in MongoDB.

    This module provides deterministic, idempotent reconstruction:
    1. Scan MongoDB actor_state collection (source of truth)
    2. Rebuild Redis hash entries with full profile + metadata
    3. Verify consistency between rebuilt index and MongoDB
    4. Automatic repair for stale/incomplete entries

Problem Solved:
    • Redis loss → actors become invisible to registry lookups
    • MongoDB still has the truth; Redis just needs repopulation
    • No manual `kubectl delete pod` needed to trigger recovery
    • No data loss: all actors restore automatically

Design Properties:
    • Deterministic: Same input (MongoDB) always produces same Redis state
    • Idempotent: Safe to run multiple times; skips already-correct entries
    • Incremental: Can run during normal operations (hot rebuild)
    • Verified: Built-in consistency checks between Redis and MongoDB
    • Observable: Detailed logging of reconstruction progress

Configuration (Environment Variables):
    • REDIS_RECENT_ENTRY_TTL_SECONDS (default: 300)
      Skip rebuilding Redis entries updated within this window.
      Use case: After Redis restart, entries within 5 min are considered
               "fresh enough" and rebuild is skipped to avoid unnecessary writes.
      
    • REDIS_STALE_ENTRY_TTL_SECONDS (default: 3600)
      Flag Redis entries not updated in longer than this window as stale.
      Use case: Entries not touched in 1 hour are likely abandoned actors;
               they'll be flagged for rebuild on next consistency check.

Usage:
    # Automatic (on Redis reconnect detection)
    runtime._rebuild_redis_index_from_mongodb()

    # Manual (for debugging or forced rebuild)
    await runtime.rebuild_redis_index_from_mongodb_async()
    
    # Verify after rebuild
    consistency = runtime.verify_redis_mongodb_consistency()
    if not consistency.is_consistent:
        logger.warning("Consistency issues: %s", consistency.issues)

Example (tuning thresholds in deployment):
    export REDIS_RECENT_ENTRY_TTL_SECONDS=600      # 10 minutes
    export REDIS_STALE_ENTRY_TTL_SECONDS=7200      # 2 hours
    kubectl set env deployment/control-plane \\
        -e REDIS_RECENT_ENTRY_TTL_SECONDS=600 \\
        -e REDIS_STALE_ENTRY_TTL_SECONDS=7200
"""

from __future__ import annotations

import logging
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("agentos.society.redis_index_reconstruction")

# Environment variable configuration for reconstruction thresholds
_REDIS_RECENT_ENTRY_TTL_SECONDS = int(
    os.getenv("REDIS_RECENT_ENTRY_TTL_SECONDS", "300")
)
_REDIS_STALE_ENTRY_TTL_SECONDS = int(
    os.getenv("REDIS_STALE_ENTRY_TTL_SECONDS", "3600")
)


@dataclass
class RedisReconstructionResult:
    """Result of Redis index reconstruction attempt."""
    success: bool
    actors_scanned: int
    actors_rebuilt: int
    actors_skipped: int
    errors: list[tuple[str, str]]  # [(actor_id, error_msg), ...]
    duration_seconds: float
    
    def summary(self) -> str:
        """Human-readable summary of reconstruction."""
        return (
            f"Rebuilt Redis index: {self.actors_rebuilt} actors from MongoDB "
            f"({self.actors_skipped} skipped, {len(self.errors)} errors) "
            f"in {self.duration_seconds:.2f}s"
        )


@dataclass
class ConsistencyCheckResult:
    """Result of Redis ↔ MongoDB consistency verification."""
    is_consistent: bool
    total_in_mongodb: int
    total_in_redis: int
    missing_from_redis: list[str]  # Actor IDs only in MongoDB
    missing_from_mongodb: list[str]  # Actor IDs only in Redis (shouldn't happen)
    stale_entries: list[tuple[str, str]]  # [(actor_id, reason), ...] 
    issues: list[str]  # Summary of all issues found
    
    def has_fixable_issues(self) -> bool:
        """Return True if rebuild would fix all issues."""
        return bool(self.missing_from_redis or self.stale_entries)


class RedisIndexReconstructor:
    """Deterministic Registry → Redis index reconstruction from MongoDB.
    
    Separated from PlanetaryRuntime for clarity and testability.
    Injected into runtime to avoid circular imports.
    """
    
    def __init__(self, planetary: Any) -> None:
        """Initialize reconstructor.
        
        Args:
            planetary: PlanetaryRuntime instance for access to Redis, MongoDB, registry
        """
        self._planetary = planetary
        self._redis = planetary._redis
        self._actors_hash_key = "monkeybrain:actors:hash"
    
    def rebuild_from_mongodb(self) -> RedisReconstructionResult:
        """Rebuild Redis actor registry from MongoDB (blocking).
        
        Scan MongoDB actor_state collection, rebuild Redis hash entries.
        Returns immediately on Redis unavailable (fail-open behavior).
        
        Returns:
            RedisReconstructionResult with reconstruction statistics
        """
        start_time = time.time()
        result = RedisReconstructionResult(
            success=False,
            actors_scanned=0,
            actors_rebuilt=0,
            actors_skipped=0,
            errors=[],
            duration_seconds=0.0,
        )
        
        if not self._redis:
            logger.debug("Redis unavailable, skipping index reconstruction")
            return result
        
        try:
            store = self._planetary._get_actor_state_store()
            if not store:
                logger.warning("ActorStateStore unavailable, cannot rebuild Redis index")
                return result
            
            # Scan MongoDB for all actors (source of truth)
            mongodb_actors = self._scan_mongodb_actors(store)
            result.actors_scanned = len(mongodb_actors)
            
            logger.info(
                "Starting Redis index reconstruction: %d actors from MongoDB",
                result.actors_scanned,
            )
            
            # Rebuild Redis entries from MongoDB documents
            for actor_id, actor_doc in mongodb_actors.items():
                try:
                    if self._rebuild_redis_entry(actor_id, actor_doc):
                        result.actors_rebuilt += 1
                    else:
                        result.actors_skipped += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to rebuild Redis entry for %s: %s",
                        actor_id, exc,
                    )
                    result.errors.append((actor_id, str(exc)))
            
            result.success = True
            result.duration_seconds = time.time() - start_time
            
            logger.info(
                "Redis index reconstruction complete: %s",
                result.summary(),
            )
            
            return result
            
        except Exception as exc:
            logger.error("Redis index reconstruction failed: %s", exc)
            result.duration_seconds = time.time() - start_time
            return result
    
    def _scan_mongodb_actors(self, store: Any) -> dict[str, dict[str, Any]]:
        """Scan MongoDB actor_state collection for all actors.
        
        Returns:
            Dict mapping actor_id to actor document from MongoDB
        """
        actors = {}

        if not hasattr(store, "_db"):
            # Edge/device/robot nodes get kernel/edge/actor_state_store.py
            # ::EdgeActorStateStore (SQLite, no `_db`) -- scanning MongoDB
            # is meaningless for a node that was never Mongo-backed.
            # Previously this fell through to the except-Exception branch
            # below and logged an ERROR ('EdgeActorStateStore' object has
            # no attribute '_db') on every normal edge boot, and callers
            # (e.g. PlanetaryRuntime._list_registry_from_mongodb) could not
            # tell "genuinely zero actors in Mongo" apart from "there is no
            # Mongo here" since both returned the same empty dict.
            logger.debug("_scan_mongodb_actors: store is not Mongo-backed (edge node) -- returning empty")
            return actors

        try:
            # Get MongoDB connection
            db = store._db.get_db()
            collection = db[store._collection_name]
            
            # Scan all actor documents (no filter; we want everything)
            cursor = collection.find({})
            for doc in cursor:
                # Extract actor_id from document (schema: {tenant_id}:{actor_id})
                composite_id = doc.get("_id", "")
                if ":" in composite_id:
                    actor_id = composite_id.split(":", 1)[1]
                else:
                    actor_id = composite_id
                
                if actor_id:
                    actors[actor_id] = doc
                    
        except Exception as exc:
            logger.error("Failed to scan MongoDB actors: %s", exc)
        
        return actors
    
    def _rebuild_redis_entry(self, actor_id: str, mongodb_doc: dict[str, Any]) -> bool:
        """Rebuild a single Redis hash entry from MongoDB document.
        
        Args:
            actor_id: Actor ID
            mongodb_doc: Document from MongoDB actor_state collection
            
        Returns:
            True if entry was rebuilt, False if skipped (already valid)
        """
        try:
            # Check if Redis entry already exists and is recent
            existing_raw = self._redis.hget(self._actors_hash_key, actor_id)
            if existing_raw:
                try:
                    existing = json.loads(existing_raw)
                    # If Redis entry is recent (within configured TTL), skip
                    redis_updated = existing.get("updated_at", 0)
                    if time.time() - redis_updated < _REDIS_RECENT_ENTRY_TTL_SECONDS:
                        logger.debug("Redis entry for %s is recent, skipping", actor_id)
                        return False
                except (json.JSONDecodeError, ValueError):
                    pass  # Corrupt entry; rebuild it below
            
            # Reconstruct actor registry entry from MongoDB
            registry_entry = self._construct_registry_entry_from_mongodb(
                actor_id, mongodb_doc
            )
            
            if not registry_entry:
                logger.warning("Could not construct registry entry for %s from MongoDB", actor_id)
                return False
            
            # Write to Redis
            self._redis.hset(
                self._actors_hash_key,
                actor_id,
                json.dumps(registry_entry),
            )
            
            logger.debug("Rebuilt Redis entry for actor %s", actor_id)
            return True
            
        except Exception as exc:
            logger.warning("Error rebuilding Redis entry for %s: %s", actor_id, exc)
            raise
    
    def _construct_registry_entry_from_mongodb(
        self, actor_id: str, mongodb_doc: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Construct a registry entry from MongoDB actor_state document.
        
        Extracts available metadata and reconstructs the structure that
        would normally be created by _actor_state_to_dict() during
        registration.
        
        Args:
            actor_id: Actor ID
            mongodb_doc: Document from MongoDB actor_state collection
            
        Returns:
            Registry entry dict (same format as written by _save_actor),
            or None if unable to construct
        """
        try:
            # PersistedActorState (persistence/actor_state_store.py) has no
            # top-level actor_type/name/society_id/status/node_id fields at
            # all -- PlanetaryRuntime.checkpoint_actor_belief (kernel/
            # society/integration.py) writes all of that identity/registry
            # metadata into the memory_kv sub-object instead (see its own
            # "actor_metadata" dict). Reading them off mongodb_doc's TOP
            # LEVEL, as this used to, always missed and silently fell back
            # to every one of the defaults below -- confirmed live: every
            # Mongo-reconstructed entry came back actor_type="unknown",
            # status="registered", node_id="unknown" regardless of the
            # real, correct values sitting one level down, which made an
            # actually-resident, READY actor look unowned/unrecognized to
            # the Lifecycle Controller the moment Mongo (rather than a
            # warm Redis index) was the source consulted.
            memory_kv = mongodb_doc.get("memory_kv") or {}

            actor_type = memory_kv.get("actor_type", "unknown")
            name = memory_kv.get("name", actor_id)
            society_id = memory_kv.get("society_id", "")

            # Preserve persisted lifecycle status (or default to registered)
            status = memory_kv.get("status", "registered")

            # Construct registry entry in the format expected by _actor_state_to_dict()
            registry_entry = {
                "identity": {
                    "actor_id": actor_id,
                    "name": name,
                    "actor_type": actor_type,
                },
                "society_id": society_id,
                "belief_state": mongodb_doc.get("belief_state"),
                "affiliations": memory_kv.get("affiliations"),
                "status": status,
                "node_id": memory_kv.get("node_id", "unknown"),
                "updated_at": time.time(),  # Mark as just-rebuilt
                "artifact_version": mongodb_doc.get("artifact_version", ""),
                "runtime_version": mongodb_doc.get("runtime_version", ""),
            }
            
            return registry_entry
            
        except Exception as exc:
            logger.error(
                "Failed to construct registry entry for %s from MongoDB: %s",
                actor_id, exc,
            )
            return None
    
    def verify_consistency(self) -> ConsistencyCheckResult:
        """Verify consistency between Redis and MongoDB actor registries.
        
        Checks for:
        • Actors in MongoDB but missing from Redis (rebuilding needed)
        • Actors in Redis but missing from MongoDB (corruption)
        • Stale entries (not updated recently)
        
        Returns:
            ConsistencyCheckResult with detailed findings
        """
        result = ConsistencyCheckResult(
            is_consistent=True,
            total_in_mongodb=0,
            total_in_redis=0,
            missing_from_redis=[],
            missing_from_mongodb=[],
            stale_entries=[],
            issues=[],
        )
        
        if not self._redis:
            result.issues.append("Redis unavailable for consistency check")
            return result
        
        try:
            store = self._planetary._get_actor_state_store()
            if not store:
                result.issues.append("ActorStateStore unavailable")
                return result
            
            # Get MongoDB actors (source of truth)
            mongodb_actors = self._scan_mongodb_actors(store)
            result.total_in_mongodb = len(mongodb_actors)
            
            # Get Redis actors
            redis_entries = self._redis.hgetall(self._actors_hash_key)
            result.total_in_redis = len(redis_entries)
            
            # Parse Redis entries
            redis_actors = {}
            for actor_id, raw in redis_entries.items():
                try:
                    redis_actors[actor_id] = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    result.stale_entries.append((actor_id, "Corrupt JSON"))
                    result.is_consistent = False
            
            # Check for missing from Redis
            for actor_id in mongodb_actors.keys():
                if actor_id not in redis_actors:
                    result.missing_from_redis.append(actor_id)
                    result.is_consistent = False
            
            # Check for missing from MongoDB (shouldn't happen in normal ops)
            for actor_id in redis_actors.keys():
                if actor_id not in mongodb_actors:
                    result.missing_from_mongodb.append(actor_id)
                    result.is_consistent = False
            
            # Check for stale Redis entries (not updated in > 1 hour)
            stale_threshold = time.time() - _REDIS_STALE_ENTRY_TTL_SECONDS
            for actor_id, entry in redis_actors.items():
                updated_at = entry.get("updated_at", 0)
                if updated_at < stale_threshold:
                    result.stale_entries.append(
                        (actor_id, f"Last updated {time.time() - updated_at:.0f}s ago")
                    )
            
            # Build issue summary
            if result.missing_from_redis:
                result.issues.append(
                    f"{len(result.missing_from_redis)} actors in MongoDB missing from Redis"
                )
            if result.missing_from_mongodb:
                result.issues.append(
                    f"{len(result.missing_from_mongodb)} actors in Redis missing from MongoDB"
                )
            if result.stale_entries:
                result.issues.append(
                    f"{len(result.stale_entries)} stale Redis entries"
                )
            
            if result.is_consistent and result.issues:
                # Mark as inconsistent if any issues found
                result.is_consistent = False
            
            return result
            
        except Exception as exc:
            logger.error("Consistency check failed: %s", exc)
            result.issues.append(f"Check failed: {exc}")
            result.is_consistent = False
            return result
    
    def repair_from_consistency_check(
        self, consistency: ConsistencyCheckResult
    ) -> RedisReconstructionResult:
        """Repair Redis index based on consistency check results.
        
        Fixes missing and stale entries identified by verify_consistency().
        
        Args:
            consistency: Result from verify_consistency()
            
        Returns:
            RedisReconstructionResult from repair attempt
        """
        result = RedisReconstructionResult(
            success=True,
            actors_scanned=len(consistency.missing_from_redis) + len(consistency.stale_entries),
            actors_rebuilt=0,
            actors_skipped=0,
            errors=[],
            duration_seconds=0.0,
        )
        
        if not consistency.has_fixable_issues():
            logger.info("No fixable issues found in Redis index")
            return result
        
        start_time = time.time()
        
        try:
            store = self._planetary._get_actor_state_store()
            if not store:
                result.success = False
                return result
            
            # Rebuild missing entries
            mongodb_actors = self._scan_mongodb_actors(store)
            for actor_id in consistency.missing_from_redis:
                if actor_id in mongodb_actors:
                    try:
                        if self._rebuild_redis_entry(actor_id, mongodb_actors[actor_id]):
                            result.actors_rebuilt += 1
                    except Exception as exc:
                        result.errors.append((actor_id, str(exc)))
            
            # Rebuild stale entries
            for actor_id, _ in consistency.stale_entries:
                if actor_id in mongodb_actors:
                    try:
                        if self._rebuild_redis_entry(actor_id, mongodb_actors[actor_id]):
                            result.actors_rebuilt += 1
                    except Exception as exc:
                        result.errors.append((actor_id, str(exc)))
            
            result.duration_seconds = time.time() - start_time
            logger.info("Redis repair complete: %s", result.summary())
            
            return result
            
        except Exception as exc:
            logger.error("Redis repair failed: %s", exc)
            result.success = False
            result.duration_seconds = time.time() - start_time
            return result
