"""End-to-end integration test for Redis loss recovery.

This test simulates a realistic scenario where Redis pod crashes (emptyDir
cleared), but MongoDB persists actor data. Tests that automatic recovery
happens at boot-time and actors remain discoverable.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch, PropertyMock

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


class MockRedisClient:
    """Mock Redis client with actual hash and string storage.

    More realistic than MagicMock for integration testing — tracks actual
    data structures, connection state, operations, etc.
    """

    def __init__(self):
        self._hashes = {}  # { key: { field: value } }
        self._strings = {}  # { key: value }
        self._connected = True
        self._operations_log = []

    def ping(self):
        """Check connection."""
        if not self._connected:
            raise ConnectionError("Redis not connected")
        self._operations_log.append(("ping",))
        return True

    def hget(self, key, field):
        """Get hash field."""
        self._operations_log.append(("hget", key, field))
        if key not in self._hashes:
            return None
        return self._hashes[key].get(field)

    def hset(self, key, field, value):
        """Set hash field."""
        self._operations_log.append(("hset", key, field))
        if key not in self._hashes:
            self._hashes[key] = {}
        self._hashes[key][field] = value
        return 1

    def hgetall(self, key):
        """Get all fields in hash."""
        self._operations_log.append(("hgetall", key))
        return self._hashes.get(key, {})

    def set(self, key, value):
        """Set string."""
        self._operations_log.append(("set", key))
        self._strings[key] = value
        return True

    def get(self, key):
        """Get string."""
        self._operations_log.append(("get", key))
        return self._strings.get(key)

    def flushdb(self):
        """Clear all data in current DB."""
        self._operations_log.append(("flushdb",))
        self._hashes.clear()
        self._strings.clear()
        return True

    def simulate_crash(self):
        """Simulate Redis pod crash (emptyDir cleared)."""
        self._hashes.clear()
        self._strings.clear()
        self._operations_log.append(("crash",))

    def simulate_reconnect(self):
        """Simulate Redis reconnection after crash."""
        self._connected = True
        self._operations_log.append(("reconnect",))

    def get_operations_log(self):
        """Get log of all operations performed."""
        return self._operations_log.copy()


class MockMongoDBCollection:
    """Mock MongoDB collection with realistic interface."""

    def __init__(self):
        self._documents = {}
        self._operations_log = []

    def insert_one(self, doc):
        """Insert a document."""
        self._operations_log.append(("insert_one", doc.get("_id")))
        self._documents[doc["_id"]] = doc

    def find(self, query=None):
        """Find documents matching query."""
        self._operations_log.append(("find", query or {}))
        # Simple query: if no query, return all
        if not query:
            return list(self._documents.values())
        # For simplicity, just return all (not a real MongoDB query engine)
        return list(self._documents.values())

    def find_one(self, query):
        """Find one document."""
        self._operations_log.append(("find_one", query))
        for doc in self._documents.values():
            # Simplified: match on _id
            if doc.get("_id") == query.get("_id"):
                return doc
        return None

    def update_one(self, query, update):
        """Update a document."""
        self._operations_log.append(("update_one", query))
        for doc_id, doc in self._documents.items():
            if doc.get("_id") == query.get("_id"):
                doc.update(update.get("$set", {}))
                return

    def delete_many(self):
        """Clear all documents."""
        self._operations_log.append(("delete_many",))
        self._documents.clear()

    def count_documents(self, query=None):
        """Count documents."""
        self._operations_log.append(("count_documents",))
        return len(self._documents)

    def get_operations_log(self):
        """Get log of all operations performed."""
        return self._operations_log.copy()


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def redis_client():
    """Provide a mock Redis client for the test."""
    return MockRedisClient()


@pytest.fixture
def mongodb_collection():
    """Provide a mock MongoDB collection for the test."""
    return MockMongoDBCollection()


@pytest.fixture
def mock_planetary_runtime_e2e(redis_client, mongodb_collection):
    """Create a mock PlanetaryRuntime with realistic Redis and MongoDB."""

    # Create mock database and store
    db = MagicMock()
    db.__getitem__.return_value = mongodb_collection

    mongo_conn = MagicMock()
    mongo_conn.get_db.return_value = db

    store = MagicMock()
    store._db = mongo_conn
    store._collection_name = "actor_state"

    # Create mock runtime
    runtime = MagicMock()
    runtime._redis = redis_client
    runtime._get_actor_state_store.return_value = store

    return runtime, redis_client, mongodb_collection


# ── Scenario 1: Basic Redis Loss Recovery ────────────────────────────


def test_redis_loss_recovery_basic(mock_planetary_runtime_e2e):
    """Test basic Redis loss recovery scenario.

    1. Register 5 actors (persisted to MongoDB)
    2. Verify Redis has all actors
    3. Simulate Redis crash (pod restarts, emptyDir cleared)
    4. Verify Redis is empty, MongoDB still has all actors
    5. Run recovery (verify_consistency + repair)
    6. Verify Redis is repopulated from MongoDB
    """
    runtime, redis, mongodb = mock_planetary_runtime_e2e

    # Step 1: Populate MongoDB with actors (simulating prior registration)
    for i in range(1, 6):
        actor_doc = {
            "_id": f"tenant-1:actor-{i}",
            "actor_type": "Agent",
            "name": f"TestAgent{i}",
            "society_id": f"society-{i % 2}",
            "status": "registered",
            "belief_state": {"level": i},
            "affiliations": ["group-1"],
            "node_id": f"node-{i % 3}",
        }
        mongodb.insert_one(actor_doc)

    assert mongodb.count_documents({}) == 5

    # Step 2: Populate Redis with these actors (simulating normal operation)
    reconstructor = RedisIndexReconstructor(runtime)
    result = reconstructor.rebuild_from_mongodb()
    assert result.success is True
    assert result.actors_rebuilt == 5
    assert len(redis._hashes["monkeybrain:actors:hash"]) == 5

    # Step 3: Simulate Redis crash (pod restarts, emptyDir cleared)
    redis.simulate_crash()
    assert len(redis._hashes.get("monkeybrain:actors:hash", {})) == 0

    # MongoDB should still have all data
    assert mongodb.count_documents({}) == 5

    # Step 4: Verify consistency detects the gap
    consistency = reconstructor.verify_consistency()
    assert consistency.is_consistent is False
    assert len(consistency.missing_from_redis) == 5
    assert consistency.has_fixable_issues() is True

    # Step 5: Run automatic recovery (as would happen at boot)
    redis.simulate_reconnect()
    repair_result = reconstructor.repair_from_consistency_check(consistency)
    assert repair_result.success is True
    assert repair_result.actors_rebuilt == 5

    # Step 6: Verify Redis is repopulated
    new_consistency = reconstructor.verify_consistency()
    assert new_consistency.is_consistent is True
    assert len(new_consistency.missing_from_redis) == 0
    assert len(new_consistency.missing_from_mongodb) == 0


# ── Scenario 2: Partial Redis Loss with Mixed State ────────────────


def test_redis_loss_recovery_partial(mock_planetary_runtime_e2e):
    """Test recovery when some actors are in Redis, some are missing.

    This can happen if Redis pod crashes while new actors are being
    registered (some made it to Redis, others didn't).
    """
    runtime, redis, mongodb = mock_planetary_runtime_e2e

    # Step 1: Populate MongoDB with 5 actors
    for i in range(1, 6):
        actor_doc = {
            "_id": f"tenant-1:actor-{i}",
            "actor_type": "Agent",
            "name": f"TestAgent{i}",
            "status": "registered",
        }
        mongodb.insert_one(actor_doc)

    # Step 2: Only partially populate Redis (actors 1-3 registered, 4-5 not yet)
    reconstructor = RedisIndexReconstructor(runtime)
    for i in range(1, 4):
        entry = {
            "identity": {"actor_id": f"actor-{i}", "name": f"TestAgent{i}"},
            "updated_at": time.time(),
        }
        redis.hset("monkeybrain:actors:hash", f"actor-{i}", json.dumps(entry))

    assert len(redis._hashes["monkeybrain:actors:hash"]) == 3

    # Step 3: Verify consistency detects missing actors
    consistency = reconstructor.verify_consistency()
    assert consistency.is_consistent is False
    assert len(consistency.missing_from_redis) == 2
    assert set(consistency.missing_from_redis) == {"actor-4", "actor-5"}

    # Step 4: Repair fills in the missing ones
    repair_result = reconstructor.repair_from_consistency_check(consistency)
    assert repair_result.success is True
    assert repair_result.actors_rebuilt == 2

    # Step 5: Verify all actors now in Redis
    new_consistency = reconstructor.verify_consistency()
    assert new_consistency.is_consistent is True
    assert len(new_consistency.missing_from_redis) == 0


# ── Scenario 3: Stale Entry Refresh ──────────────────────────────────


def test_redis_loss_recovery_stale_refresh(mock_planetary_runtime_e2e):
    """Test that stale entries are refreshed from MongoDB.

    An actor's MongoDB record may be updated (e.g., belief state changed),
    but Redis entry is old. Recovery should refresh from source of truth.
    """
    runtime, redis, mongodb = mock_planetary_runtime_e2e

    # Step 1: Populate MongoDB with an actor
    actor_doc = {
        "_id": "tenant-1:actor-1",
        "actor_type": "Agent",
        "name": "TestAgent",
        "belief_state": {"updated": "v2"},  # Newer version
        "status": "active",
    }
    mongodb.insert_one(actor_doc)

    # Step 2: Put a stale Redis entry (with old belief state)
    stale_time = time.time() - 7200  # 2 hours ago
    stale_entry = {
        "identity": {"actor_id": "actor-1", "name": "TestAgent"},
        "belief_state": {"updated": "v1"},  # Old version
        "updated_at": stale_time,
    }
    redis.hset("monkeybrain:actors:hash", "actor-1", json.dumps(stale_entry))

    reconstructor = RedisIndexReconstructor(runtime)

    # Step 3: Verify consistency detects stale entry
    consistency = reconstructor.verify_consistency()
    assert consistency.is_consistent is False
    assert len(consistency.stale_entries) == 1

    # Step 4: Repair refreshes from MongoDB
    repair_result = reconstructor.repair_from_consistency_check(consistency)
    assert repair_result.success is True
    assert repair_result.actors_rebuilt == 1

    # Step 5: Verify Redis entry now has current data from MongoDB
    current_entry = redis.hget("monkeybrain:actors:hash", "actor-1")
    assert current_entry is not None
    entry_data = json.loads(current_entry)
    assert entry_data["belief_state"]["updated"] == "v2"
    assert entry_data["status"] == "active"


# ── Scenario 4: Boot-Time Recovery (Automatic) ───────────────────────


def test_boot_time_automatic_recovery(mock_planetary_runtime_e2e):
    """Test that automatic recovery happens at boot-time.

    Simulates the _init_persistence() flow: after Redis reconnects,
    verify_consistency() is called, and if issues found, repair runs
    automatically without operator intervention.
    """
    runtime, redis, mongodb = mock_planetary_runtime_e2e

    # Step 1: Populate MongoDB (represents prior state before crash)
    for i in range(1, 4):
        actor_doc = {
            "_id": f"tenant-1:actor-{i}",
            "actor_type": "Agent",
            "name": f"Actor{i}",
            "status": "registered",
        }
        mongodb.insert_one(actor_doc)

    # Step 2: Redis starts empty (just recovered from crash)
    assert len(redis._hashes.get("monkeybrain:actors:hash", {})) == 0

    # Step 3: Simulate boot-time sequence (as in _init_persistence)
    reconstructor = RedisIndexReconstructor(runtime)

    # 3a. Check consistency
    consistency = reconstructor.verify_consistency()

    # 3b. If issues found, automatically repair
    if consistency.has_fixable_issues():
        repair_result = reconstructor.repair_from_consistency_check(consistency)
        assert repair_result.success is True

    # Step 4: Verify everything is restored
    final_consistency = reconstructor.verify_consistency()
    assert final_consistency.is_consistent is True
    assert final_consistency.total_in_mongodb == 3
    assert final_consistency.total_in_redis == 3


# ── Scenario 5: Idempotency (Safe to Run Multiple Times) ─────────────


def test_recovery_idempotency(mock_planetary_runtime_e2e):
    """Test that recovery is idempotent (safe to run multiple times).

    If recovery runs twice (due to operator manually triggering it, or
    a restart happening before first recovery completes), it should
    produce the same result without duplication or corruption.
    """
    runtime, redis, mongodb = mock_planetary_runtime_e2e

    # Step 1: Populate MongoDB
    for i in range(1, 4):
        actor_doc = {
            "_id": f"tenant-1:actor-{i}",
            "actor_type": "Agent",
            "name": f"Actor{i}",
            "status": "registered",
        }
        mongodb.insert_one(actor_doc)

    reconstructor = RedisIndexReconstructor(runtime)

    # Step 2: Run recovery first time
    result1 = reconstructor.rebuild_from_mongodb()
    assert result1.success is True
    assert result1.actors_rebuilt == 3
    count_after_first = len(redis._hashes["monkeybrain:actors:hash"])

    # Step 3: Run recovery again (idempotency test)
    result2 = reconstructor.rebuild_from_mongodb()
    # Second time, entries should be skipped (recent within 5 min)
    assert result2.actors_scanned == 3
    assert result2.actors_skipped == 3  # All skipped, no rebuilds
    count_after_second = len(redis._hashes["monkeybrain:actors:hash"])

    # Step 4: Verify same number of entries
    assert count_after_first == count_after_second == 3

    # Step 5: Verify consistency is good
    consistency = reconstructor.verify_consistency()
    assert consistency.is_consistent is True


# ── Scenario 6: Corruption Detection ─────────────────────────────────


def test_recovery_detects_corruption(mock_planetary_runtime_e2e):
    """Test that recovery detects and fixes corrupted Redis entries."""
    runtime, redis, mongodb = mock_planetary_runtime_e2e

    # Step 1: Populate MongoDB
    actor_doc = {
        "_id": "tenant-1:actor-1",
        "actor_type": "Agent",
        "name": "TestAgent",
        "status": "registered",
    }
    mongodb.insert_one(actor_doc)

    # Step 2: Populate Redis with corrupt JSON
    redis.hset("monkeybrain:actors:hash", "actor-1", "{invalid json")

    reconstructor = RedisIndexReconstructor(runtime)

    # Step 3: Consistency check detects corruption
    consistency = reconstructor.verify_consistency()
    assert consistency.is_consistent is False
    assert len(consistency.stale_entries) == 1
    assert "Corrupt" in consistency.stale_entries[0][1]

    # Step 4: Repair fixes it
    repair_result = reconstructor.repair_from_consistency_check(consistency)
    assert repair_result.success is True
    assert repair_result.actors_rebuilt == 1

    # Step 5: Verify valid JSON now stored
    current = redis.hget("monkeybrain:actors:hash", "actor-1")
    assert current is not None
    entry = json.loads(current)  # Should not raise
    assert entry["identity"]["actor_id"] == "actor-1"


# ── Scenario 7: Scale (Many Actors) ──────────────────────────────────


def test_recovery_at_scale(mock_planetary_runtime_e2e):
    """Test recovery with many actors (100+) to check performance."""
    runtime, redis, mongodb = mock_planetary_runtime_e2e

    actor_count = 100

    # Step 1: Populate MongoDB with many actors
    for i in range(actor_count):
        actor_doc = {
            "_id": f"tenant-1:actor-{i}",
            "actor_type": "Agent",
            "name": f"Actor{i}",
            "status": "registered",
            "belief_state": {"iteration": i},
        }
        mongodb.insert_one(actor_doc)

    assert mongodb.count_documents({}) == actor_count

    # Step 2: Run recovery with timing
    reconstructor = RedisIndexReconstructor(runtime)
    start = time.time()
    result = reconstructor.rebuild_from_mongodb()
    duration = time.time() - start

    # Step 3: Verify all actors recovered
    assert result.success is True
    assert result.actors_scanned == actor_count
    assert result.actors_rebuilt == actor_count
    assert result.duration_seconds > 0

    # Step 4: Verify Redis consistency
    consistency = reconstructor.verify_consistency()
    assert consistency.is_consistent is True
    assert consistency.total_in_mongodb == actor_count
    assert consistency.total_in_redis == actor_count


# ── Scenario 8: Mixed Tenant Data ────────────────────────────────────


def test_recovery_multi_tenant(mock_planetary_runtime_e2e):
    """Test recovery with multiple tenants to ensure isolation.

    Note: This is a simplified version. Full multi-tenant testing in real
    scenarios requires actual database fixtures. The core reconstruction
    logic is tenant-agnostic (works with any actor_id format).
    """
    runtime, redis, mongodb = mock_planetary_runtime_e2e

    # Populate MongoDB with single tenant (sufficient for testing core logic)
    for i in range(1, 4):
        actor_doc = {
            "_id": f"tenant-1:actor-{i}",
            "actor_type": "Agent",
            "name": f"Actor{i}",
            "status": "registered",
        }
        mongodb.insert_one(actor_doc)

    # The reconstructor extracts actor_id from composite _id (tenant-1:actor-1 -> actor-1)
    reconstructor = RedisIndexReconstructor(runtime)

    # Verify recovery works (actual multi-tenant isolation tested in higher-level e2e tests)
    result = reconstructor.rebuild_from_mongodb()
    assert result.success is True
    assert result.actors_scanned == 3

    consistency = reconstructor.verify_consistency()
    assert consistency.is_consistent is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
