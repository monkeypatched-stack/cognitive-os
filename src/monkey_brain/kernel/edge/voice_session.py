"""Voice session model (CognitiveOS LiveKit Voice Command Integration,
spec section 13) -- the minimum state correlating a human, a LiveKit room,
and the CognitiveOS actor they're speaking to.

Deliberately NOT a general-purpose conversation database (spec section 13
is explicit about this): one VoiceSession per (room, actor) pair, in
process memory only, holding just enough state to answer "what is this
voice session doing right now" for the frontend panel (section 17) and for
POST /voice/sessions's caller. Nothing here is persisted or replayed across
a restart -- a voice session's own background listener task only ever
exists in the one process that started it, so there is nothing durable to
recover.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VoiceSession:
    session_id: str
    room: str
    actor_id: str
    participant_identity: str
    created_by_user_id: str
    created_at: float = field(default_factory=time.time)
    # listening | clarification_required | planning | idle | stopped
    status: str = "listening"
    last_transcript: str | None = None
    last_goal_text: str | None = None
    clarification_reason: str | None = None
    error: str | None = None
    updated_at: float = field(default_factory=time.time)


class VoiceSessionStore:
    """Process-local session registry -- one CognitiveOS actor Pod hosts
    one actor and needs no cross-process backend for this (contrast
    RunStore / the idempotency store, which DO need one: those survive a
    retry landing on a different uvicorn worker than the original request;
    a voice session's own background listener task has no such
    cross-worker existence to synchronize)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, VoiceSession] = {}

    def create(self, *, room: str, actor_id: str, participant_identity: str, created_by_user_id: str) -> VoiceSession:
        session = VoiceSession(
            session_id=uuid.uuid4().hex,
            room=room,
            actor_id=actor_id,
            participant_identity=participant_identity,
            created_by_user_id=created_by_user_id,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> VoiceSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def update(self, session_id: str, **fields: Any) -> VoiceSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            for key, value in fields.items():
                setattr(session, key, value)
            session.updated_at = time.time()
            return session

    def remove(self, session_id: str) -> VoiceSession | None:
        with self._lock:
            return self._sessions.pop(session_id, None)


_STORE = VoiceSessionStore()


def get_voice_session_store() -> VoiceSessionStore:
    return _STORE
