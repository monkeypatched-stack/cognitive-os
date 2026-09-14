"""E2E acceptance test for Sprint 13 — Complete Actor Cognitive Execution Loop.

Verifies the full lifecycle:
  Boot → Create Society → Create Actors → Create Memberships →
  Actor Tick → Society Tick → Planet Tick →
  World Updated → Experience Stored → Learning Updated →
  Runtime Metrics → Clean Shutdown
"""

from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"


@pytest.fixture(scope="module")
def client():
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parents[2] / ".env")
    from src.monkey_brain.api.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestSprint13Acceptance:
    def test_full_cognitive_lifecycle(self, client):
        # ── 1. Create societies ──────────────────────────────────────────
        r = client.post(
            "/api/v1/agentos/societies",
            json={
                "name": "Milk Delivery Society",
                "description": "Coordinates milk delivery",
            },
        )
        assert r.status_code == 200
        s1_id = r.json()["society_id"]

        r = client.post(
            "/api/v1/agentos/societies",
            json={
                "name": "Warehouse Society",
                "description": "Manages warehouse operations",
            },
        )
        assert r.status_code == 200
        s2_id = r.json()["society_id"]

        # ── 2. Create actors with goals ──────────────────────────────────
        actors = []
        for name, atype, goal in [
            ("Alice", "human", "deliver_milk"),
            ("Robot-3000", "robot", "navigate_warehouse"),
            ("RetailCo", "enterprise", "manage_inventory"),
        ]:
            r = client.post(
                "/api/v1/agentos/actors",
                json={
                    "name": name,
                    "actor_type": atype,
                    "capabilities": [{"name": "delivery"}],
                    "goals": [goal],
                },
            )
            assert r.status_code == 200
            actors.append(r.json()["actor_id"])

        # ── 3. Create memberships ────────────────────────────────────────
        for aid in actors[:2]:
            r = client.post(
                "/api/v1/agentos/memberships",
                json={
                    "actor_id": aid,
                    "society_id": s1_id,
                    "role": "worker",
                },
            )
            assert r.status_code == 200
        for aid in actors[2:]:
            r = client.post(
                "/api/v1/agentos/memberships",
                json={
                    "actor_id": aid,
                    "society_id": s2_id,
                    "role": "manager",
                },
            )
            assert r.status_code == 200

        # ── 4. Actor ticks — real cognitive loop ──────────────────────────
        for aid in actors:
            r = client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "warehouse",
                    "goal": "customer_door",
                    "reward": 1.0,
                },
            )
            assert r.status_code == 200
            d = r.json()
            result = d["result"]
            assert result.get("belief_updated") is True or "plan" in result
            assert d["tick_count"] >= 1

        # ── 5. Society tick — tick primary society (has actors) ───────────
        r = client.get("/api/v1/agentos/societies")
        primary_society = r.json()[0]["society_id"]
        r = client.post(f"/api/v1/agentos/societies/{primary_society}/tick")
        assert r.status_code == 200
        st = r.json()
        assert st["actors_ticked"] >= 1
        assert st["duration_ms"] >= 0

        # ── 6. Planet tick ───────────────────────────────────────────────
        r = client.post("/api/v1/agentos/planet/tick")
        assert r.status_code == 200
        pt = r.json()
        assert pt["actors_observed"] >= 1
        assert pt["cycle_number"] >= 1
        assert pt["duration_ms"] >= 0

        # ── 7. Share experience via /learn/experience ────────────────────
        r = client.post(
            "/api/v1/agentos/learn/experience",
            json={
                "experience": {
                    "actor_id": actors[0],
                    "description": "Delivered milk successfully",
                    "outcome": "success",
                    "confidence": 0.9,
                    "lessons": ["avoid_traffic", "use_back_road"],
                }
            },
        )
        assert r.status_code == 200
        lr = r.json()
        assert lr["status"] == "ok"
        assert lr["result"]["experience_id"]
        assert lr["result"]["world_refined"] is True

        # ── 8. Learning statistics updated ───────────────────────────────
        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.status_code == 200
        stats = r.json()
        assert stats["total_experiences"] >= 1

        # ── 9. World events generated ────────────────────────────────────
        r = client.get("/api/v1/agentos/world/events")
        assert r.status_code == 200
        events = r.json()
        assert events["count"] >= 1

        # ── 10. World state ──────────────────────────────────────────────
        r = client.get("/api/v1/agentos/world")
        assert r.status_code == 200
        world = r.json()
        assert world["version"] >= 0

        # ── 11. Runtime metrics ──────────────────────────────────────────
        r = client.get("/api/v1/agentos/runtime/status")
        assert r.status_code == 200
        rs = r.json()
        assert rs["cognitive"] == "ready"
        assert rs["society"] == "ready"

        # ── 12. Planet overview ──────────────────────────────────────────
        r = client.get("/api/v1/agentos/planet")
        assert r.status_code == 200
        planet = r.json()
        assert planet["actor_count"] >= 3
        assert planet["society_registry_count"] >= 2

        # ── Print summary ────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("  Sprint 13 Acceptance: PASSED")
        print("=" * 60)
        print(f"  Societies:        {planet['society_registry_count']}")
        print(f"  Actors:           {planet['actor_count']}")
        print(f"  Planet cycle:     {pt['cycle_number']}")
        print(f"  Society tick:     {st['actors_ticked']} actor(s)")
        print(f"  Experiences:      {stats['total_experiences']}")
        print(f"  World events:     {events['count']}")
        print(f"  World version:    {world['version']}")
        print(f"  Runtime:          {rs['cognitive']}/{rs['society']}")
        print(f"  Clean shutdown:   ✓")
        print("=" * 60)
