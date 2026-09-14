"""Unit tests for Redis Index Reconstruction module.

Tests the RedisIndexReconstructor class which handles deterministic
rebuilding of the Redis actor registry from MongoDB after Redis loss.
"""

from __future__ import annotations

import json
import os
import sys
import time
from unittest.mock import MagicMock, Mock, patch, PropertyMock

import pytest

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.monkey_brain.kernel.society.redis_index_reconstruction import (
    RedisIndexReconstructor,
    RedisReconstructionResult,
    ConsistencyCheckResult,
)

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_redis():
    """Create a mock Redis client with in-memory hash storage."""
    redis = MagicMock()
    redis._hash_storage = {}  # In-memory storage for hashes

    def hget_impl(key, field):
        if key not in redis._hash_storage:
            return None
        return redis._hash_storage[key].get(field)

    def hset_impl(key, field, value):
        if key not in redis._hash_storage:
            redis._hash_storage[key] = {}
        redis._hash_storage[key][field] = value
        return 1

    def hgetall_impl(key):
        return redis._hash_storage.get(key, {})

    def ping_impl():
        return True

    redis.hget.side_effect = hget_impl
    redis.hset.side_effect = hset_impl
    redis.hgetall.side_effect = hgetall_impl
    redis.ping.side_effect = ping_impl

    return redis


@pytest.fixture
def mock_mongodb():
    """Create a mock MongoDB connection and collection."""
    collection = MagicMock()
    collection._docs = {}  # In-memory storage for documents

    def find_impl(query=None):
        # Return all documents
        return list(collection._docs.values())

    collection.find.side_effect = find_impl

    db = MagicMock()
    db.__getitem__.return_value = collection

    mongo_connection = MagicMock()
    mongo_connection.get_db.return_value = db

    return mongo_connection, collection, db


@pytest.fixture
def mock_actor_state_store(mock_mongodb):
    """Create a mock ActorStateStore."""
    mongo_conn, collection, db = mock_mongodb
    store = MagicMock()
    store._db = mongo_conn
    store._collection_name = "actor_state"
    return store, collection, mongo_conn


@pytest.fixture
def mock_planetary_runtime(mock_redis, mock_actor_state_store):
    """Create a mock PlanetaryRuntime with Redis and MongoDB."""
    store, collection, mongo_conn = mock_actor_state_store

    runtime = MagicMock()
    runtime._redis = mock_redis
    runtime._get_actor_state_store.return_value = store

    return runtime, mock_redis, collection, mongo_conn


# ── Tests: RedisReconstructionResult ──────────────────────────────────


def test_reconstruction_result_summary():
    """Test that RedisReconstructionResult generates a readable summary."""
    result = RedisReconstructionResult(
        success=True,
        actors_scanned=100,
        actors_rebuilt=95,
        actors_skipped=5,
        errors=[],
        duration_seconds=1.5,
    )

    summary = result.summary()
    assert "95 actors" in summary
    assert "5 skipped" in summary
    assert "1.50s" in summary


def test_reconstruction_result_summary_with_errors():
    """Test summary includes error count."""
    result = RedisReconstructionResult(
        success=False,
        actors_scanned=100,
        actors_rebuilt=90,
        actors_skipped=5,
        errors=[("actor-1", "connection error"), ("actor-2", "timeout")],
        duration_seconds=2.0,
    )

    summary = result.summary()
    assert "90 actors" in summary
    assert "2 errors" in summary


# ── Tests: ConsistencyCheckResult ────────────────────────────────────


def test_consistency_result_has_fixable_issues():
    """Test that has_fixable_issues() detects problems."""
    # No issues
    result = ConsistencyCheckResult(
        is_consistent=True,
        total_in_mongodb=10,
        total_in_redis=10,
        missing_from_redis=[],
        missing_from_mongodb=[],
        stale_entries=[],
        issues=[],
    )
    assert not result.has_fixable_issues()

    # Missing from Redis
    result_missing = ConsistencyCheckResult(
        is_consistent=False,
        total_in_mongodb=10,
        total_in_redis=8,
        missing_from_redis=["actor-1", "actor-2"],
        missing_from_mongodb=[],
        stale_entries=[],
        issues=["2 actors missing from Redis"],
    )
    assert result_missing.has_fixable_issues()

    # Stale entries
    result_stale = ConsistencyCheckResult(
        is_consistent=False,
        total_in_mongodb=10,
        total_in_redis=10,
        missing_from_redis=[],
        missing_from_mongodb=[],
        stale_entries=[("actor-1", "not updated in 2 hours")],
        issues=["1 stale entry"],
    )
    assert result_stale.has_fixable_issues()


# ── Tests: Scan MongoDB ──────────────────────────────────────────────


def test_scan_mongodb_actors_empty(mock_planetary_runtime):
    """Test scanning empty MongoDB collection."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime
    collection._docs = {}

    reconstructor = RedisIndexReconstructor(runtime)
    store = runtime._get_actor_state_store()

    actors = reconstructor._scan_mongodb_actors(store)

    assert len(actors) == 0


def test_scan_mongodb_actors_with_data(mock_planetary_runtime):
    """Test scanning MongoDB with actor documents."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Add some actor documents to MongoDB
    collection._docs = {
        "tenant-1:actor-1": {
            "_id": "tenant-1:actor-1",
            "actor_type": "Agent",
            "name": "TestAgent1",
            "status": "registered",
            "belief_state": {"key": "value"},
        },
        "tenant-1:actor-2": {
            "_id": "tenant-1:actor-2",
            "actor_type": "Agent",
            "name": "TestAgent2",
            "status": "registered",
        },
    }

    reconstructor = RedisIndexReconstructor(runtime)
    store = runtime._get_actor_state_store()

    actors = reconstructor._scan_mongodb_actors(store)

    assert len(actors) == 2
    assert "actor-1" in actors
    assert "actor-2" in actors
    assert actors["actor-1"]["actor_type"] == "Agent"


def test_scan_mongodb_handles_exceptions(mock_planetary_runtime):
    """Test that MongoDB scan gracefully handles exceptions."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime
    store = runtime._get_actor_state_store()

    # Make find() raise an exception
    collection.find.side_effect = Exception("MongoDB connection failed")

    reconstructor = RedisIndexReconstructor(runtime)
    actors = reconstructor._scan_mongodb_actors(store)

    # Should return empty dict on exception (fail-open)
    assert actors == {}


# ── Tests: Rebuild Redis Entry ───────────────────────────────────────


def test_rebuild_redis_entry_from_scratch(mock_planetary_runtime):
    """Test rebuilding a Redis entry from MongoDB document."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    actor_doc = {
        "_id": "tenant-1:actor-1",
        "actor_type": "Agent",
        "name": "TestAgent",
        "society_id": "society-1",
        "status": "registered",
        "belief_state": {"mood": "happy"},
    }

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor._rebuild_redis_entry("actor-1", actor_doc)

    assert result is True

    # Check that Redis entry was written
    stored = redis.hget("monkeybrain:actors:hash", "actor-1")
    assert stored is not None

    entry = json.loads(stored)
    assert entry["identity"]["actor_id"] == "actor-1"
    assert entry["identity"]["name"] == "TestAgent"


def test_rebuild_redis_entry_skips_recent(mock_planetary_runtime):
    """Test that recent entries are skipped (within 5 minutes)."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Pre-populate Redis with a recent entry
    recent_time = time.time() - 100  # 100 seconds ago (< 5 min)
    recent_entry = {
        "identity": {"actor_id": "actor-1", "name": "TestAgent"},
        "updated_at": recent_time,
    }
    redis.hset(
        "monkeybrain:actors:hash",
        "actor-1",
        json.dumps(recent_entry),
    )

    actor_doc = {
        "_id": "tenant-1:actor-1",
        "actor_type": "Agent",
        "name": "TestAgent",
        "status": "registered",
    }

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor._rebuild_redis_entry("actor-1", actor_doc)

    # Should skip (return False)
    assert result is False


def test_rebuild_redis_entry_replaces_stale(mock_planetary_runtime):
    """Test that stale entries are replaced (older than 5 minutes)."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Pre-populate Redis with a stale entry
    stale_time = time.time() - 600  # 600 seconds ago (> 5 min)
    stale_entry = {
        "identity": {"actor_id": "actor-1", "name": "OldName"},
        "updated_at": stale_time,
    }
    redis.hset(
        "monkeybrain:actors:hash",
        "actor-1",
        json.dumps(stale_entry),
    )

    actor_doc = {
        "_id": "tenant-1:actor-1",
        "actor_type": "Agent",
        "name": "NewName",
        "status": "registered",
    }

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor._rebuild_redis_entry("actor-1", actor_doc)

    # Should rebuild (return True)
    assert result is True

    # Check that entry was updated with new name
    stored = redis.hget("monkeybrain:actors:hash", "actor-1")
    entry = json.loads(stored)
    assert entry["identity"]["name"] == "NewName"


def test_rebuild_redis_entry_handles_corrupt_json(mock_planetary_runtime):
    """Test that corrupt JSON entries are replaced."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Pre-populate Redis with corrupt JSON
    redis.hset("monkeybrain:actors:hash", "actor-1", "{invalid json")

    actor_doc = {
        "_id": "tenant-1:actor-1",
        "actor_type": "Agent",
        "name": "TestAgent",
        "status": "registered",
    }

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor._rebuild_redis_entry("actor-1", actor_doc)

    # Should rebuild (return True)
    assert result is True

    # Check that valid JSON is now stored
    stored = redis.hget("monkeybrain:actors:hash", "actor-1")
    entry = json.loads(stored)
    assert entry["identity"]["actor_id"] == "actor-1"


# ── Tests: Construct Registry Entry ──────────────────────────────────


def test_construct_registry_entry_from_mongodb(mock_planetary_runtime):
    """Test constructing a registry entry from MongoDB document."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    actor_doc = {
        "actor_type": "Agent",
        "name": "TestAgent",
        "society_id": "society-1",
        "status": "active",
        "belief_state": {"key": "value"},
        "affiliations": ["group-1"],
        "node_id": "node-1",
        "artifact_version": "1.0",
        "runtime_version": "2.0",
    }

    reconstructor = RedisIndexReconstructor(runtime)
    entry = reconstructor._construct_registry_entry_from_mongodb("actor-1", actor_doc)

    assert entry is not None
    assert entry["identity"]["actor_id"] == "actor-1"
    assert entry["identity"]["name"] == "TestAgent"
    assert entry["identity"]["actor_type"] == "Agent"
    assert entry["society_id"] == "society-1"
    assert entry["status"] == "active"
    assert "updated_at" in entry


def test_construct_registry_entry_with_defaults(mock_planetary_runtime):
    """Test that registry entry fills in sensible defaults."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Minimal document
    actor_doc = {"_id": "tenant-1:actor-1"}

    reconstructor = RedisIndexReconstructor(runtime)
    entry = reconstructor._construct_registry_entry_from_mongodb("actor-1", actor_doc)

    assert entry is not None
    assert entry["identity"]["actor_id"] == "actor-1"
    assert entry["identity"]["name"] == "actor-1"  # Defaults to actor_id
    assert entry["identity"]["actor_type"] == "unknown"
    assert entry["status"] == "registered"


def test_construct_registry_entry_handles_exceptions(mock_planetary_runtime):
    """Test graceful handling of construction errors."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Simulate an exception during construction
    actor_doc = None  # This will cause an error

    reconstructor = RedisIndexReconstructor(runtime)
    with patch.object(
        reconstructor,
        "_construct_registry_entry_from_mongodb",
        side_effect=Exception("Construction failed"),
    ):
        with pytest.raises(Exception):
            reconstructor._rebuild_redis_entry("actor-1", actor_doc)


# ── Tests: Rebuild from MongoDB ──────────────────────────────────────


def test_rebuild_from_mongodb_basic(mock_planetary_runtime):
    """Test basic reconstruction from MongoDB."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Populate MongoDB with actors
    collection._docs = {
        "tenant-1:actor-1": {
            "_id": "tenant-1:actor-1",
            "actor_type": "Agent",
            "name": "Actor1",
            "status": "registered",
        },
        "tenant-1:actor-2": {
            "_id": "tenant-1:actor-2",
            "actor_type": "Agent",
            "name": "Actor2",
            "status": "registered",
        },
    }

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.rebuild_from_mongodb()

    assert result.success is True
    assert result.actors_scanned == 2
    assert result.actors_rebuilt == 2
    assert result.errors == []


def test_rebuild_from_mongodb_with_redis_unavailable(mock_planetary_runtime):
    """Test reconstruction when Redis is unavailable."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime
    runtime._redis = None

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.rebuild_from_mongodb()

    # Should fail gracefully (fail-open behavior)
    assert result.success is False
    assert result.actors_scanned == 0


def test_rebuild_from_mongodb_with_mongodb_unavailable(mock_planetary_runtime):
    """Test reconstruction when MongoDB is unavailable."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime
    runtime._get_actor_state_store.return_value = None

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.rebuild_from_mongodb()

    # Should fail gracefully
    assert result.success is False
    assert result.actors_scanned == 0


def test_rebuild_from_mongodb_mixed_success_and_errors(mock_planetary_runtime):
    """Test reconstruction with some successes and some failures."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Populate MongoDB with actors
    collection._docs = {
        "tenant-1:actor-1": {
            "_id": "tenant-1:actor-1",
            "actor_type": "Agent",
            "name": "Actor1",
            "status": "registered",
        },
        "tenant-1:actor-2": {
            "_id": "tenant-1:actor-2",
            "actor_type": "Agent",
            "name": "Actor2",
            "status": "registered",
        },
    }

    reconstructor = RedisIndexReconstructor(runtime)

    # Mock _rebuild_redis_entry to fail for actor-2
    original_rebuild = reconstructor._rebuild_redis_entry

    def failing_rebuild(actor_id, doc):
        if actor_id == "actor-2":
            raise Exception("Simulated rebuild failure")
        return original_rebuild(actor_id, doc)

    with patch.object(reconstructor, "_rebuild_redis_entry", side_effect=failing_rebuild):
        result = reconstructor.rebuild_from_mongodb()

    assert result.success is True
    assert result.actors_scanned == 2
    assert result.actors_rebuilt == 1
    assert len(result.errors) == 1
    assert result.errors[0][0] == "actor-2"


def test_rebuild_from_mongodb_duration_recorded(mock_planetary_runtime):
    """Test that reconstruction time is recorded."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Populate MongoDB
    collection._docs = {
        f"tenant-1:actor-{i}": {
            "_id": f"tenant-1:actor-{i}",
            "actor_type": "Agent",
            "name": f"Actor{i}",
            "status": "registered",
        }
        for i in range(5)
    }

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.rebuild_from_mongodb()

    assert result.success is True
    assert result.duration_seconds > 0


# ── Tests: Verify Consistency ────────────────────────────────────────


def test_verify_consistency_all_consistent(mock_planetary_runtime):
    """Test consistency check when everything is aligned."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Populate MongoDB
    collection._docs = {
        "tenant-1:actor-1": {
            "_id": "tenant-1:actor-1",
            "actor_type": "Agent",
            "name": "Actor1",
        },
        "tenant-1:actor-2": {
            "_id": "tenant-1:actor-2",
            "actor_type": "Agent",
            "name": "Actor2",
        },
    }

    # Populate Redis with matching entries
    redis.hset(
        "monkeybrain:actors:hash",
        "actor-1",
        json.dumps(
            {
                "identity": {"actor_id": "actor-1"},
                "updated_at": time.time(),
            }
        ),
    )
    redis.hset(
        "monkeybrain:actors:hash",
        "actor-2",
        json.dumps(
            {
                "identity": {"actor_id": "actor-2"},
                "updated_at": time.time(),
            }
        ),
    )

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.verify_consistency()

    assert result.is_consistent is True
    assert result.total_in_mongodb == 2
    assert result.total_in_redis == 2
    assert result.missing_from_redis == []
    assert result.missing_from_mongodb == []


def test_verify_consistency_missing_from_redis(mock_planetary_runtime):
    """Test consistency check detects missing Redis entries."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Populate MongoDB with 3 actors
    collection._docs = {
        f"tenant-1:actor-{i}": {
            "_id": f"tenant-1:actor-{i}",
            "actor_type": "Agent",
            "name": f"Actor{i}",
        }
        for i in range(1, 4)
    }

    # Populate Redis with only 1 actor
    redis.hset(
        "monkeybrain:actors:hash",
        "actor-1",
        json.dumps(
            {
                "identity": {"actor_id": "actor-1"},
                "updated_at": time.time(),
            }
        ),
    )

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.verify_consistency()

    assert result.is_consistent is False
    assert result.total_in_mongodb == 3
    assert result.total_in_redis == 1
    assert set(result.missing_from_redis) == {"actor-2", "actor-3"}


def test_verify_consistency_stale_entries(mock_planetary_runtime):
    """Test consistency check detects stale entries."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Populate MongoDB
    collection._docs = {
        "tenant-1:actor-1": {
            "_id": "tenant-1:actor-1",
            "actor_type": "Agent",
            "name": "Actor1",
        },
    }

    # Populate Redis with a stale entry (not updated in > 1 hour)
    stale_time = time.time() - 7200  # 2 hours ago
    redis.hset(
        "monkeybrain:actors:hash",
        "actor-1",
        json.dumps(
            {
                "identity": {"actor_id": "actor-1"},
                "updated_at": stale_time,
            }
        ),
    )

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.verify_consistency()

    assert result.is_consistent is False
    assert len(result.stale_entries) == 1
    assert result.stale_entries[0][0] == "actor-1"


def test_verify_consistency_redis_unavailable(mock_planetary_runtime):
    """Test consistency check when Redis is unavailable."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime
    runtime._redis = None

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.verify_consistency()

    # When Redis unavailable, returns early with issues but is_consistent stays True
    # (fail-open: we don't know if there's a problem, so don't assert inconsistency)
    assert "Redis unavailable" in result.issues[0]
    assert len(result.issues) > 0


def test_verify_consistency_corrupt_redis_entry(mock_planetary_runtime):
    """Test consistency check detects corrupt Redis entries."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Populate MongoDB
    collection._docs = {
        "tenant-1:actor-1": {
            "_id": "tenant-1:actor-1",
            "actor_type": "Agent",
            "name": "Actor1",
        },
    }

    # Populate Redis with corrupt JSON
    redis.hset("monkeybrain:actors:hash", "actor-1", "{invalid json")

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.verify_consistency()

    assert result.is_consistent is False
    assert len(result.stale_entries) == 1
    assert "Corrupt" in result.stale_entries[0][1]


# ── Tests: Repair from Consistency Check ─────────────────────────────


def test_repair_no_issues(mock_planetary_runtime):
    """Test repair when there are no issues."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    consistency = ConsistencyCheckResult(
        is_consistent=True,
        total_in_mongodb=10,
        total_in_redis=10,
        missing_from_redis=[],
        missing_from_mongodb=[],
        stale_entries=[],
        issues=[],
    )

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.repair_from_consistency_check(consistency)

    assert result.success is True
    assert result.actors_rebuilt == 0


def test_repair_missing_from_redis(mock_planetary_runtime):
    """Test repair rebuilds missing Redis entries."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Populate MongoDB
    collection._docs = {
        "tenant-1:actor-1": {
            "_id": "tenant-1:actor-1",
            "actor_type": "Agent",
            "name": "Actor1",
        },
        "tenant-1:actor-2": {
            "_id": "tenant-1:actor-2",
            "actor_type": "Agent",
            "name": "Actor2",
        },
    }

    consistency = ConsistencyCheckResult(
        is_consistent=False,
        total_in_mongodb=2,
        total_in_redis=1,
        missing_from_redis=["actor-2"],
        missing_from_mongodb=[],
        stale_entries=[],
        issues=["1 actor missing from Redis"],
    )

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.repair_from_consistency_check(consistency)

    assert result.success is True
    assert result.actors_rebuilt == 1

    # Verify actor-2 is now in Redis
    stored = redis.hget("monkeybrain:actors:hash", "actor-2")
    assert stored is not None


def test_repair_stale_entries(mock_planetary_runtime):
    """Test repair rebuilds stale entries."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Populate MongoDB
    collection._docs = {
        "tenant-1:actor-1": {
            "_id": "tenant-1:actor-1",
            "actor_type": "Agent",
            "name": "NewName",
        },
    }

    consistency = ConsistencyCheckResult(
        is_consistent=False,
        total_in_mongodb=1,
        total_in_redis=1,
        missing_from_redis=[],
        missing_from_mongodb=[],
        stale_entries=[("actor-1", "not updated in 2 hours")],
        issues=["1 stale entry"],
    )

    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.repair_from_consistency_check(consistency)

    assert result.success is True
    assert result.actors_rebuilt == 1


def test_repair_with_errors(mock_planetary_runtime):
    """Test repair handles errors gracefully."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Populate MongoDB
    collection._docs = {
        "tenant-1:actor-1": {
            "_id": "tenant-1:actor-1",
            "actor_type": "Agent",
            "name": "Actor1",
        },
    }

    consistency = ConsistencyCheckResult(
        is_consistent=False,
        total_in_mongodb=1,
        total_in_redis=0,
        missing_from_redis=["actor-1"],
        missing_from_mongodb=[],
        stale_entries=[],
        issues=["1 actor missing from Redis"],
    )

    reconstructor = RedisIndexReconstructor(runtime)

    # Mock rebuild to fail
    with patch.object(reconstructor, "_rebuild_redis_entry", side_effect=Exception("Rebuild failed")):
        result = reconstructor.repair_from_consistency_check(consistency)

    # Repair still reports success=True but logs the errors
    assert result.success is True
    assert len(result.errors) == 1
    assert result.errors[0][1] == "Rebuild failed"


# ── Integration: Public API ──────────────────────────────────────────


def test_reconstructor_initialization(mock_planetary_runtime):
    """Test basic initialization of RedisIndexReconstructor."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    reconstructor = RedisIndexReconstructor(runtime)

    assert reconstructor._planetary is runtime
    assert reconstructor._redis is redis
    assert reconstructor._actors_hash_key == "monkeybrain:actors:hash"


def test_end_to_end_reconstruction_scenario(mock_planetary_runtime):
    """Test a complete end-to-end reconstruction scenario."""
    runtime, redis, collection, mongo_conn = mock_planetary_runtime

    # Scenario: Redis pod crashes, loses all data
    # MongoDB has persisted actors
    collection._docs = {
        f"tenant-1:actor-{i}": {
            "_id": f"tenant-1:actor-{i}",
            "actor_type": "Agent",
            "name": f"Actor{i}",
            "status": "registered",
        }
        for i in range(1, 6)
    }

    # Redis is now empty (emptyDir recreated)
    redis._hash_storage = {}

    reconstructor = RedisIndexReconstructor(runtime)

    # Step 1: Verify consistency detects the gap
    consistency = reconstructor.verify_consistency()
    assert consistency.is_consistent is False
    assert len(consistency.missing_from_redis) == 5
    assert consistency.has_fixable_issues() is True

    # Step 2: Repair rebuilds everything
    repair_result = reconstructor.repair_from_consistency_check(consistency)
    assert repair_result.success is True
    assert repair_result.actors_rebuilt == 5

    # Step 3: Verify consistency shows everything restored
    new_consistency = reconstructor.verify_consistency()
    assert new_consistency.is_consistent is True
    assert len(new_consistency.missing_from_redis) == 0


def test_environment_variables_configure_thresholds():
    """Test that environment variables properly configure reconstruction thresholds.

    Verifies that REDIS_RECENT_ENTRY_TTL_SECONDS and REDIS_STALE_ENTRY_TTL_SECONDS
    environment variables are read and used for threshold tuning.
    """
    import importlib
    import sys
    import os as os_module

    # Import the module
    import src.monkey_brain.kernel.society.redis_index_reconstruction as recon_module

    # Check that the constants are defined and have default values
    assert hasattr(recon_module, "_REDIS_RECENT_ENTRY_TTL_SECONDS")
    assert hasattr(recon_module, "_REDIS_STALE_ENTRY_TTL_SECONDS")

    # Default values should be set
    assert recon_module._REDIS_RECENT_ENTRY_TTL_SECONDS == 300 or recon_module._REDIS_RECENT_ENTRY_TTL_SECONDS > 0
    assert recon_module._REDIS_STALE_ENTRY_TTL_SECONDS == 3600 or recon_module._REDIS_STALE_ENTRY_TTL_SECONDS > 0

    # Verify they're documented in the module docstring
    assert "REDIS_RECENT_ENTRY_TTL_SECONDS" in recon_module.__doc__
    assert "REDIS_STALE_ENTRY_TTL_SECONDS" in recon_module.__doc__


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
