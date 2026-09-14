"""Health/readiness must tell the truth (H-1).

/health hardcoded  {"status": "healthy"}  and returned 200 regardless — it computed
overall_health() and then ignored it. Observed live: Mongo and Neo4j both DOWN, every /plan
request hanging for 3+ minutes, and /health still answering 200 "healthy" to its load
balancer. There was no readiness probe at all, so nothing could take the instance out of
rotation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class _Lemon:
    def __init__(self, health):
        self._h = health

    def overall_health(self):
        return self._h


@pytest.fixture()
def client():
    from src.monkey_brain.api.main import app

    return TestClient(app)


def test_health_reports_actual_state_not_a_hardcoded_healthy(client):
    client.app.state.lemon = _Lemon("unhealthy")
    r = client.get("/health")
    assert r.status_code == 200  # liveness: process IS alive
    assert r.json()["status"] == "unhealthy"  # but it must not CLAIM to be healthy
    assert r.json()["health"] == "unhealthy"


def test_readiness_takes_a_dead_instance_out_of_rotation(client):
    client.app.state.lemon = _Lemon("unhealthy")
    r = client.get("/ready")
    assert r.status_code == 503  # LB removes it instead of hanging traffic
    assert r.json()["ready"] is False


def test_readiness_serves_when_healthy_or_degraded(client):
    for state in ("healthy", "degraded"):
        client.app.state.lemon = _Lemon(state)
        r = client.get("/ready")
        assert r.status_code == 200, f"{state} should still serve"
        assert r.json()["ready"] is True


def test_liveness_does_not_gate_on_dependencies(client):
    """A datastore blip must not fail liveness — that would restart every pod at once."""
    client.app.state.lemon = _Lemon("unhealthy")
    assert client.get("/health").status_code == 200
