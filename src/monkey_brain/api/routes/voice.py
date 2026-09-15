"""Voice session API (CognitiveOS LiveKit Voice Command Integration,
spec sections 4/5/8/13/17/18).

REST is ONLY used here for session setup / token issuance / status polling
-- never for realtime audio (section 4: "Do not route realtime audio
through the normal REST API. REST/API endpoints may be used for: room
token issuance, authentication, session setup, configuration."). The
browser publishes microphone audio straight to LiveKit; CognitiveOS's own
VoiceCommandRuntime (kernel/edge/voice_command_runtime.py) subscribes to
that same room server-side -- this route never sees an audio byte.

Reuses api/routes/livekit.py's own create_livekit_room_token() directly
(not a second HTTP round-trip to that route) and the same require_permission
auth dependency every mutating route in this codebase already uses. The
participant identity returned to the caller is derived from the
AUTHENTICATED user_id, never client-supplied (section 8) -- a client cannot
ask to speak as anyone else's identity.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent
from src.monkey_brain.kernel.edge.livekit_adapter import (
    LiveKitUnavailableError,
    create_livekit_room_token,
)
from src.monkey_brain.kernel.edge.voice_command_runtime import VoiceCommandRuntime
from src.monkey_brain.kernel.edge.voice_session import get_voice_session_store

logger = logging.getLogger("agentos.gateway.voice")
router = APIRouter()

# session_id -> running bridge. Process-local, matching VoiceSessionStore's
# own scope (one CognitiveOS actor Pod == one process, see that module's
# docstring for why this needs no cross-process backend).
_runtimes: dict[str, VoiceCommandRuntime] = {}


class VoiceSessionCreateRequest(BaseModel):
    room: str
    actor_id: str
    ttl_seconds: int = 120


class VoiceSessionResponse(BaseModel):
    session_id: str
    room: str
    actor_id: str
    participant_identity: str
    status: str
    token: str


class VoiceSessionStatusResponse(BaseModel):
    session_id: str
    status: str
    last_transcript: str | None
    last_goal_text: str | None
    clarification_reason: str | None
    error: str | None


def _get_planetary_runtime(request: Request):
    return getattr(request.app.state, "planetary_runtime", None)


@router.post("/voice/sessions", response_model=VoiceSessionResponse, tags=["Voice"])
@idempotent("voice.create_session")
async def create_voice_session(
    body: VoiceSessionCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-action")),
) -> VoiceSessionResponse:
    """Start a voice session: CognitiveOS joins the given LiveKit room
    server-side as a SEPARATE, subscribe-only participant
    (VoiceCommandRuntime's own listener identity — never this one) and
    begins draining transcripts into the given actor's goal queue. The
    token returned here is for the CALLER's own browser to join and
    publish microphone audio, under the authenticated identity
    `voice-{user_id}` — never a client-supplied speaker identity."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    found = any(sr.get_actor(body.actor_id) is not None for sr in pr.all_societies())
    if not found:
        raise HTTPException(status_code=404, detail=f"Actor {body.actor_id} not found")

    participant_identity = f"voice-{user_id}"
    store = get_voice_session_store()
    session = store.create(
        room=body.room,
        actor_id=body.actor_id,
        participant_identity=participant_identity,
        created_by_user_id=user_id,
    )

    try:
        runtime = VoiceCommandRuntime(session, pr)
        runtime.start()
        _runtimes[session.session_id] = runtime
    except LiveKitUnavailableError as exc:
        store.remove(session.session_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        token = create_livekit_room_token(
            body.room, participant_identity, can_publish=True, can_subscribe=True, ttl_seconds=body.ttl_seconds
        )
    except (LiveKitUnavailableError, RuntimeError) as exc:
        await runtime.stop()
        _runtimes.pop(session.session_id, None)
        store.remove(session.session_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return VoiceSessionResponse(
        session_id=session.session_id,
        room=session.room,
        actor_id=session.actor_id,
        participant_identity=participant_identity,
        status=session.status,
        token=token,
    )


@router.get("/voice/sessions/{session_id}", response_model=VoiceSessionStatusResponse, tags=["Voice"])
async def get_voice_session_status(
    session_id: str,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> VoiceSessionStatusResponse:
    """Polled by the frontend panel (section 17) for connection/transcript/
    goal/status display — no websocket needed for this MVP's update
    cadence (transcripts land on a multi-second window already)."""
    session = get_voice_session_store().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="voice session not found")
    return VoiceSessionStatusResponse(
        session_id=session.session_id,
        status=session.status,
        last_transcript=session.last_transcript,
        last_goal_text=session.last_goal_text,
        clarification_reason=session.clarification_reason,
        error=session.error,
    )


@router.delete("/voice/sessions/{session_id}", tags=["Voice"])
@idempotent("voice.stop_session")
async def stop_voice_session(
    session_id: str,
    user_id: str = Depends(require_permission("perm-execute-action")),
) -> dict:
    runtime = _runtimes.pop(session_id, None)
    if runtime is not None:
        await runtime.stop()
    session = get_voice_session_store().remove(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="voice session not found")
    return {"session_id": session_id, "stopped": True}
