"""Video session model (CognitiveOS Drone Camera Video Telemetry, spec
section "VOICE SESSION MODEL" pattern carried over verbatim for video --
that section's own text is the camera task's precedent even though it was
written for voice). Sibling of kernel/edge/voice_session.py, same shape,
same "NOT a general-purpose conversation database" scope: one VideoSession
per (room, actor, camera track), process-local, in-memory only, holding
just enough state for the frontend panel and for POST /video/sessions's
caller to poll.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VideoSession:
    session_id: str
    room: str
    actor_id: str
    camera_track_name: str
    created_by_user_id: str
    created_at: float = field(default_factory=time.time)
    # connecting | streaming | degraded | stopped
    status: str = "connecting"
    stream_available: bool = False
    last_observation_attribute: str | None = None
    last_observation_value: Any = None
    last_observation_confidence: float | None = None
    error: str | None = None
    updated_at: float = field(default_factory=time.time)


class VideoSessionStore:
    """Process-local session registry — same scope justification as
    kernel/edge/voice_session.py::VoiceSessionStore (one CognitiveOS actor
    Pod hosts one actor and this session's own background task, no
    cross-process backend needed)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, VideoSession] = {}

    def create(self, *, room: str, actor_id: str, camera_track_name: str, created_by_user_id: str) -> VideoSession:
        session = VideoSession(
            session_id=uuid.uuid4().hex,
            room=room,
            actor_id=actor_id,
            camera_track_name=camera_track_name,
            created_by_user_id=created_by_user_id,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> VideoSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def update(self, session_id: str, **fields: Any) -> VideoSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            for key, value in fields.items():
                setattr(session, key, value)
            session.updated_at = time.time()
            return session

    def remove(self, session_id: str) -> VideoSession | None:
        with self._lock:
            return self._sessions.pop(session_id, None)


_STORE = VideoSessionStore()


def get_video_session_store() -> VideoSessionStore:
    return _STORE
