"""Tests for Kong API Gateway boundary enforcement."""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.monkey_brain.api.gateway_boundary import ApiGatewayBoundaryMiddleware


@pytest.fixture()
def gateway_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("API_GATEWAY_REQUIRED", "true")
    app = FastAPI()
    app.add_middleware(ApiGatewayBoundaryMiddleware)

    @app.get("/live")
    def live() -> dict:
        return {"status": "alive"}

    @app.get("/api/v1/agentos/planet")
    def planet() -> dict:
        return {"ok": True}

    return app


def test_exempt_health_without_kong_header(gateway_app: FastAPI) -> None:
    client = TestClient(gateway_app)
    assert client.get("/live").status_code == 200


def test_blocks_direct_api_without_kong_header(gateway_app: FastAPI) -> None:
    client = TestClient(gateway_app)
    resp = client.get("/api/v1/agentos/planet")
    assert resp.status_code == 403
    assert resp.json()["error"] == "api_gateway_required"


def test_allows_request_with_kong_header(gateway_app: FastAPI) -> None:
    client = TestClient(gateway_app)
    resp = client.get(
        "/api/v1/agentos/planet",
        headers={"X-Kong-Request-Id": "kong-req-123"},
    )
    assert resp.status_code == 200


def test_disabled_when_api_gateway_not_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_GATEWAY_REQUIRED", "false")
    app = FastAPI()
    app.add_middleware(ApiGatewayBoundaryMiddleware)

    @app.get("/api/v1/agentos/planet")
    def planet() -> dict:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/api/v1/agentos/planet").status_code == 200
