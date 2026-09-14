"""ActorStateStore Facade - Coordinates storage layer components.

Delegates to specialized components:
- SchemaManager: Schema creation and indexing
- Persistence: Save and load individual states
- Repository: Query and list operations
- BatchExecutor: Batch load/save operations

Maintains backward compatibility with existing ActorStateStore interface.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.monkey_brain.persistence.storage_layers.actor_schema_manager import (
    ActorSchemaManager,
)
from src.monkey_brain.persistence.storage_layers.actor_state_persistence import (
    ActorStatePersistence,
)
from src.monkey_brain.persistence.storage_layers.actor_state_repository import (
    ActorStateRepository,
)
from src.monkey_brain.persistence.storage_layers.batch_operation_executor import (
    BatchOperationExecutor,
)

if TYPE_CHECKING:
    from src.monkey_brain.persistence.actor_state_store import PersistedActorState

logger = logging.getLogger("agentos.persistence.storage_layers.actor_facade")


class ActorStateStoreFacade:
    """Facade coordinating actor state storage components.

    Maintains backward compatibility with original ActorStateStore
    while delegating to focused components.
    """

    def __init__(self, db_connection_pool: Any) -> None:
        self._db = db_connection_pool

        # Layer 1: Schema Management
        self._schema_manager = ActorSchemaManager(db_connection_pool)

        # Layer 2: Persistence (single state)
        self._persistence = ActorStatePersistence(db_connection_pool)

        # Layer 3: Repository (querying)
        self._repository = ActorStateRepository(db_connection_pool)

        # Layer 4: Batch Operations
        self._batch_executor = BatchOperationExecutor(db_connection_pool)

        # Initialize schema
        self._schema_manager._init_schema()

    # ── Backward Compatibility Methods ──────────────────────────────────────

    def save(self, actor_state: PersistedActorState) -> None:
        """Save actor state (delegates to Persistence layer)."""
        self._persistence.save(actor_state)

    def load(self, actor_id: str, tenant_id: str) -> PersistedActorState | None:
        """Load actor state (delegates to Persistence layer)."""
        return self._persistence.load(actor_id, tenant_id)

    def delete(self, actor_id: str, tenant_id: str) -> bool:
        """Delete actor state (delegates to Persistence layer)."""
        return self._persistence.delete(actor_id, tenant_id)

    def list_actors(self, tenant_id: str, active_only: bool = True) -> list[str]:
        """List actors for tenant (delegates to Repository layer)."""
        return self._repository.find_by_tenant(tenant_id, active_only=active_only)

    def batch_load(
        self,
        actor_ids: list[str],
        tenant_id: str,
    ) -> dict[str, PersistedActorState]:
        """Load multiple actors (delegates to Batch layer)."""
        return self._batch_executor.batch_load(actor_ids, tenant_id)

    def batch_save(self, states: list[PersistedActorState]) -> None:
        """Save multiple actors (delegates to Batch layer)."""
        self._batch_executor.batch_save(states)

    def increment_cycle_count(self, actor_id: str, tenant_id: str) -> None:
        """Increment actor cycle count.

        Loads state, increments, and saves back.
        """
        state = self.load(actor_id, tenant_id)
        if state:
            state.cycle_count += 1
            state.cycle_count = state.cycle_count  # Trigger update
            self.save(state)
            logger.debug(
                "[actor_facade] Incremented cycle count for %s (now %d)",
                actor_id,
                state.cycle_count,
            )

    # ── Component Access (for testing/advanced use) ────────────────────────

    @property
    def schema_manager(self) -> ActorSchemaManager:
        """Access schema manager."""
        return self._schema_manager

    @property
    def persistence(self) -> ActorStatePersistence:
        """Access persistence layer."""
        return self._persistence

    @property
    def repository(self) -> ActorStateRepository:
        """Access repository layer."""
        return self._repository

    @property
    def batch_executor(self) -> BatchOperationExecutor:
        """Access batch executor."""
        return self._batch_executor

    # ── Health and Status ───────────────────────────────────────────────────

    @property
    def health(self) -> dict[str, Any]:
        """Get health status of storage layer."""
        return {
            "schema": self._schema_manager.health,
            "initialized": self._schema_manager.is_initialized(),
        }

    def is_ready(self) -> bool:
        """Check if storage layer is ready."""
        return self._schema_manager.is_initialized()
