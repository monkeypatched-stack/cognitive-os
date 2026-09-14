"""Pilot boot smoke — the real assembled server object boots and serves.

Imports the actual FastAPI app from main.py (all routers, middleware, the exchange server)
and drives it via TestClient without the full Kernel lifespan, proving the server assembles
and its core + exchange routes respond.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def app():
    from src.monkey_brain.api.main import app as real_app

    return real_app


def test_app_assembles_with_core_and_exchange_routes(app):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    # the exchange transport is mounted: proposal is POST-only (GET → 405, not 404 = exists)
    assert client.get("/api/v1/agentos/exchange/proposal").status_code == 405
    assert client.get("/api/v1/agentos/exchange/stats").status_code == 200


def test_health_responds(app):
    from fastapi.testclient import TestClient

    client = TestClient(app)  # no context manager → no Kernel boot needed
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "monkeybrain-runtime"


def test_exchange_stats_responds(app):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/api/v1/agentos/exchange/stats")
    assert r.status_code == 200
    assert "queue_pending" in r.json()
