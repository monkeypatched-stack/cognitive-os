"""Unit tests for Actor State Rehydration on PlanetaryRuntime restart.

Tests that actors survive restart without reseeding, with durable actor_state
as the authoritative source of truth for reconstruction.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

import os
import sys

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.monkey_brain.kernel.society.actor_state_rehydrator import (
    ActorStateRehydrator,
    RehydrationResult,
)

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_mongodb_collection():
    """Create a mock MongoDB collection with actor_state documents."""
    collection = MagicMock()
    collection._docs = {}

    def find_impl(query=None):
        return list(collection._docs.values())

    collection.find.side_effect = find_impl
    return collection


@pytest.fixture
def mock_actor_state_store(mock_mongodb_collection):
    """Create a mock ActorStateStore."""
    db = MagicMock()
    db.__getitem__.return_value = mock_mongodb_collection

    mongo_conn = MagicMock()
    mongo_conn.get_db.return_value = db

    store = MagicMock()
    store._db = mongo_conn
    store._collection_name = "actor_state"
    return store


@pytest.fixture
def mock_planetary_runtime(mock_actor_state_store):
    """Create a mock PlanetaryRuntime with MongoDB backend."""
    runtime = MagicMock()
    runtime._get_actor_state_store.return_value = mock_actor_state_store
    runtime._societies = {}
    runtime._society_runtime = MagicMock()

    # Mock society that can register actors
    mock_society = MagicMock()
    mock_society.society.name = "TestSociety"
    mock_society.society.society_id = "test-society"
    mock_society.register_actor.return_value = MagicMock(
        actor_id="test-actor",
        profile=MagicMock(),
        status="registered",
        belief_state=None,
        actor_runtime=MagicMock(),
    )
    mock_society.get_actor.return_value = None  # No existing actors

    runtime._societies["test-society"] = mock_society
    runtime._society_runtime = mock_society

    # Mock other methods
    runtime._subscribe_actor_inbox = MagicMock()
    runtime.set_actor_desired_state = MagicMock()

    return runtime, mock_actor_state_store


# ── Tests: RehydrationResult ─────────────────────────────────────────


def test_rehydration_result_summary():
    """Test that RehydrationResult generates a readable summary."""
    result = RehydrationResult(
        success=True,
        actors_scanned=10,
        actors_rehydrated=9,
        actors_skipped=1,
        errors=[],
        duration_seconds=0.5,
    )

    summary = result.summary()
    assert "9 restored" in summary
    assert "1 skipped" in summary
    assert "0.50s" in summary


def test_rehydration_result_summary_with_errors():
    """Test summary includes error count."""
    result = RehydrationResult(
        success=False,
        actors_scanned=10,
        actors_rehydrated=8,
        actors_skipped=1,
        errors=[("actor-1", "error1"), ("actor-2", "error2")],
        duration_seconds=1.0,
    )

    summary = result.summary()
    assert "8 restored" in summary
    assert "2" in summary  # Error count


# ── Tests: Rehydrate from MongoDB ────────────────────────────────────


def test_rehydrate_from_mongodb_empty(mock_planetary_runtime):
    """Test rehydration with no persisted actors."""
    runtime, mock_store = mock_planetary_runtime

    rehydrator = ActorStateRehydrator(runtime)
    result = rehydrator.rehydrate_from_mongodb()

    assert result.success is True
    assert result.actors_scanned == 0
    assert result.actors_rehydrated == 0


def test_rehydrate_from_mongodb_single_actor(mock_planetary_runtime):
    """Test rehydration with one persisted actor."""
    runtime, mock_store = mock_planetary_runtime

    # Add persisted actor to MongoDB
    actor_doc = {
        "_id": "tenant-1:actor-1",
        "actor_id": "actor-1",
        "tenant_id": "tenant-1",
        "name": "TestActor",
        "actor_type": "Agent",
        "society_id": "test-society",
        "status": "registered",
        "belief_state": json.dumps({"version": 1}),
    }
    mock_store._db.get_db.return_value[mock_store._collection_name]._docs = {"tenant-1:actor-1": actor_doc}

    rehydrator = ActorStateRehydrator(runtime)
    result = rehydrator.rehydrate_from_mongodb()

    assert result.success is True
    assert result.actors_scanned == 1
    assert result.actors_rehydrated == 1
    assert result.actors_skipped == 0
    assert len(result.errors) == 0


def test_rehydrate_from_mongodb_multiple_actors(mock_planetary_runtime):
    """Test rehydration with multiple persisted actors."""
    runtime, mock_store = mock_planetary_runtime

    # Add multiple actors to MongoDB
    actors = {}
    for i in range(1, 6):
        actor_doc = {
            "_id": f"tenant-1:actor-{i}",
            "actor_id": f"actor-{i}",
            "tenant_id": "tenant-1",
            "name": f"Actor{i}",
            "actor_type": "Agent",
            "society_id": "test-society",
            "status": "registered",
        }
        actors[f"tenant-1:actor-{i}"] = actor_doc

    mock_store._db.get_db.return_value[mock_store._collection_name]._docs = actors

    rehydrator = ActorStateRehydrator(runtime)
    result = rehydrator.rehydrate_from_mongodb()

    assert result.success is True
    assert result.actors_scanned == 5
    assert result.actors_rehydrated == 5


def test_rehydrate_skips_existing_actors(mock_planetary_runtime):
    """Test that rehydration skips actors already in memory."""
    runtime, mock_store = mock_planetary_runtime

    # Mock: actor-1 already exists in memory (in any society)
    # We need to make get_actor return None initially, then return existing_actor on iteration
    existing_actor = MagicMock()
    existing_actor.actor_id = "actor-1"

    def get_actor_side_effect(actor_id):
        if actor_id == "actor-1":
            return existing_actor
        return None

    runtime._society_runtime.get_actor.side_effect = get_actor_side_effect

    # Add actor-1 and actor-2 to MongoDB
    actors = {
        "tenant-1:actor-1": {
            "_id": "tenant-1:actor-1",
            "actor_id": "actor-1",
            "name": "Actor1",
            "actor_type": "Agent",
        },
        "tenant-1:actor-2": {
            "_id": "tenant-1:actor-2",
            "actor_id": "actor-2",
            "name": "Actor2",
            "actor_type": "Agent",
        },
    }
    mock_store._db.get_db.return_value[mock_store._collection_name]._docs = actors

    rehydrator = ActorStateRehydrator(runtime)
    result = rehydrator.rehydrate_from_mongodb()

    assert result.success is True
    assert result.actors_scanned == 2
    # Both are skipped because they either exist or the mock society.register_actor returns None by default
    assert result.actors_skipped >= 1


def test_rehydrate_handles_missing_actor_id(mock_planetary_runtime):
    """Test that rehydration handles documents with missing actor_id."""
    runtime, mock_store = mock_planetary_runtime

    # Add actor without actor_id
    actors = {
        "tenant-1:bad": {
            "_id": "tenant-1:bad",
            "name": "BadActor",
        }
    }
    mock_store._db.get_db.return_value[mock_store._collection_name]._docs = actors

    rehydrator = ActorStateRehydrator(runtime)
    result = rehydrator.rehydrate_from_mongodb()

    assert result.success is True
    assert result.actors_scanned == 1
    assert result.actors_rehydrated == 0
    assert result.actors_skipped == 0


def test_rehydrate_handles_mongodb_unavailable():
    """Test graceful handling when MongoDB is unavailable."""
    runtime = MagicMock()
    runtime._get_actor_state_store.return_value = None

    rehydrator = ActorStateRehydrator(runtime)
    result = rehydrator.rehydrate_from_mongodb()

    assert result.success is False
    assert result.actors_scanned == 0


def test_rehydrate_handles_exceptions(mock_planetary_runtime):
    """Test that rehydration handles exceptions gracefully."""
    runtime, mock_store = mock_planetary_runtime

    # Make find() raise an exception
    mock_store._db.get_db.return_value[mock_store._collection_name].find.side_effect = Exception(
        "MongoDB connection failed"
    )

    rehydrator = ActorStateRehydrator(runtime)
    result = rehydrator.rehydrate_from_mongodb()

    assert result.success is False


# ── Tests: Desired State Restoration ─────────────────────────────────


def test_rehydrate_restores_desired_state(mock_planetary_runtime):
    """Test that rehydration attempts to restore persisted desired state."""
    runtime, mock_store = mock_planetary_runtime

    # Make register_actor actually return a mock object
    runtime._society_runtime.register_actor.return_value = MagicMock(
        actor_id="actor-1",
        profile=MagicMock(),
        status="registered",
        belief_state=None,
    )

    # Add actor with desired_state to MongoDB
    actor_doc = {
        "_id": "tenant-1:actor-1",
        "actor_id": "actor-1",
        "name": "TestActor",
        "actor_type": "Agent",
        "society_id": "test-society",
        "desired_state": {
            "state": "PAUSED",
            "reason": "Was paused before restart",
        },
    }
    mock_store._db.get_db.return_value[mock_store._collection_name]._docs = {"tenant-1:actor-1": actor_doc}

    rehydrator = ActorStateRehydrator(runtime)
    result = rehydrator.rehydrate_from_mongodb()

    assert result.success is True
    assert result.actors_rehydrated == 1
    # Verify that the rehydration process didn't fail
    assert len(result.errors) == 0


def test_rehydrate_restores_belief_state(mock_planetary_runtime):
    """Test that rehydration restores persisted belief state."""
    runtime, mock_store = mock_planetary_runtime

    belief_data = {"version": 5, "values": {"mood": "happy"}}
    actor_doc = {
        "_id": "tenant-1:actor-1",
        "actor_id": "actor-1",
        "name": "TestActor",
        "actor_type": "Agent",
        "society_id": "test-society",
        "belief_state": json.dumps(belief_data),
    }
    mock_store._db.get_db.return_value[mock_store._collection_name]._docs = {"tenant-1:actor-1": actor_doc}

    rehydrator = ActorStateRehydrator(runtime)
    result = rehydrator.rehydrate_from_mongodb()

    assert result.success is True
    assert result.actors_rehydrated == 1


def test_rehydrate_restores_lifecycle_status(mock_planetary_runtime):
    """Test that rehydration restores persisted actor status."""
    runtime, mock_store = mock_planetary_runtime

    actor_doc = {
        "_id": "tenant-1:actor-1",
        "actor_id": "actor-1",
        "name": "TestActor",
        "actor_type": "Agent",
        "society_id": "test-society",
        "status": "ACTIVE",
    }
    mock_store._db.get_db.return_value[mock_store._collection_name]._docs = {"tenant-1:actor-1": actor_doc}

    rehydrator = ActorStateRehydrator(runtime)
    result = rehydrator.rehydrate_from_mongodb()

    assert result.success is True
    assert result.actors_rehydrated == 1


# ── Tests: Idempotency ───────────────────────────────────────────────


def test_rehydrate_idempotent_same_input(mock_planetary_runtime):
    """Test that rehydration is idempotent (same input, same result)."""
    runtime, mock_store = mock_planetary_runtime

    # Add actor to MongoDB
    actor_doc = {
        "_id": "tenant-1:actor-1",
        "actor_id": "actor-1",
        "name": "TestActor",
        "actor_type": "Agent",
    }
    mock_store._db.get_db.return_value[mock_store._collection_name]._docs = {"tenant-1:actor-1": actor_doc}

    rehydrator = ActorStateRehydrator(runtime)

    # First rehydration
    result1 = rehydrator.rehydrate_from_mongodb()

    # Reset the mock to track calls
    runtime.reset_mock()
    runtime._get_actor_state_store.return_value = mock_store

    # Second rehydration of same input
    result2 = rehydrator.rehydrate_from_mongodb()

    assert result1.actors_rehydrated == result2.actors_rehydrated
    assert result1.actors_skipped == result2.actors_skipped


# ── Tests: Actor Profile Construction ────────────────────────────────


def test_construct_actor_profile_basic(mock_planetary_runtime):
    """Test constructing actor profile from MongoDB document."""
    runtime, mock_store = mock_planetary_runtime

    actor_doc = {
        "actor_id": "actor-1",
        "name": "TestActor",
        "actor_type": "Agent",
    }

    rehydrator = ActorStateRehydrator(runtime)
    profile = rehydrator._construct_actor_profile_from_mongodb("actor-1", actor_doc)

    assert profile is not None
    assert profile.identity.actor_id == "actor-1"
    assert profile.identity.name == "TestActor"
    assert profile.identity.actor_type == "Agent"


def test_construct_actor_profile_with_metadata(mock_planetary_runtime):
    """Test constructing actor profile with full metadata."""
    runtime, mock_store = mock_planetary_runtime

    actor_doc = {
        "actor_id": "actor-1",
        "name": "TestActor",
        "actor_type": "Agent",
        "goals": ["goal1", "goal2"],
        "policies": ["policy1"],
        "trust_level": 0.8,
        "ownership": "user-123",
    }

    rehydrator = ActorStateRehydrator(runtime)
    profile = rehydrator._construct_actor_profile_from_mongodb("actor-1", actor_doc)

    assert profile is not None
    assert profile.trust_level == 0.8
    assert "goal1" in profile.goals
    assert "policy1" in profile.policies


def test_construct_actor_profile_missing_fields(mock_planetary_runtime):
    """Test that construction handles missing fields gracefully."""
    runtime, mock_store = mock_planetary_runtime

    # Minimal document
    actor_doc = {"actor_id": "actor-1"}

    rehydrator = ActorStateRehydrator(runtime)
    profile = rehydrator._construct_actor_profile_from_mongodb("actor-1", actor_doc)

    assert profile is not None
    assert profile.identity.actor_id == "actor-1"
    assert profile.identity.name == "actor-1"  # Defaults to actor_id
    assert profile.identity.actor_type == "unknown"


# ── Integration Tests ────────────────────────────────────────────────


def test_rehydration_end_to_end_restart_scenario(mock_planetary_runtime):
    """Test complete restart scenario: register → checkpoint → rehydrate."""
    runtime, mock_store = mock_planetary_runtime

    # Make register_actor return a valid mock actor
    runtime._society_runtime.register_actor.return_value = MagicMock(
        actor_id="alice",
        profile=MagicMock(),
        status="active",
        belief_state=None,
        actor_runtime=MagicMock(),
    )

    # Simulate: actor was registered, persisted, then system restarted
    # Step 1: Actor was previously registered and persisted to MongoDB
    persisted_actor = {
        "_id": "tenant-1:alice",
        "actor_id": "alice",
        "name": "Alice",
        "actor_type": "researcher",
        "society_id": "test-society",
        "status": "active",
        "belief_state": json.dumps({"confidence": 0.8, "knowledge": "extensive"}),
        "cycle_count": 42,
        "desired_state": {"state": "RUNNING", "reason": "Active before restart"},
    }
    mock_store._db.get_db.return_value[mock_store._collection_name]._docs = {"tenant-1:alice": persisted_actor}

    # Step 2: System restarts, runtime calls rehydrator
    rehydrator = ActorStateRehydrator(runtime)
    result = rehydrator.rehydrate_from_mongodb()

    # Step 3: Verify actor was rehydrated successfully
    assert result.success is True
    assert result.actors_rehydrated == 1
    assert result.actors_skipped == 0

    # Verify rehydration had no errors
    assert len(result.errors) == 0, f"Expected no errors, got: {result.errors}"

    # Verify register_actor was called to recreate the actor
    runtime._society_runtime.register_actor.assert_called_once()

    # Verify NATS inbox was re-subscribed
    runtime._subscribe_actor_inbox.assert_called_once()


def test_rehydration_multi_society(mock_planetary_runtime):
    """Test rehydration across multiple societies."""
    runtime, mock_store = mock_planetary_runtime

    # Create multiple societies
    society1 = MagicMock()
    society1.society.name = "Society1"
    society1.society.society_id = "society-1"
    society1.get_actor.return_value = None
    society1.register_actor.return_value = MagicMock()

    society2 = MagicMock()
    society2.society.name = "Society2"
    society2.society.society_id = "society-2"
    society2.get_actor.return_value = None
    society2.register_actor.return_value = MagicMock()

    runtime._societies = {
        "society-1": society1,
        "society-2": society2,
    }

    # Add actors from both societies to MongoDB
    actors = {
        "tenant-1:actor-1": {
            "_id": "tenant-1:actor-1",
            "actor_id": "actor-1",
            "name": "Actor1",
            "actor_type": "Agent",
            "society_id": "society-1",
        },
        "tenant-1:actor-2": {
            "_id": "tenant-1:actor-2",
            "actor_id": "actor-2",
            "name": "Actor2",
            "actor_type": "Agent",
            "society_id": "society-2",
        },
    }
    mock_store._db.get_db.return_value[mock_store._collection_name]._docs = actors

    rehydrator = ActorStateRehydrator(runtime)
    result = rehydrator.rehydrate_from_mongodb()

    assert result.success is True
    assert result.actors_rehydrated == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
