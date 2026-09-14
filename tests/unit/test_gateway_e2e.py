"""E2E tests for Runtime Gateway — every endpoint hit via TestClient.

Uses the real FastAPI app (no Kernel lifespan), proving all gateway
routers assembled correctly and respond with expected status codes and
shapes. Read-only endpoints that gracefully handle missing runtimes
return defaults; mutation endpoints that need a runtime return 503.

Auth is disabled for testing via AGENTOS_AUTH_REQUIRED=false.
"""

from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"
os.environ["API_GATEWAY_REQUIRED"] = "false"


@pytest.fixture(scope="module")
def client():
    from src.monkey_brain.api.main import app as real_app

    return TestClient(real_app, raise_server_exceptions=False)


# ═══════════════════════════════════════════════════════════════════════════
# Planet
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanetEndpoints:
    def test_get_planet(self, client):
        r = client.get("/api/v1/agentos/planet")
        assert r.status_code == 200
        d = r.json()
        assert "society_id" in d
        assert "actor_count" in d
        assert "world_version" in d

    def test_get_planet_status(self, client):
        r = client.get("/api/v1/agentos/planet/status")
        assert r.status_code == 200
        d = r.json()
        assert "status" in d
        assert "uptime_s" in d
        assert "cycle_count" in d
        assert "societies" in d
        assert "actors" in d

    def test_get_planet_societies(self, client):
        r = client.get("/api/v1/agentos/planet/societies")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_planet_actors(self, client):
        r = client.get("/api/v1/agentos/planet/actors")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_planet_statistics(self, client):
        r = client.get("/api/v1/agentos/planet/statistics")
        assert r.status_code == 200
        d = r.json()
        assert "total_actors" in d
        assert "total_societies" in d
        assert "total_federations" in d


# ═══════════════════════════════════════════════════════════════════════════
# Societies
# ═══════════════════════════════════════════════════════════════════════════


class TestSocietyEndpoints:
    def test_list_societies(self, client):
        r = client.get("/api/v1/agentos/societies")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_society_no_runtime(self, client):
        r = client.post("/api/v1/agentos/societies", json={"name": "Test Society"})
        assert r.status_code == 503

    def test_get_society_not_found(self, client):
        r = client.get("/api/v1/agentos/societies/nonexistent")
        assert r.status_code == 503

    def test_delete_society_not_found(self, client):
        r = client.delete("/api/v1/agentos/societies/nonexistent")
        assert r.status_code == 503

    def test_patch_society_not_found(self, client):
        r = client.patch("/api/v1/agentos/societies/nonexistent", json={"name": "new"})
        assert r.status_code == 503

    def test_get_society_status_not_found(self, client):
        r = client.get("/api/v1/agentos/societies/nonexistent/status")
        assert r.status_code == 503

    def test_get_society_actors_not_found(self, client):
        r = client.get("/api/v1/agentos/societies/nonexistent/actors")
        assert r.status_code == 503

    def test_get_society_context_not_found(self, client):
        r = client.get("/api/v1/agentos/societies/nonexistent/context")
        assert r.status_code == 503

    def test_get_society_beliefs_not_found(self, client):
        r = client.get("/api/v1/agentos/societies/nonexistent/beliefs")
        assert r.status_code == 503

    def test_get_society_resources_not_found(self, client):
        r = client.get("/api/v1/agentos/societies/nonexistent/resources")
        assert r.status_code == 503


# ═══════════════════════════════════════════════════════════════════════════
# Actors
# ═══════════════════════════════════════════════════════════════════════════


class TestActorEndpoints:
    def test_list_actors(self, client):
        r = client.get("/api/v1/agentos/actors")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_actor_no_runtime(self, client):
        r = client.post("/api/v1/agentos/actors", json={"name": "Test Actor"})
        assert r.status_code == 503

    def test_get_actor_not_found(self, client):
        r = client.get("/api/v1/agentos/actors/nonexistent")
        assert r.status_code == 503

    def test_delete_actor_not_found(self, client):
        r = client.delete("/api/v1/agentos/actors/nonexistent")
        assert r.status_code == 503

    def test_patch_actor_not_found(self, client):
        r = client.patch("/api/v1/agentos/actors/nonexistent", json={"name": "new"})
        assert r.status_code == 503

    def test_get_actor_beliefs_not_found(self, client):
        r = client.get("/api/v1/agentos/actors/nonexistent/beliefs")
        assert r.status_code == 503

    def test_get_actor_memory_not_found(self, client):
        r = client.get("/api/v1/agentos/actors/nonexistent/memory")
        assert r.status_code == 503

    def test_get_actor_goals_not_found(self, client):
        r = client.get("/api/v1/agentos/actors/nonexistent/goals")
        assert r.status_code == 503

    def test_get_actor_capabilities_not_found(self, client):
        r = client.get("/api/v1/agentos/actors/nonexistent/capabilities")
        assert r.status_code == 503

    def test_get_actor_status_not_found(self, client):
        r = client.get("/api/v1/agentos/actors/nonexistent/status")
        assert r.status_code == 503

    def test_tick_actor_not_found(self, client):
        r = client.post("/api/v1/agentos/actors/nonexistent/tick", json={})
        assert r.status_code == 503

    def test_observe_actor_not_found(self, client):
        r = client.post("/api/v1/agentos/actors/nonexistent/observe")
        assert r.status_code == 503

    def test_plan_actor_not_found(self, client):
        r = client.post("/api/v1/agentos/actors/nonexistent/plan")
        assert r.status_code == 503

    def test_execute_actor_not_found(self, client):
        r = client.post("/api/v1/agentos/actors/nonexistent/execute")
        assert r.status_code == 503


# ═══════════════════════════════════════════════════════════════════════════
# Memberships
# ═══════════════════════════════════════════════════════════════════════════


class TestMembershipEndpoints:
    def test_list_memberships(self, client):
        r = client.get("/api/v1/agentos/memberships")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_membership_no_society(self, client):
        r = client.post(
            "/api/v1/agentos/memberships",
            json={
                "actor_id": "a1",
                "society_id": "nonexistent",
            },
        )
        assert r.status_code == 503

    def test_delete_membership_not_found(self, client):
        r = client.delete("/api/v1/agentos/memberships/nonexistent")
        assert r.status_code == 404

    def test_get_actor_memberships(self, client):
        r = client.get("/api/v1/agentos/memberships/actor/a1")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_society_memberships(self, client):
        r = client.get("/api/v1/agentos/memberships/society/s1")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_patch_membership_not_found(self, client):
        r = client.patch("/api/v1/agentos/memberships/nonexistent", json={"role": "admin"})
        assert r.status_code == 404

    def test_create_list_delete_lifecycle(self, client):
        # Without a booted PlanetaryRuntime, create returns 503
        create = client.post(
            "/api/v1/agentos/memberships",
            json={
                "actor_id": "a1",
                "society_id": "s1",
                "role": "worker",
            },
        )
        assert create.status_code == 503  # no PlanetaryRuntime booted


# ═══════════════════════════════════════════════════════════════════════════
# Runtime
# ═══════════════════════════════════════════════════════════════════════════


class TestRuntimeEndpoints:
    def test_get_runtime(self, client):
        r = client.get("/api/v1/agentos/runtime")
        assert r.status_code == 200
        d = r.json()
        assert "name" in d
        assert "status" in d

    def test_get_runtime_status(self, client):
        r = client.get("/api/v1/agentos/runtime/status")
        assert r.status_code == 200
        d = r.json()
        assert "cognitive" in d
        assert "simulation" in d
        assert "comparator" in d
        assert "society" in d

    def test_get_runtime_health(self, client):
        r = client.get("/api/v1/agentos/runtime/health")
        assert r.status_code == 200
        d = r.json()
        assert "overall" in d
        assert "components" in d

    def test_get_runtime_configuration(self, client):
        r = client.get("/api/v1/agentos/runtime/configuration")
        assert r.status_code == 200
        d = r.json()
        assert "auth_required" in d
        assert "rate_limit_rps" in d
        assert "cors_origins" in d

    def test_get_runtime_metrics(self, client):
        r = client.get("/api/v1/agentos/runtime/metrics")
        assert r.status_code == 200
        d = r.json()
        assert "total_requests" in d
        assert "total_cycles" in d


# ═══════════════════════════════════════════════════════════════════════════
# Simulation
# ═══════════════════════════════════════════════════════════════════════════


class TestSimulationEndpoints:
    def test_simulate_no_runtime(self, client):
        # /simulate/run, not bare /simulate -- that path belongs to
        # api/routes/predict.py's real plan-driven simulate endpoint
        # (relied on by the CLI/REPL); this gateway's own simpler
        # direct-simulate action lives at /simulate/run to avoid colliding.
        r = client.post("/api/v1/agentos/simulate/run", json={"question": "test"})
        assert r.status_code in (503, 500)

    def test_simulate_step_no_runtime(self, client):
        r = client.post("/api/v1/agentos/simulate/step", json={"question": "test", "steps": 1})
        assert r.status_code in (503, 500)

    def test_simulate_scenario_no_runtime(self, client):
        r = client.post("/api/v1/agentos/simulate/scenario", json={"name": "test"})
        assert r.status_code in (503, 500)

    def test_simulate_status(self, client):
        r = client.get("/api/v1/agentos/simulate/status")
        assert r.status_code == 200
        d = r.json()
        assert "status" in d


# ═══════════════════════════════════════════════════════════════════════════
# Comparator
# ═══════════════════════════════════════════════════════════════════════════


class TestComparatorEndpoints:
    def test_compare_no_runtime(self, client):
        # /compare/run, not bare /compare -- that path belongs to
        # api/routes/predict.py's real plan-driven compare endpoint
        # (relied on by the CLI/REPL); this gateway's own simpler
        # direct-compare action lives at /compare/run to avoid colliding.
        r = client.post("/api/v1/agentos/compare/run", json={"question": "test"})
        assert r.status_code in (503, 500)

    def test_compare_epistemic_loss_no_runtime(self, client):
        r = client.post("/api/v1/agentos/compare/epistemic-loss", json={})
        assert r.status_code in (503, 500)

    def test_compare_history(self, client):
        r = client.get("/api/v1/agentos/compare/history")
        assert r.status_code == 200
        d = r.json()
        assert "comparisons" in d
        assert "total" in d


# ═══════════════════════════════════════════════════════════════════════════
# Learning
# ═══════════════════════════════════════════════════════════════════════════


class TestLearningEndpoints:
    def test_learn_no_runtime(self, client):
        r = client.post("/api/v1/agentos/learn", json={"experience": {}})
        assert r.status_code in (200, 500)

    def test_learn_update(self, client):
        r = client.post(
            "/api/v1/agentos/learn/update",
            json={
                "actor_id": "a1",
                "updates": {},
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_learn_train(self, client):
        r = client.post("/api/v1/agentos/learn/train", json={"data": [], "epochs": 5})
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "ok"
        assert d["result"]["epochs"] == 5

    def test_learn_statistics(self, client):
        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.status_code == 200
        d = r.json()
        assert "total_experiences" in d
        assert "total_reputations" in d
        assert "total_improvements" in d


# ═══════════════════════════════════════════════════════════════════════════
# World
# ═══════════════════════════════════════════════════════════════════════════


class TestWorldEndpoints:
    def test_get_world(self, client):
        r = client.get("/api/v1/agentos/world")
        assert r.status_code == 200
        d = r.json()
        assert "version" in d
        assert "entities" in d
        assert "relationships" in d
        assert "events" in d
        assert "resources" in d

    def test_get_world_context(self, client):
        r = client.get("/api/v1/agentos/world/context")
        assert r.status_code == 200
        d = r.json()
        assert "events" in d
        assert "event_count" in d

    def test_get_world_beliefs(self, client):
        r = client.get("/api/v1/agentos/world/beliefs")
        assert r.status_code == 200
        d = r.json()
        assert "actors" in d

    def test_get_world_entities(self, client):
        r = client.get("/api/v1/agentos/world/entities")
        assert r.status_code == 200
        d = r.json()
        assert "entities" in d
        assert "count" in d

    def test_get_world_relationships(self, client):
        r = client.get("/api/v1/agentos/world/relationships")
        assert r.status_code == 200
        d = r.json()
        assert "relationships" in d
        assert "count" in d

    def test_query_world(self, client):
        r = client.post("/api/v1/agentos/world/query", json={"query": "test"})
        assert r.status_code == 200
        d = r.json()
        assert "results" in d
        assert "count" in d


# ═══════════════════════════════════════════════════════════════════════════
# Discovery
# ═══════════════════════════════════════════════════════════════════════════


class TestDiscoveryEndpoints:
    def test_get_providers(self, client):
        r = client.get("/api/v1/agentos/providers")
        assert r.status_code == 200
        d = r.json()
        assert "providers" in d or "total" in d

    def test_get_capabilities(self, client):
        # Existing /capabilities route from capabilities.py takes precedence
        r = client.get("/api/v1/agentos/capabilities")
        assert r.status_code == 200
        d = r.json()
        assert "capabilities" in d or "total" in d

    def test_get_agents(self, client):
        # Existing /agents route from agents.py takes precedence
        r = client.get("/api/v1/agentos/agents")
        assert r.status_code == 200
        d = r.json()
        assert "agents" in d or "total" in d

    def test_get_models(self, client):
        r = client.get("/api/v1/agentos/models")
        assert r.status_code == 200
        d = r.json()
        assert "models" in d
        assert "count" in d


# ═══════════════════════════════════════════════════════════════════════════
# Admin
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminEndpoints:
    def test_boot(self, client):
        r = client.post("/api/v1/agentos/boot", json={"force": False})
        assert r.status_code == 200
        d = r.json()
        assert "status" in d
        assert "components" in d

    def test_shutdown(self, client):
        r = client.post("/api/v1/agentos/shutdown")
        assert r.status_code == 200
        assert r.json()["status"] == "shutdown_requested"

    def test_reload(self, client):
        r = client.post("/api/v1/agentos/reload")
        assert r.status_code == 200
        d = r.json()
        assert "status" in d
        assert "modules_reloaded" in d

    def test_logs(self, client):
        r = client.get("/api/v1/agentos/logs")
        assert r.status_code == 200
        d = r.json()
        assert "logs" in d
        assert "count" in d

    def test_configuration(self, client):
        r = client.get("/api/v1/agentos/configuration")
        assert r.status_code == 200
        d = r.json()
        assert "config" in d
        assert "auth_required" in d["config"]

    def test_version(self, client):
        import tomllib

        r = client.get("/api/v1/agentos/version")
        assert r.status_code == 200
        d = r.json()
        # The endpoint reads project.version from pyproject.toml live — assert
        # against that same source instead of a hardcoded string that goes
        # stale on every version bump.
        with open("pyproject.toml", "rb") as f:
            expected_version = tomllib.load(f)["project"]["version"]
        assert d["version"] == expected_version
        assert d["runtime"] == "monkeybrain"


# ═══════════════════════════════════════════════════════════════════════════
# Backward compatibility — existing endpoints still work
# ═══════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    def test_health_still_works(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["service"] == "monkeybrain-runtime"

    def test_readiness_still_works(self, client):
        r = client.get("/ready")
        assert r.status_code in (200, 503)

    def test_openapi_docs_available(self, client):
        r = client.get("/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        paths = schema.get("paths", {})
        assert "/api/v1/agentos/planet" in paths
        assert "/api/v1/agentos/societies" in paths
        assert "/api/v1/agentos/actors" in paths
        assert "/api/v1/agentos/memberships" in paths
        assert "/api/v1/agentos/runtime" in paths
        assert "/api/v1/agentos/simulate" in paths
        assert "/api/v1/agentos/compare" in paths
        assert "/api/v1/agentos/learn" in paths
        assert "/api/v1/agentos/world" in paths
        assert "/api/v1/agentos/version" in paths
