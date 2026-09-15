"""LiveKit room-token issuance (Layer 2: Realtime & Perception).

The only HTTP surface for kernel/edge/livekit_adapter.py — a client that
wants to join a LiveKit room (e.g. to talk to an actor) gets a short-TTL,
scope-bound token from here, same require_permission auth pattern
api/routes/approval.py already uses, rather than minting its own.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent
from src.monkey_brain.kernel.edge.livekit_adapter import (
    LiveKitUnavailableError,
    create_livekit_room_token,
)

logger = logging.getLogger("agentos.gateway.livekit")
router = APIRouter()


class LiveKitTokenRequest(BaseModel):
    room: str
    can_publish: bool = True
    can_subscribe: bool = True
    ttl_seconds: int = 120


class LiveKitTokenResponse(BaseModel):
    token: str
    room: str
    identity: str


@router.post("/livekit/token", tags=["LiveKit"])
@idempotent("livekit.issue_token")
async def issue_livekit_token(
    body: LiveKitTokenRequest,
    user_id: str = Depends(require_permission("perm-execute-action")),
) -> LiveKitTokenResponse:
    """Mint a room-join token for the authenticated caller. identity is the
    caller's own authenticated principal — never client-supplied — so a
    LiveKit room's participant list stays traceable to a real principal."""
    try:
        token = create_livekit_room_token(
            body.room,
            user_id,
            can_publish=body.can_publish,
            can_subscribe=body.can_subscribe,
            ttl_seconds=body.ttl_seconds,
        )
    except LiveKitUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return LiveKitTokenResponse(token=token, room=body.room, identity=user_id)
