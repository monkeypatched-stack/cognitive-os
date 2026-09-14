"""Phase 2 — Chaos Testing: Randomly inject failures, verify recovery.

The system should degrade gracefully under component failures, not crash.
"""

from __future__ import annotations

import os
import pytest
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


class TestChaosActorDeletion:
    """Kill actors during operation."""

    def test_delete_actor_mid_society_tick(self, client):
        actors = [_create_actor(client, f"Chaos-Del-{i}", f"task_{i}") for i in range(5)]

        # Tick some actors
        for aid in actors[:3]:
            client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "a",
                    "goal": "b",
                    "reward": 1.0,
                },
            )

        # Delete one actor
        r = client.delete(f"/api/v1/agentos/actors/{actors[2]}")
        assert r.status_code == 200

        # Society tick should still work
        r = client.get("/api/v1/agentos/societies")
        sid = r.json()[0]["society_id"]
        r = client.post(f"/api/v1/agentos/societies/{sid}/tick")
        assert r.status_code == 200

    def test_delete_all_actors(self, client):
        actors = [_create_actor(client, f"Chaos-DelAll-{i}", f"task_{i}") for i in range(3)]

        # Delete all
        for aid in actors:
            client.delete(f"/api/v1/agentos/actors/{aid}")

        # Society tick with reduced actors should not crash
        r = client.get("/api/v1/agentos/societies")
        sid = r.json()[0]["society_id"]
        r = client.post(f"/api/v1/agentos/societies/{sid}/tick")
        assert r.status_code == 200
        assert r.json()["actors_ticked"] >= 0


class TestChaosSocietyDeletion:
    """Delete societies during operation."""

    def test_create_delete_society_cycle(self, client):
        # Create and immediately delete
        r = client.post("/api/v1/agentos/societies", json={"name": "Ephemeral"})
        sid = r.json()["society_id"]
        r = client.delete(f"/api/v1/agentos/societies/{sid}")
        assert r.status_code == 200

        # Planet tick should still work
        r = client.post("/api/v1/agentos/planet/tick")
        assert r.status_code == 200


class TestChaosInvalidInputs:
    """Send malformed requests."""

    def test_tick_with_invalid_body(self, client):
        aid = _create_actor(client, "Chaos-Invalid", "task")
        r = client.post(f"/api/v1/agentos/actors/{aid}/tick", json={})
        # Should either succeed with defaults or return 422
        assert r.status_code in (200, 422)

    def test_create_actor_missing_name(self, client):
        r = client.post("/api/v1/agentos/actors", json={"actor_type": "robot"})
        assert r.status_code == 422  # Pydantic validation

    def test_get_nonexistent_actor(self, client):
        r = client.get("/api/v1/agentos/actors/nonexistent_id")
        assert r.status_code in (404, 503)

    def test_tick_nonexistent_actor(self, client):
        r = client.post(
            "/api/v1/agentos/actors/nonexistent_id/tick",
            json={
                "start": "a",
                "goal": "b",
                "reward": 1.0,
            },
        )
        assert r.status_code in (404, 503)


class TestChaosRapidOperations:
    """Rapid create/delete cycles."""

    def test_rapid_actor_lifecycle(self, client):
        for i in range(20):
            aid = _create_actor(client, f"Rapid-{i}", f"task_{i}")
            client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "a",
                    "goal": "b",
                    "reward": 1.0,
                },
            )
            client.delete(f"/api/v1/agentos/actors/{aid}")

        # System should be stable
        r = client.get("/api/v1/agentos/runtime/status")
        assert r.status_code == 200
        assert r.json()["cognitive"] == "ready"


class TestChaosMembershipChaos:
    """Corrupt membership state."""

    def test_create_membership_for_deleted_actor(self, client):
        aid = _create_actor(client, "Chaos-Member", "task")
        client.delete(f"/api/v1/agentos/actors/{aid}")

        r = client.get("/api/v1/agentos/societies")
        sid = r.json()[0]["society_id"]

        # Membership for deleted actor should still be creatable (in-memory store)
        r = client.post(
            "/api/v1/agentos/memberships",
            json={
                "actor_id": aid,
                "society_id": sid,
                "role": "worker",
            },
        )
        # This is expected behavior — membership store is independent
        assert r.status_code == 200


class TestChaosPlanetTickUnderStress:
    """Planet tick under degraded conditions."""

    def test_planet_tick_after_mass_deletion(self, client):
        actors = [_create_actor(client, f"Stress-{i}", f"task_{i}") for i in range(10)]

        # Tick all
        for aid in actors:
            client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "a",
                    "goal": "b",
                    "reward": 1.0,
                },
            )

        # Delete half
        for aid in actors[:5]:
            client.delete(f"/api/v1/agentos/actors/{aid}")

        # Planet tick should work with remaining actors
        r = client.post("/api/v1/agentos/planet/tick")
        assert r.status_code == 200
        assert r.json()["actors_observed"] >= 5
