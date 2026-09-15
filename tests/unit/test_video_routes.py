"""Unit tests for api/routes/video.py -- the drone camera video session REST
surface (CognitiveOS Drone Camera Video Telemetry, spec TESTS #1's "Token"
group carried over from the voice spec's own wording, adapted for video's
subscribe-only token).

Same isolated-bare-FastAPI-app pattern tests/unit/test_voice_routes.py
already uses -- no external dependencies, and the "LiveKit unavailable"
path is exercised for real wherever LIVEKIT_API_KEY/SECRET genuinely
aren't set in this environment."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.monkey_brain.api.routes.video import router as video_router
from src.monkey_brain.kernel.edge.camera_state import (
    CameraIdentity,
    register_camera_identity,
    unregister_camera_identity,
)
from src.monkey_brain.kernel.edge.video_session import get_video_session_store


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(video_router, prefix="/api/v1/agentos")
    application.state.planetary_runtime = None
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def _auth_disabled(monkeypatch):
    monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "false")


@pytest.fixture(autouse=True)
def _registered_camera():
    identity = CameraIdentity(
        actor_id="drone-a",
        vehicle_id="px4/drone-a",
        ros_namespace="px4_1",
        livekit_room="mission-room",
        livekit_participant_identity="drone-a-camera",
        camera_track_name="drone-a-camera-track",
    )
    register_camera_identity(identity)
    yield identity
    unregister_camera_identity("drone-a")


def _fake_planetary_runtime(actor_id: str = "drone-a"):
    state = MagicMock()
    sr = MagicMock()
    sr.get_actor.side_effect = lambda aid: state if aid == actor_id else None
    sr.tick_one_actor = AsyncMock(return_value=True)
    pr = MagicMock()
    pr.all_societies.return_value = [sr]
    return pr, sr, state


def test_create_session_without_identity_header_is_unauthorized(client, app):
    pr, _sr, _state = _fake_planetary_runtime()
    app.state.planetary_runtime = pr
    resp = client.post("/api/v1/agentos/video/sessions", json={"actor_id": "drone-a"})
    assert resp.status_code in (401, 403)


def test_create_session_no_planetary_runtime_returns_503(client, app):
    app.state.planetary_runtime = None
    resp = client.post("/api/v1/agentos/video/sessions", json={"actor_id": "drone-a"}, headers={"X-User-ID": "u1"})
    assert resp.status_code == 503


def test_create_session_unknown_actor_returns_404(client, app):
    pr, _sr, _state = _fake_planetary_runtime(actor_id="drone-a")
    app.state.planetary_runtime = pr
    resp = client.post(
        "/api/v1/agentos/video/sessions",
        json={"actor_id": "drone-does-not-exist"},
        headers={"X-User-ID": "u1"},
    )
    assert resp.status_code == 404


def test_create_session_actor_without_registered_camera_returns_404(client, app):
    # A real actor with no camera registered (spec's own scope: not every
    # actor is a drone with a camera) -- must not fall back to guessing a
    # track name.
    pr, _sr, _state = _fake_planetary_runtime(actor_id="drone-no-camera")
    app.state.planetary_runtime = pr
    resp = client.post(
        "/api/v1/agentos/video/sessions",
        json={"actor_id": "drone-no-camera"},
        headers={"X-User-ID": "u1"},
    )
    assert resp.status_code == 404


def test_create_session_no_livekit_credentials_returns_503(client, app):
    pr, _sr, _state = _fake_planetary_runtime()
    app.state.planetary_runtime = pr
    store = get_video_session_store()
    before = len(store._sessions)

    resp = client.post("/api/v1/agentos/video/sessions", json={"actor_id": "drone-a"}, headers={"X-User-ID": "u1"})

    assert resp.status_code == 503
    assert len(store._sessions) == before  # cleaned up, not leaked


def test_create_session_success_returns_subscribe_only_token(client, app):
    pr, _sr, _state = _fake_planetary_runtime()
    app.state.planetary_runtime = pr

    with (
        patch("src.monkey_brain.api.routes.video.create_livekit_room_token", return_value="fake.jwt.token") as mint,
        patch("src.monkey_brain.api.routes.video.VideoCommandRuntime") as runtime_cls,
    ):
        runtime = MagicMock()
        runtime.stop = AsyncMock()
        runtime_cls.return_value = runtime

        resp = client.post("/api/v1/agentos/video/sessions", json={"actor_id": "drone-a"}, headers={"X-User-ID": "u1"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["token"] == "fake.jwt.token"
    assert body["actor_id"] == "drone-a"
    assert body["camera_track_name"] == "drone-a-camera-track"
    assert body["participant_identity"] == "video-u1"
    runtime.start.assert_called_once()

    mint.assert_called_once()
    _, kwargs = mint.call_args
    # The browser here is a VIEWER of the drone's camera, never a
    # publisher -- unlike voice.py's session, where can_publish=True is
    # correct because the browser publishes its own microphone.
    assert kwargs.get("can_publish") is False
    assert kwargs.get("can_subscribe") is True


def test_get_status_unknown_session_returns_404(client):
    resp = client.get("/api/v1/agentos/video/sessions/no-such-session", headers={"X-User-ID": "u1"})
    assert resp.status_code == 404


def test_stop_unknown_session_returns_404(client):
    resp = client.delete("/api/v1/agentos/video/sessions/no-such-session", headers={"X-User-ID": "u1"})
    assert resp.status_code == 404
