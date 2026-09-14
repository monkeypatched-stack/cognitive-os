"""Tests for internal actor-runtime authentication."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.monkey_brain.api.internal_auth import require_internal_service_token


@pytest.fixture()
def internal_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("ACTOR_RUNTIME_INTERNAL_ONLY", "true")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "test-internal-token")
    app = FastAPI()

    @app.post("/execute")
    async def execute(request: Request) -> dict:
        require_internal_service_token(request)
        return {"ok": True}

    return app


def test_execute_rejects_missing_token(internal_app: FastAPI) -> None:
    client = TestClient(internal_app)
    resp = client.post("/execute")
    assert resp.status_code == 403


def test_execute_accepts_valid_token(internal_app: FastAPI) -> None:
    client = TestClient(internal_app)
    resp = client.post(
        "/execute",
        headers={"X-Internal-Service-Token": "test-internal-token"},
    )
    assert resp.status_code == 200
