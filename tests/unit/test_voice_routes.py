"""Unit tests for api/routes/voice.py -- the voice session REST surface
(CognitiveOS LiveKit Voice Command Integration, spec section 24's "Token"
test group: authenticated user, unauthorized user, scoped room, TTL).

A bare FastAPI app carrying only voice_router (same isolated-app pattern
test_idempotency.py already uses for api/idempotency.py) rather than the
full MonkeyBrain app -- no external dependencies, no real LiveKit project
credentials needed for the auth/not-found paths, and the "LiveKit
unavailable" path (section 19) is exercised for real since this
environment genuinely has no LIVEKIT_API_KEY/SECRET configured.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.monkey_brain.api.routes.voice import router as voice_router
from src.monkey_brain.kernel.edge.voice_session import get_voice_session_store


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(voice_router, prefix="/api/v1/agentos")
    application.state.planetary_runtime = None
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def _auth_disabled(monkeypatch):
    # Same auth-bypass convention require_permission itself documents:
    # only with AGENTOS_AUTH_REQUIRED explicitly false does X-User-ID alone
    # authenticate -- lets these tests exercise the route logic without a
    # real JWT, while still going through the real dependency.
    monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "false")


def _fake_planetary_runtime(actor_id: str = "drone-a"):
    state = MagicMock()
    state.actor_runtime = MagicMock()
    sr = MagicMock()
    sr.get_actor.side_effect = lambda aid: state if aid == actor_id else None
    sr.tick_one_actor = AsyncMock(return_value=True)
    pr = MagicMock()
    pr.all_societies.return_value = [sr]
    return pr, sr, state


def test_create_session_without_identity_header_is_unauthorized(client, app):
    pr, _sr, _state = _fake_planetary_runtime()
    app.state.planetary_runtime = pr
    resp = client.post("/api/v1/agentos/voice/sessions", json={"room": "r", "actor_id": "drone-a"})
    assert resp.status_code in (401, 403)


def test_create_session_no_planetary_runtime_returns_503(client, app):
    app.state.planetary_runtime = None
    resp = client.post(
        "/api/v1/agentos/voice/sessions",
        json={"room": "r", "actor_id": "drone-a"},
        headers={"X-User-ID": "u1"},
    )
    assert resp.status_code == 503


def test_create_session_unknown_actor_returns_404(client, app):
    pr, _sr, _state = _fake_planetary_runtime(actor_id="drone-a")
    app.state.planetary_runtime = pr
    resp = client.post(
        "/api/v1/agentos/voice/sessions",
        json={"room": "r", "actor_id": "drone-does-not-exist"},
        headers={"X-User-ID": "u1"},
    )
    assert resp.status_code == 404


def test_create_session_no_livekit_credentials_returns_503(client, app):
    # This test environment genuinely has no LIVEKIT_API_KEY/SECRET set --
    # exercises the real "LiveKit unavailable" error path (section 19),
    # not a mocked one, and confirms the session is cleaned up (not left
    # dangling) rather than left half-created.
    pr, _sr, _state = _fake_planetary_runtime()
    app.state.planetary_runtime = pr
    store = get_voice_session_store()
    before = len(store._sessions)

    resp = client.post(
        "/api/v1/agentos/voice/sessions",
        json={"room": "r", "actor_id": "drone-a"},
        headers={"X-User-ID": "u1"},
    )

    assert resp.status_code == 503
    assert len(store._sessions) == before  # cleaned up, not leaked


def test_create_session_success_returns_token_and_derives_identity_from_auth(client, app):
    pr, _sr, _state = _fake_planetary_runtime()
    app.state.planetary_runtime = pr

    with (
        patch("src.monkey_brain.api.routes.voice.create_livekit_room_token", return_value="fake.jwt.token") as mint,
        patch("src.monkey_brain.api.routes.voice.VoiceCommandRuntime") as runtime_cls,
    ):
        runtime = MagicMock()
        runtime.stop = AsyncMock()
        runtime_cls.return_value = runtime

        resp = client.post(
            "/api/v1/agentos/voice/sessions",
            json={"room": "r", "actor_id": "drone-a"},
            headers={"X-User-ID": "u1"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["token"] == "fake.jwt.token"
    # Identity is SERVER-derived from the authenticated caller, not
    # client-supplied (section 8) -- never trust an arbitrary speaker id.
    assert body["participant_identity"] == "voice-u1"
    assert body["actor_id"] == "drone-a"
    runtime.start.assert_called_once()
    # The browser's own token must be able to publish (it's the mic
    # publisher); CognitiveOS's own listener uses a SEPARATE identity
    # internal to VoiceCommandRuntime, never this one.
    mint.assert_called_once()
    _, kwargs = mint.call_args
    assert kwargs.get("can_publish") is True


def test_get_status_unknown_session_returns_404(client):
    resp = client.get("/api/v1/agentos/voice/sessions/does-not-exist", headers={"X-User-ID": "u1"})
    assert resp.status_code == 404


def test_get_status_returns_current_session_state(client):
    store = get_voice_session_store()
    session = store.create(room="r", actor_id="drone-a", participant_identity="voice-u1", created_by_user_id="u1")
    store.update(session.session_id, status="clarification_required", clarification_reason="which waypoint?")

    resp = client.get(f"/api/v1/agentos/voice/sessions/{session.session_id}", headers={"X-User-ID": "u1"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "clarification_required"
    assert body["clarification_reason"] == "which waypoint?"
