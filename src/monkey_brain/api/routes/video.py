"""Drone camera video session API (CognitiveOS Drone Camera Video
Telemetry, spec sections "VIDEO TELEMETRY" / "AUTHORIZATION"). Sibling of
api/routes/voice.py -- same REST-is-only-for-setup shape (spec: "Do not
route realtime audio through the normal REST API" applies identically to
video here); the actual camera frames travel over LiveKit, never through
this route.

Reuses api/routes/livekit.py's create_livekit_room_token() directly (same
LiveKit room/token architecture voice already established -- no second
LiveKit service, no second auth mechanism) and require_permission, this
codebase's one auth dependency.

Unlike voice.py's session (where the BROWSER publishes microphone audio to
CognitiveOS), a video session's browser side SUBSCRIBES to the drone's
camera track published by kernel/edge/livekit_video_adapter.py::
RosCameraToLiveKitBridge -- so the token minted for the caller here is
subscribe-only (can_publish=False), never a publish grant. The caller can
watch; only the ROS-camera-bridge (a CognitiveOS-owned process, not the
browser) publishes.

The actor_id -> camera track mapping is resolved server-side from
kernel/edge/camera_state.py's registry, never accepted from the client
(spec's "Do not rely on display names or implicit ordering" -- a client
could otherwise ask to watch any track name it likes)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent
from src.monkey_brain.kernel.edge.camera_state import get_camera_identity
from src.monkey_brain.kernel.edge.livekit_adapter import (
    LiveKitUnavailableError,
    create_livekit_room_token,
)
from src.monkey_brain.kernel.edge.livekit_video_adapter import LiveKitVideoUnavailableError
from src.monkey_brain.kernel.edge.video_command_runtime import VideoCommandRuntime
from src.monkey_brain.kernel.edge.video_session import get_video_session_store

logger = logging.getLogger("agentos.gateway.video")
router = APIRouter()

# session_id -> running bridge. Process-local, matching VideoSessionStore's
# own scope (same justification as api/routes/voice.py's own _runtimes dict).
_runtimes: dict[str, VideoCommandRuntime] = {}


class VideoSessionCreateRequest(BaseModel):
    actor_id: str
    ttl_seconds: int = 120


class VideoSessionResponse(BaseModel):
    session_id: str
    room: str
    actor_id: str
    camera_track_name: str
    participant_identity: str
    status: str
    token: str


class VideoSessionStatusResponse(BaseModel):
    session_id: str
    status: str
    stream_available: bool
    last_observation_attribute: str | None
    last_observation_value: object
    last_observation_confidence: float | None
    error: str | None


def _get_planetary_runtime(request: Request):
    return getattr(request.app.state, "planetary_runtime", None)


@router.post("/video/sessions", response_model=VideoSessionResponse, tags=["Video"])
@idempotent("video.create_session")
async def create_video_session(
    body: VideoSessionCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> VideoSessionResponse:
    """Start watching one drone's camera feed and let CognitiveOS start
    perceiving it. Viewing a camera feed is a read/observe action, not a
    command -- gated on perm-view-actors (matching GET /actors/{id}'s own
    permission), not perm-execute-action, which api/routes/voice.py's
    session-create correctly requires instead (a voice session can END UP
    commanding the actor; watching a camera never can by itself)."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    found = any(sr.get_actor(body.actor_id) is not None for sr in pr.all_societies())
    if not found:
        raise HTTPException(status_code=404, detail=f"Actor {body.actor_id} not found")

    identity = get_camera_identity(body.actor_id)
    if identity is None:
        raise HTTPException(status_code=404, detail=f"No camera registered for actor {body.actor_id}")

    store = get_video_session_store()
    session = store.create(
        room=identity.livekit_room,
        actor_id=body.actor_id,
        camera_track_name=identity.camera_track_name,
        created_by_user_id=user_id,
    )

    try:
        runtime = VideoCommandRuntime(session, pr)
        runtime.start()
        _runtimes[session.session_id] = runtime
    except LiveKitVideoUnavailableError as exc:
        store.remove(session.session_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    participant_identity = f"video-{user_id}"
    try:
        token = create_livekit_room_token(
            identity.livekit_room,
            participant_identity,
            can_publish=False,
            can_subscribe=True,
            ttl_seconds=body.ttl_seconds,
        )
    except (LiveKitUnavailableError, RuntimeError) as exc:
        await runtime.stop()
        _runtimes.pop(session.session_id, None)
        store.remove(session.session_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return VideoSessionResponse(
        session_id=session.session_id,
        room=session.room,
        actor_id=session.actor_id,
        camera_track_name=session.camera_track_name,
        participant_identity=participant_identity,
        status=session.status,
        token=token,
    )


@router.get("/video/sessions/{session_id}", response_model=VideoSessionStatusResponse, tags=["Video"])
async def get_video_session_status(
    session_id: str,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> VideoSessionStatusResponse:
    """Polled by the frontend panel for connection/observation/status
    display -- same polling-over-websocket MVP tradeoff voice.py made."""
    session = get_video_session_store().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="video session not found")
    return VideoSessionStatusResponse(
        session_id=session.session_id,
        status=session.status,
        stream_available=session.stream_available,
        last_observation_attribute=session.last_observation_attribute,
        last_observation_value=session.last_observation_value,
        last_observation_confidence=session.last_observation_confidence,
        error=session.error,
    )


@router.delete("/video/sessions/{session_id}", tags=["Video"])
@idempotent("video.stop_session")
async def stop_video_session(
    session_id: str,
    user_id: str = Depends(require_permission("perm-execute-action")),
) -> dict:
    runtime = _runtimes.pop(session_id, None)
    if runtime is not None:
        await runtime.stop()
    session = get_video_session_store().remove(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="video session not found")
    return {"session_id": session_id, "stopped": True}
