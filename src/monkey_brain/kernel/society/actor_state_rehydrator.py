"""Actor State Rehydrator — Restore Actors from MongoDB on PlanetaryRuntime startup.

Problem Solved:
    • AgentOS restart → actors must be reseeded (identity lost)
    • MongoDB persists actor_state (belief, model history, cycle count)
    • Redis index can be reconstructed (via RedisIndexReconstructor)
    • But actors still vanish from memory without manual re-registration
    • Result: Broken persistent actor semantics, operator burden

Solution:
    • Load all persisted actor_state records from MongoDB at boot
    • Reconstruct in-memory Actor/ActorRuntime objects in SocietyRuntime
    • Restore belief state, desired state, lifecycle status, affiliations
    • Automatic: No reseeding, operator has zero manual work
    • Deterministic: Same MongoDB records → same in-memory actors
    • Idempotent: Safe to run multiple times (skips duplicates)

Architecture:
    1. ActorStateRehydrator scans MongoDB actor_state collection
    2. For each record, constructs ActorProfile + ActorDesiredState
    3. Calls SocietyRuntime.register_actor() (same path as new registration)
    4. Restores belief state, lifecycle status, affiliations, NATS subscriptions
    5. Returns detailed report: actors_loaded, actors_skipped, errors

Integration:
    • Called from PlanetaryRuntime._init_persistence() after Redis rebuild
    • Runs BEFORE _load_actors() (which loads from Redis hash)
    • If MongoDB unavailable, fails gracefully (log warning, continue)
    • Non-blocking: errors don't crash boot

Design Properties:
    • Deterministic: Same input (MongoDB) always produces same actor state
    • Idempotent: Skips actors already in memory (by actor_id)
    • Incremental: Can be run during normal ops if needed
    • Verified: Logs detailed success/failure per actor
    • Observable: Returns RehydrationResult with counts/errors

Example:
    rehydrator = ActorStateRehydrator(planetary_runtime)
    result = rehydrator.rehydrate_from_mongodb()
    if result.success:
        logger.info(result.summary())
    else:
        logger.warning("Rehydration failed: %s", result.errors)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("agentos.society.actor_state_rehydrator")


@dataclass
class RehydrationResult:
    """Result of actor state rehydration attempt."""
    success: bool
    actors_scanned: int
    actors_rehydrated: int
    actors_skipped: int
    errors: list[tuple[str, str]]  # [(actor_id, error_msg), ...]
    duration_seconds: float

    def summary(self) -> str:
        """Human-readable summary of rehydration."""
        return (
            f"Rehydrated actors from MongoDB: {self.actors_rehydrated} restored "
            f"({self.actors_skipped} skipped, {len(self.errors)} errors) "
            f"in {self.duration_seconds:.2f}s"
        )


class ActorStateRehydrator:
    """Load persisted actor state from MongoDB and reconstruct in-memory actors.
    
    Separated from PlanetaryRuntime for clarity and testability.
    Injected into runtime to avoid circular imports.
    """

    def __init__(self, planetary: Any) -> None:
        """Initialize rehydrator.
        
        Args:
            planetary: PlanetaryRuntime instance for access to MongoDB, societies, etc.
        """
        self._planetary = planetary

    def rehydrate_from_mongodb(self) -> RehydrationResult:
        """Rehydrate all persisted actors from MongoDB into in-memory registry.
        
        Scans MongoDB actor_state collection, reconstructs Actor objects in each
        SocietyRuntime, and restores persisted state (belief, desired_state, etc.).
        
        Returns:
            RehydrationResult with rehydration statistics
        """
        start_time = time.time()
        result = RehydrationResult(
            success=False,
            actors_scanned=0,
            actors_rehydrated=0,
            actors_skipped=0,
            errors=[],
            duration_seconds=0.0,
        )

        try:
            store = self._planetary._get_actor_state_store()
            if not store:
                logger.warning("ActorStateStore unavailable, cannot rehydrate actors from MongoDB")
                return result
            if not hasattr(store, "_db"):
                # Edge/device/robot nodes get kernel/edge/actor_state_
                # store.py::EdgeActorStateStore (SQLite, no `_db`) --
                # MongoDB rehydration is a cloud-node concept (Edge-First
                # Architecture), not a failure for a node that was never
                # Mongo-backed in the first place. Was previously logged
                # as an ERROR ('EdgeActorStateStore' object has no
                # attribute '_db') on every normal edge boot.
                logger.info("Actor state store is not Mongo-backed (edge node) -- skipping MongoDB rehydration")
                result.success = True
                return result

            # Get MongoDB connection
            db = store._db.get_db()
            collection = db[store._collection_name]

            # Scan all persisted actor_state documents
            persisted_actors = list(collection.find({}))
            result.actors_scanned = len(persisted_actors)

            logger.info(
                "Starting actor rehydration: %d actors from MongoDB",
                result.actors_scanned,
            )

            # Rehydrate each persisted actor
            for actor_doc in persisted_actors:
                # Registry identity is stored as a durable projection inside
                # actor_state.  Older records may have the projection at the
                # document root; accept both shapes during migration.
                durable_metadata = actor_doc.get("registry_metadata") or {}
                if durable_metadata:
                    actor_doc = {**durable_metadata, **actor_doc}
                actor_id = actor_doc.get("actor_id", "")
                if not actor_id:
                    logger.warning("Skipping actor_state record with missing actor_id: %s", actor_doc.get("_id"))
                    continue

                try:
                    if actor_doc.get("is_active") is False:
                        result.actors_skipped += 1
                        continue
                    if self._rehydrate_single_actor(actor_id, actor_doc):
                        result.actors_rehydrated += 1
                    else:
                        result.actors_skipped += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to rehydrate actor %s: %s",
                        actor_id, exc,
                    )
                    result.errors.append((actor_id, str(exc)))

            result.success = True
            result.duration_seconds = time.time() - start_time

            logger.info(
                "Actor rehydration complete: %s",
                result.summary(),
            )

            return result

        except Exception as exc:
            logger.error("Actor rehydration failed: %s", exc)
            result.duration_seconds = time.time() - start_time
            return result

    def _rehydrate_single_actor(self, actor_id: str, actor_doc: dict[str, Any]) -> bool:
        """Rehydrate a single actor from MongoDB document.
        
        Args:
            actor_id: Actor ID
            actor_doc: Document from MongoDB actor_state collection
            
        Returns:
            True if actor was rehydrated, False if skipped (already exists)
        """
        try:
            # Check if actor already exists in memory
            for society_runtime in self._planetary._societies.values():
                if society_runtime.get_actor(actor_id):
                    logger.debug("Actor %s already in memory, skipping rehydration", actor_id)
                    return False

            # Extract actor profile from MongoDB
            actor_profile = self._construct_actor_profile_from_mongodb(actor_id, actor_doc)
            if not actor_profile:
                logger.warning("Could not construct actor profile for %s from MongoDB", actor_id)
                return False

            # Determine target society
            society_id = actor_doc.get("society_id", "")
            target_sr = None
            if society_id and society_id in self._planetary._societies:
                target_sr = self._planetary._societies[society_id]
            else:
                # Default to primary society
                target_sr = self._planetary._society_runtime

            if not target_sr:
                logger.warning("No valid society for rehydrating actor %s", actor_id)
                return False

            # Register actor through SocietyRuntime
            # This reconstructs the ActorRuntime and in-memory state
            actor_runtime_state = target_sr.register_actor(actor_profile)

            if not actor_runtime_state:
                logger.warning("Failed to register actor %s during rehydration", actor_id)
                return False

            # Re-subscribe NATS inbox (same as _load_actors does)
            self._planetary._subscribe_actor_inbox(actor_id, actor_profile)

            # Restore persisted lifecycle status
            persisted_status = actor_doc.get("status")
            if persisted_status:
                from src.monkey_brain.kernel.society.domain import ActorStatus
                try:
                    actor_runtime_state.status = ActorStatus(persisted_status)
                except ValueError:
                    logger.debug("Unknown persisted actor status %r for %s", persisted_status, actor_id)

            # Restore belief state if available
            if actor_doc.get("belief_state"):
                try:
                    from src.monkey_brain.kernel.pipeline.belief_state import BeliefState
                    belief_dict = json.loads(actor_doc["belief_state"])
                    belief_state = BeliefState.from_dict(belief_dict)
                    actor_runtime_state.belief_state = belief_state
                except Exception as exc:
                    logger.debug("Could not restore belief state for %s: %s", actor_id, exc)

            # Restore affiliations if available
            if actor_doc.get("affiliations"):
                try:
                    from src.monkey_brain.kernel.affiliations.manager import AffiliationManager
                    restored_affiliations = AffiliationManager.from_dict(actor_doc["affiliations"])
                    actor_runtime = actor_runtime_state.actor_runtime
                    if actor_runtime and hasattr(actor_runtime, "affiliations"):
                        for aff in restored_affiliations.all():
                            actor_runtime.affiliations.add(aff)
                        for target, level in restored_affiliations.trust_engine.all_trust("self").items():
                            actor_runtime.affiliations.trust_engine.set_trust("self", target, level)
                except Exception as exc:
                    logger.debug("Could not restore affiliations for %s: %s", actor_id, exc)

            # Restore desired state if available (persisted in Redis, but also in actor_doc)
            persisted_desired_state = actor_doc.get("desired_state")
            if persisted_desired_state:
                try:
                    from src.monkey_brain.kernel.society.actor_lifecycle import ActorDesiredState
                    desired_value = persisted_desired_state.get("state", "RUNNING")
                    reason = persisted_desired_state.get("reason", "Rehydrated from persistent storage")
                    
                    # Set desired state to restore the control-plane's intent
                    desired_enum = ActorDesiredState(desired_value) if isinstance(desired_value, str) else desired_value
                    self._planetary.set_actor_desired_state(
                        actor_id,
                        desired_enum,
                        reason=reason,
                    )
                    
                    # Immediately apply the desired state if it's not RUNNING
                    # (PAUSED, SUSPENDED, etc.). This ensures actors don't
                    # unexpectedly become active on restart if they were
                    # previously paused.
                    if desired_enum != ActorDesiredState.RUNNING:
                        self._enforce_desired_state_immediately(actor_id, desired_enum, actor_runtime_state)
                        logger.info(
                            "Enforced desired state %s for rehydrated actor %s",
                            desired_enum.value, actor_id,
                        )
                except Exception as exc:
                    logger.debug("Could not restore desired state for %s: %s", actor_id, exc)

            logger.debug("Rehydrated actor %s into %s", actor_id, target_sr.society.name)
            return True

        except Exception as exc:
            logger.warning("Error rehydrating actor %s: %s", actor_id, exc)
            raise

    def _enforce_desired_state_immediately(
        self, actor_id: str, desired_state: Any, actor_runtime_state: Any
    ) -> None:
        """Immediately enforce desired state on rehydrated actor.
        
        If an actor was PAUSED, SUSPENDED, etc., this applies that state
        immediately without waiting for the lifecycle controller's next
        reconciliation cycle. Ensures actors don't unexpectedly become
        active on restart if they were previously paused.
        
        Args:
            actor_id: Actor ID
            desired_state: ActorDesiredState enum value
            actor_runtime_state: The in-memory actor runtime state to modify
        """
        try:
            from src.monkey_brain.kernel.society.actor_lifecycle import ActorDesiredState, ActorStatus
            
            if desired_state == ActorDesiredState.PAUSED:
                # Suspend the actor (prevent it from being scheduled/ticked)
                if hasattr(actor_runtime_state, "status"):
                    actor_runtime_state.status = ActorStatus.SUSPENDED
                    logger.debug("Suspended rehydrated actor %s (was paused)", actor_id)
                    
            elif desired_state == ActorDesiredState.SUSPENDED:
                # Mark as suspended
                if hasattr(actor_runtime_state, "status"):
                    actor_runtime_state.status = ActorStatus.SUSPENDED
                    logger.debug("Suspended rehydrated actor %s", actor_id)
                    
            elif desired_state == ActorDesiredState.TERMINATED:
                # Mark as terminated (won't be scheduled)
                if hasattr(actor_runtime_state, "status"):
                    actor_runtime_state.status = ActorStatus.TERMINATED
                    logger.debug("Terminated rehydrated actor %s", actor_id)
                    
        except Exception as exc:
            logger.warning("Could not immediately enforce desired state for %s: %s", actor_id, exc)

    def _construct_actor_profile_from_mongodb(
        self, actor_id: str, actor_doc: dict[str, Any]
    ) -> Any | None:
        """Construct an ActorProfile from MongoDB actor_state document.
        
        Extracts available metadata and reconstructs the profile structure
        that would normally be created by register_actor().
        
        Args:
            actor_id: Actor ID
            actor_doc: Document from MongoDB actor_state collection
            
        Returns:
            ActorProfile object, or None if unable to construct
        """
        try:
            from src.monkey_brain.kernel.society.domain import ActorProfile, ActorIdentity

            # Extract core identity from MongoDB
            actor_type = actor_doc.get("actor_type", "unknown")
            name = actor_doc.get("name", actor_id)

            # Construct actor identity
            identity = ActorIdentity(
                actor_id=actor_id,
                name=name,
                actor_type=actor_type,
            )

            # Construct actor profile with correct fields (capabilities is optional, not in constructor)
            profile = ActorProfile(
                identity=identity,
                capabilities=tuple(actor_doc.get("capabilities", [])),
                goals=tuple(actor_doc.get("goals", [])),
                policies=tuple(actor_doc.get("policies", [])),
                trust_level=actor_doc.get("trust_level", 0.5),
                ownership=actor_doc.get("ownership", ""),
                objective=actor_doc.get("objective", ""),
                metadata=actor_doc.get("metadata", {}),
            )

            return profile

        except Exception as exc:
            logger.error(
                "Failed to construct actor profile for %s from MongoDB: %s",
                actor_id, exc,
            )
            return None
