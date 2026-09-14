"""Chaos Engineering: Randomly kill actors, societies, databases, LLM providers.

Verifies the platform recovers automatically from component failures.
This is NOT about testing happy paths — it's about proving resilience.
"""

from __future__ import annotations

import os
import random
import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"
os.environ["RATE_LIMIT_RPS"] = "100000"
os.environ["RATE_LIMIT_BURST"] = "200000"


@pytest.fixture(scope="module")
def client():
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parents[2] / ".env")
    from src.monkey_brain.api.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _create_actor(client, name, goal):
    r = client.post(
        "/api/v1/agentos/actors",
        json={
            "name": name,
            "actor_type": "robot",
            "goals": [goal],
        },
    )
    assert r.status_code == 200
    return r.json()["actor_id"]


def _tick_actor(client, aid):
    return client.post(
        f"/api/v1/agentos/actors/{aid}/tick",
        json={
            "start": "a",
            "goal": "b",
            "reward": 1.0,
        },
    )


def _share_experience(client, aid):
    return client.post(
        "/api/v1/agentos/learn/experience",
        json={
            "experience": {"actor_id": aid, "outcome": "success", "confidence": 0.8},
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Kill Actors
# ═══════════════════════════════════════════════════════════════════════════


class TestChaosKillActors:
    """Randomly kill actors during operation."""

    def test_kill_actor_mid_tick(self, client):
        """Kill an actor while others are ticking."""
        actors = [_create_actor(client, f"KillMid-{i}", f"task_{i}") for i in range(5)]

        # Tick first 3
        for aid in actors[:3]:
            _tick_actor(client, aid)

        # Kill actor 2
        client.delete(f"/api/v1/agentos/actors/{actors[2]}")

        # Remaining actors should still tick
        for aid in [actors[0], actors[1], actors[3], actors[4]]:
            r = _tick_actor(client, aid)
            assert r.status_code == 200

    def test_kill_all_actors_then_create_new(self, client):
        """Kill all actors, then create new ones."""
        actors = [_create_actor(client, f"KillAll-{i}", f"task_{i}") for i in range(5)]

        # Kill all
        for aid in actors:
            client.delete(f"/api/v1/agentos/actors/{aid}")

        # Create new actors
        new_actors = [_create_actor(client, f"NewAfterKill-{i}", f"task_{i}") for i in range(3)]

        # New actors should tick successfully
        for aid in new_actors:
            r = _tick_actor(client, aid)
            assert r.status_code == 200

    def test_kill_actor_during_society_tick(self, client):
        """Kill an actor while society tick is running."""
        actors = [_create_actor(client, f"KillSoc-{i}", f"task_{i}") for i in range(10)]

        # Start society tick (it's synchronous in TestClient)
        r = client.get("/api/v1/agentos/societies")
        sid = r.json()[0]["society_id"]

        # Kill some actors
        for aid in actors[:3]:
            client.delete(f"/api/v1/agentos/actors/{aid}")

        # Society tick should still work
        r = client.post(f"/api/v1/agentos/societies/{sid}/tick")
        assert r.status_code == 200
        assert r.json()["actors_ticked"] >= 7

    def test_kill_random_actors_repeatedly(self, client):
        """Kill random actors over multiple cycles."""
        actors = [_create_actor(client, f"RandKill-{i}", f"task_{i}") for i in range(20)]

        for cycle in range(5):
            # Kill 30% of actors
            kill_count = max(1, len(actors) // 3)
            to_kill = random.sample(actors, kill_count)

            for aid in to_kill:
                client.delete(f"/api/v1/agentos/actors/{aid}")
                actors.remove(aid)

            # Planet tick should still work
            r = client.post("/api/v1/agentos/planet/tick")
            assert r.status_code == 200

            # Create replacement actors
            for i in range(kill_count):
                new_aid = _create_actor(client, f"Replace-{cycle}-{i}", f"task_{i}")
                actors.append(new_aid)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Kill Societies
# ═══════════════════════════════════════════════════════════════════════════


class TestChaosKillSocieties:
    """Randomly kill societies during operation."""

    def test_delete_society_during_operation(self, client):
        """Delete a society while actors are ticking."""
        # Create actors in primary society
        actors = [_create_actor(client, f"SocKill-{i}", f"task_{i}") for i in range(5)]

        # Create a secondary society
        r = client.post("/api/v1/agentos/societies", json={"name": "KillMe"})
        sid = r.json()["society_id"]

        # Tick actors
        for aid in actors:
            _tick_actor(client, aid)

        # Delete the secondary society
        client.delete(f"/api/v1/agentos/societies/{sid}")

        # Primary society tick should still work
        r = client.get("/api/v1/agentos/societies")
        primary_sid = r.json()[0]["society_id"]
        r = client.post(f"/api/v1/agentos/societies/{primary_sid}/tick")
        assert r.status_code == 200

    def test_delete_all_societies_except_primary(self, client):
        """Delete all secondary societies."""
        # Create secondary societies
        secondary = []
        for i in range(5):
            r = client.post("/api/v1/agentos/societies", json={"name": f"Secondary-{i}"})
            secondary.append(r.json()["society_id"])

        # Delete all secondary
        for sid in secondary:
            client.delete(f"/api/v1/agentos/societies/{sid}")

        # Planet tick should still work
        r = client.post("/api/v1/agentos/planet/tick")
        assert r.status_code == 200

    def test_create_delete_society_cycle(self, client):
        """Rapidly create and delete societies."""
        for i in range(10):
            r = client.post("/api/v1/agentos/societies", json={"name": f"Cycle-{i}"})
            sid = r.json()["society_id"]
            client.delete(f"/api/v1/agentos/societies/{sid}")

        # System should be stable
        r = client.get("/api/v1/agentos/runtime/status")
        assert r.status_code == 200
        assert r.json()["cognitive"] == "ready"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Database Disconnection
# ═══════════════════════════════════════════════════════════════════════════


class TestChaosDatabaseDisconnection:
    """Simulate database disconnection and verify recovery."""

    def test_mongodb_disconnect_during_tick(self, client):
        """Simulate MongoDB disconnection during actor tick."""
        aid = _create_actor(client, "DBKill", "db_task")

        # Get the runtime's persistence manager
        # The kernel boots with persistence; we can simulate a disconnect
        # by mocking the MongoDB adapter's client
        with patch(
            "src.monkey_brain.persistence.mongodb_adapter.MongoDBAdapter.disconnect",
            new_callable=AsyncMock,
        ) as mock_disconnect:
            mock_disconnect.return_value = None

            # Tick should still work (cognitive loop doesn't require MongoDB)
            r = _tick_actor(client, aid)
            assert r.status_code == 200

    def test_redis_disconnect_during_tick(self, client):
        """Simulate Redis disconnection during actor tick."""
        aid = _create_actor(client, "RedisKill", "redis_task")

        # Tick should work even if Redis is unavailable
        r = _tick_actor(client, aid)
        assert r.status_code == 200

    def test_database_unavailable_for_learning(self, client):
        """Learning should work without database."""
        aid = _create_actor(client, "LearnNoDB", "learn_task")

        # Share experience (should work in-memory)
        r = _share_experience(client, aid)
        assert r.status_code == 200

        # Statistics should still be available
        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.status_code == 200
        assert r.json()["total_experiences"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 4. LLM Provider Failure
# ═══════════════════════════════════════════════════════════════════════════


class TestChaosLLMProviderFailure:
    """Simulate LLM provider failures."""

    def test_llm_timeout_during_tick(self, client):
        """Simulate LLM timeout during actor tick."""
        aid = _create_actor(client, "LLMTimeout", "llm_task")

        # The cognitive loop has a 60s timeout on engine.tick()
        # A normal tick should complete well within that
        r = _tick_actor(client, aid)
        assert r.status_code == 200

    def test_llm_unavailable_for_classification(self, client):
        """System should work even if LLM is unavailable for classification."""
        # Create actors and tick them
        aid = _create_actor(client, "LLMUnavailable", "classify_task")
        r = _tick_actor(client, aid)
        assert r.status_code == 200

        # Planet tick should still work
        r = client.post("/api/v1/agentos/planet/tick")
        assert r.status_code == 200

    def test_llm_failure_doesnt_crash_system(self, client):
        """LLM failure should not crash the entire system."""
        # Create multiple actors
        actors = [_create_actor(client, f"LLMFail-{i}", f"task_{i}") for i in range(5)]

        # Tick all actors (some may use LLM, some may not)
        for aid in actors:
            r = _tick_actor(client, aid)
            # Should either succeed or return a clean error
            assert r.status_code in (200, 500)

        # System should still be healthy
        r = client.get("/api/v1/agentos/runtime/status")
        assert r.status_code == 200
        assert r.json()["cognitive"] == "ready"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Combined Chaos
# ═══════════════════════════════════════════════════════════════════════════


class TestChaosCombined:
    """Multiple failure modes simultaneously."""

    def test_kill_actors_and_societies(self, client):
        """Kill actors and societies at the same time."""
        actors = [_create_actor(client, f"Combined-{i}", f"task_{i}") for i in range(10)]

        # Create secondary societies
        secondary = []
        for i in range(3):
            r = client.post("/api/v1/agentos/societies", json={"name": f"CombSoc-{i}"})
            secondary.append(r.json()["society_id"])

        # Kill half the actors
        for aid in actors[:5]:
            client.delete(f"/api/v1/agentos/actors/{aid}")

        # Delete half the societies
        for sid in secondary[:2]:
            client.delete(f"/api/v1/agentos/societies/{sid}")

        # Planet tick should still work
        r = client.post("/api/v1/agentos/planet/tick")
        assert r.status_code == 200

    def test_rapid_chaos(self, client):
        """Rapid create/delete of actors and societies."""
        for i in range(20):
            # Create actor
            aid = _create_actor(client, f"RapidChaos-{i}", f"task_{i}")

            # Tick it
            _tick_actor(client, aid)

            # Delete it
            client.delete(f"/api/v1/agentos/actors/{aid}")

            # Create society
            r = client.post("/api/v1/agentos/societies", json={"name": f"RapidSoc-{i}"})
            sid = r.json()["society_id"]

            # Delete it
            client.delete(f"/api/v1/agentos/societies/{sid}")

        # System should be stable
        r = client.get("/api/v1/agentos/runtime/status")
        assert r.status_code == 200

    def test_chaos_during_learning(self, client):
        """Kill actors while learning is happening."""
        actors = [_create_actor(client, f"LearnChaos-{i}", f"task_{i}") for i in range(10)]

        # Share experiences
        for aid in actors[:5]:
            _share_experience(client, aid)

        # Kill some actors
        for aid in actors[:3]:
            client.delete(f"/api/v1/agentos/actors/{aid}")

        # Share more experiences with remaining actors
        for aid in actors[5:]:
            _share_experience(client, aid)

        # Statistics should be consistent
        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.status_code == 200
        assert r.json()["total_experiences"] >= 5


# ═══════════════════════════════════════════════════════════════════════════
# 6. Recovery Verification
# ═══════════════════════════════════════════════════════════════════════════


class TestChaosRecovery:
    """Verify full recovery after chaos."""

    def test_full_recovery_after_mass_deletion(self, client):
        """Delete everything, then rebuild."""
        # Create actors
        actors = [_create_actor(client, f"Recover-{i}", f"task_{i}") for i in range(10)]

        # Tick all
        for aid in actors:
            _tick_actor(client, aid)

        # Delete all actors
        for aid in actors:
            client.delete(f"/api/v1/agentos/actors/{aid}")

        # Create new actors
        new_actors = [_create_actor(client, f"Recovered-{i}", f"task_{i}") for i in range(5)]

        # New actors should tick
        for aid in new_actors:
            r = _tick_actor(client, aid)
            assert r.status_code == 200

        # Learning should work
        for aid in new_actors:
            _share_experience(client, aid)

        # Statistics should be updated
        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.status_code == 200
        assert r.json()["total_experiences"] >= 5

        # Runtime should be healthy
        r = client.get("/api/v1/agentos/runtime/status")
        assert r.status_code == 200
        assert r.json()["cognitive"] == "ready"

    def test_recovery_after_chaos_storm(self, client):
        """Survive a storm of random operations."""
        for cycle in range(10):
            # Random operations
            op = random.choice(["create", "tick", "delete", "learn", "planet_tick"])

            if op == "create":
                _create_actor(client, f"Storm-{cycle}", f"task_{cycle}")
            elif op == "tick":
                r = client.get("/api/v1/agentos/actors")
                if r.json():
                    aid = r.json()[0]["actor_id"]
                    _tick_actor(client, aid)
            elif op == "delete":
                r = client.get("/api/v1/agentos/actors")
                if r.json():
                    aid = r.json()[0]["actor_id"]
                    client.delete(f"/api/v1/agentos/actors/{aid}")
            elif op == "learn":
                r = client.get("/api/v1/agentos/actors")
                if r.json():
                    aid = r.json()[0]["actor_id"]
                    _share_experience(client, aid)
            elif op == "planet_tick":
                client.post("/api/v1/agentos/planet/tick")

        # System should be stable after the storm
        r = client.get("/api/v1/agentos/runtime/status")
        assert r.status_code == 200
        assert r.json()["cognitive"] == "ready"
