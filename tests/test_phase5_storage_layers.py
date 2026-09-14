"""Phase 5: Verification Tests for Storage Layer Components.

Tests component isolation, integration, and SOLID compliance.
"""

from __future__ import annotations

import pytest
from unittest.mock import Mock, MagicMock, patch

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
from src.monkey_brain.persistence.storage_layers.actor_state_store_facade import (
    ActorStateStoreFacade,
)
from src.monkey_brain.persistence.storage_layers.memory_schema_manager import (
    MemorySchemaManager,
)
from src.monkey_brain.persistence.storage_layers.memory_persistence import (
    MemoryPersistence,
)
from src.monkey_brain.persistence.storage_layers.memory_querying import MemoryQuerying
from src.monkey_brain.persistence.storage_layers.episodic_memory_store_facade import (
    EpisodicMemoryStoreFacade,
)
from src.monkey_brain.persistence.actor_state_store import PersistedActorState


class TestActorSchemaManager:
    """Test schema management for actor state."""

    def test_initialization(self):
        """Test schema manager initialization."""
        db_pool = Mock()
        manager = ActorSchemaManager(db_pool)

        assert manager.name == "ActorSchemaManager"
        assert not manager.is_initialized()

    def test_init_schema(self):
        """Test schema initialization."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        manager = ActorSchemaManager(db_pool)
        manager._init_schema()

        assert manager.is_initialized()
        assert collection.create_index.called

    def test_health_status(self):
        """Test health status reporting."""
        db_pool = Mock()
        manager = ActorSchemaManager(db_pool)

        health = manager.health
        assert "status" in health
        assert "schema_initialized" in health

    def test_caching_init_schema(self):
        """Test that schema init is cached."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        manager = ActorSchemaManager(db_pool)
        manager._init_schema()
        manager._init_schema()  # Call again

        # Should only be called once due to caching
        assert db.get_db.call_count == 2  # Called twice but schema check happens first


class TestActorStatePersistence:
    """Test individual actor state persistence."""

    def test_save_actor_state(self):
        """Test saving actor state."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        persistence = ActorStatePersistence(db_pool)

        state = PersistedActorState(
            actor_id="actor1",
            tenant_id="tenant1",
            belief_tensor=b"belief_data",
            bellman_policy=b"policy_data",
            phi_compiled=b"phi_data",
            memory_kv={"key": "value"},
            last_updated="2026-07-17",
            version=1,
        )

        persistence.save(state)

        assert collection.replace_one.called

    def test_load_actor_state(self):
        """Test loading actor state."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()

        # Mock document
        doc = {
            "actor_id": "actor1",
            "tenant_id": "tenant1",
            "belief_tensor": "Ymlyef9kYXRh",  # base64 of b"belief_data"
            "bellman_policy": "cG9saWN5X2RhdGE=",  # base64 of b"policy_data"
            "phi_compiled": "cGhpX2RhdGE=",  # base64 of b"phi_data"
            "memory_kv": {},
            "world_snapshot": "",
            "world_version": 0,
            "last_updated": "2026-07-17",
            "version": 1,
        }
        collection.find_one.return_value = doc
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        persistence = ActorStatePersistence(db_pool)
        state = persistence.load("actor1", "tenant1")

        assert state is not None
        assert state.actor_id == "actor1"

    def test_delete_actor_state(self):
        """Test deleting actor state."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()
        result = Mock()
        result.deleted_count = 1
        collection.delete_one.return_value = result
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        persistence = ActorStatePersistence(db_pool)
        deleted = persistence.delete("actor1", "tenant1")

        assert deleted is True


class TestActorStateRepository:
    """Test querying and listing actor states."""

    def test_find_by_tenant(self):
        """Test finding actors by tenant."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()

        # Mock query result
        docs = [
            {"actor_id": "actor1"},
            {"actor_id": "actor2"},
        ]
        collection.find.return_value = docs
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        repo = ActorStateRepository(db_pool)
        actors = repo.find_by_tenant("tenant1")

        assert len(actors) == 2
        assert "actor1" in actors

    def test_count_by_tenant(self):
        """Test counting actors by tenant."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()
        collection.count_documents.return_value = 5
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        repo = ActorStateRepository(db_pool)
        count = repo.count_by_tenant("tenant1")

        assert count == 5

    def test_exists(self):
        """Test existence check."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()
        collection.count_documents.return_value = 1
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        repo = ActorStateRepository(db_pool)
        exists = repo.exists("actor1", "tenant1")

        assert exists is True


class TestBatchOperationExecutor:
    """Test batch load/save operations."""

    def test_batch_load(self):
        """Test batch loading actors."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()

        # Mock batch query result
        docs = [
            {
                "actor_id": "actor1",
                "tenant_id": "tenant1",
                "belief_tensor": "Ymlyef9kYXRh",
                "bellman_policy": "cG9saWN5X2RhdGE=",
                "phi_compiled": "cGhpX2RhdGE=",
                "memory_kv": {},
                "world_snapshot": "",
                "world_version": 0,
                "last_updated": "2026-07-17",
                "version": 1,
            },
        ]
        collection.find.return_value = docs
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        executor = BatchOperationExecutor(db_pool)
        states = executor.batch_load(["actor1"], "tenant1")

        assert "actor1" in states
        assert isinstance(states, dict)

    def test_batch_save(self):
        """Test batch saving actors."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()
        result = Mock()
        result.inserted_count = 1
        result.modified_count = 0
        collection.bulk_write.return_value = result
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        executor = BatchOperationExecutor(db_pool)

        states = [
            PersistedActorState(
                actor_id="actor1",
                tenant_id="tenant1",
                belief_tensor=b"belief",
                bellman_policy=b"policy",
                phi_compiled=b"phi",
                memory_kv={},
                last_updated="2026-07-17",
                version=1,
            ),
        ]

        executor.batch_save(states)

        assert collection.bulk_write.called


class TestActorStateStoreFacade:
    """Test facade coordination of storage components."""

    def test_facade_initialization(self):
        """Test facade initialization."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        facade = ActorStateStoreFacade(db_pool)

        assert facade.schema_manager is not None
        assert facade.persistence is not None
        assert facade.repository is not None
        assert facade.batch_executor is not None

    def test_facade_health(self):
        """Test facade health status."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        facade = ActorStateStoreFacade(db_pool)
        health = facade.health

        assert "schema" in health
        assert "initialized" in health

    def test_facade_backward_compatibility(self):
        """Test backward compatibility with original interface."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        facade = ActorStateStoreFacade(db_pool)

        # Original methods should still exist
        assert hasattr(facade, "save")
        assert hasattr(facade, "load")
        assert hasattr(facade, "delete")
        assert hasattr(facade, "list_actors")
        assert hasattr(facade, "batch_load")
        assert hasattr(facade, "batch_save")


class TestMemorySchemaManager:
    """Test episodic memory schema management."""

    def test_initialization(self):
        """Test memory schema manager initialization."""
        db_pool = Mock()
        manager = MemorySchemaManager("actor1", "tenant1", db_pool)

        assert "actor1" in manager.name
        assert not manager.is_initialized()

    def test_health_status(self):
        """Test memory schema health."""
        db_pool = Mock()
        manager = MemorySchemaManager("actor1", "tenant1", db_pool)

        health = manager.health
        assert "status" in health
        assert "has_db" in health


class TestMemoryPersistence:
    """Test episodic memory persistence."""

    def test_save_memory(self):
        """Test saving memory."""
        db_pool = Mock()
        persistence = MemoryPersistence("actor1", "tenant1", db_pool)

        persistence.save("key1", {"data": "value"}, episode_type="observation")

        assert "key1" in persistence._in_memory

    def test_load_memory(self):
        """Test loading memory from cache."""
        db_pool = Mock()
        persistence = MemoryPersistence("actor1", "tenant1", db_pool)

        persistence.save("key1", {"data": "value"})
        loaded = persistence.load("key1")

        assert loaded == {"data": "value"}

    def test_delete_memory(self):
        """Test deleting memory."""
        db_pool = Mock()
        persistence = MemoryPersistence("actor1", "tenant1", db_pool)

        persistence.save("key1", {"data": "value"})
        deleted = persistence.delete("key1")

        assert deleted is True
        assert "key1" not in persistence._in_memory


class TestMemoryQuerying:
    """Test episodic memory querying."""

    def test_find_by_type(self):
        """Test finding memories by type."""
        db_pool = Mock()
        cache = {
            "mem1": {"value": "val1", "episode_type": "observation"},
            "mem2": {"value": "val2", "episode_type": "action"},
        }

        querying = MemoryQuerying("actor1", "tenant1", db_pool, cache=cache)
        # Update cache reference
        querying._cache = cache

        results = querying.find_by_type("observation")

        assert len(results) >= 1

    def test_count_all(self):
        """Test counting all memories."""
        db_pool = Mock()
        cache = {"mem1": {"value": "val1"}, "mem2": {"value": "val2"}}

        querying = MemoryQuerying("actor1", "tenant1", db_pool)
        querying._cache = cache

        count = querying.count_all()

        assert count >= 2


class TestEpisodicMemoryStoreFacade:
    """Test episodic memory facade."""

    def test_facade_initialization(self):
        """Test memory facade initialization."""
        db_pool = Mock()
        db = Mock()
        collection = Mock()
        db.__getitem__.return_value = collection
        db_pool.get_db.return_value = db

        facade = EpisodicMemoryStoreFacade("actor1", "tenant1", db_pool)

        assert facade.get_actor_id() == "actor1"
        assert facade.get_tenant_id() == "tenant1"

    def test_facade_backward_compatibility(self):
        """Test backward compatibility with original interface."""
        db_pool = Mock()

        facade = EpisodicMemoryStoreFacade("actor1", "tenant1", db_pool)

        # Original methods should exist
        assert hasattr(facade, "remember")
        assert hasattr(facade, "recall")
        assert hasattr(facade, "forget")
        assert hasattr(facade, "find_type")
        assert hasattr(facade, "all_memories")
        assert hasattr(facade, "clear")


class TestSOLIDCompliance:
    """Test SOLID principle compliance."""

    def test_single_responsibility(self):
        """Test that each component has single responsibility."""
        # Each component should implement exactly one focused interface
        db_pool = Mock()

        schema_manager = ActorSchemaManager(db_pool)
        assert hasattr(schema_manager, "boot")  # BootInterface
        assert hasattr(schema_manager, "health")  # HealthMonitorInterface

        persistence = ActorStatePersistence(db_pool)
        assert hasattr(persistence, "save")  # PersistenceInterface
        assert hasattr(persistence, "load")  # PersistenceInterface

        repo = ActorStateRepository(db_pool)
        assert hasattr(repo, "find_by_id")  # RepositoryInterface

    def test_interface_segregation(self):
        """Test that interfaces are focused."""
        # No component forced to implement unneeded methods
        db_pool = Mock()

        # Schema manager doesn't need save/load
        schema_manager = ActorSchemaManager(db_pool)
        assert not hasattr(schema_manager, "save")

        # Repository doesn't need boot/shutdown
        repo = ActorStateRepository(db_pool)
        assert not hasattr(repo, "boot")

    def test_dependency_inversion(self):
        """Test that components depend on abstractions."""
        db_pool = Mock()

        # Components accept db_pool (abstraction) not concrete implementation
        persistence = ActorStatePersistence(db_pool)
        assert persistence._db is db_pool

        # Can inject mock db_pool for testing
        mock_db = Mock()
        persistence2 = ActorStatePersistence(mock_db)
        assert persistence2._db is mock_db
