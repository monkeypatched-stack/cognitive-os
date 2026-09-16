"""LiveKit voice perception adapter (Layer 2: Realtime & Perception).

This module is intentionally optional: importing CognitiveOS does not
require the livekit/livekit-api packages — same "guarded import at
construction, not at module load" shape as px4_ros_adapter.py uses for
rclpy (see that module's own docstring for the rationale).

Two responsibilities, both real:
  - `LiveKitVoiceObservationProvider` implements kernel/pipeline/
    observations.py's `ObservationProvider` Protocol — the actor-side
    perception contract already existed (WorldPollingProvider was its only
    implementation before this). It does NOT join the room or run Whisper
    itself anymore — it's an HTTP client to a separate native macOS
    process, scripts/voice_service/transcription_service.py, which does
    the real work (LiveKit room join, Silero VAD utterance segmentation,
    mlx-whisper inference on Metal). Moved out-of-process because Docker
    Desktop's Linux VM has no GPU passthrough to Apple's Metal/MPS at all
    — mlx-whisper's "medium" model needs it to be fast, same reason
    llama-server (this pipeline's LLM planner backend) also runs natively
    on the Mac rather than as a k8s sidecar. This class emits one
    Observation per non-empty transcript the remote service reports.
  - `create_livekit_room_token()` mints a real LiveKit room-join token via
    the official livekit-api SDK (LiveKit's own server rejects anything not
    in its own signed JWT grant format — a home-grown token, even one
    shaped like kernel/security_boundary.py's, would not work here). The
    short-TTL, scoped-claims, spiffe-style-identity shape still follows
    domains/.../services/auth/helpers/agent_tokens.py::create_pipeline_token
    — that's the pattern being mirrored, not the wire format. Still used
    for the browser's own join token (api/routes/voice.py) and by the
    video adapter — the remote transcription service mints its own
    listener-participant token directly against LIVEKIT_API_KEY/SECRET,
    since it isn't this process and can't call this function.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta
import threading
from typing import Any

from src.introspection.otel_bridge import get_bridge
from src.monkey_brain.kernel.pipeline.observations import (
    Observation,
    ObservationSet,
    Provenance,
)

logger = logging.getLogger("agentos.edge.livekit")

LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")

# scripts/voice_service/transcription_service.py's own base URL -- a
# native macOS process (host.docker.internal from inside this pod), not
# another in-cluster Service. See that module's own docstring for why.
VOICE_TRANSCRIPTION_SERVICE_URL = os.environ.get("VOICE_TRANSCRIPTION_SERVICE_URL", "")

# How often this class polls the remote service for freshly finished
# utterances -- the remote service itself decides utterance boundaries via
# Silero VAD, this is just the drain cadence, so it only needs to be
# reasonably prompt, not aligned to any fixed window anymore.
_REMOTE_POLL_INTERVAL_SECONDS = 1.0


class LiveKitUnavailableError(RuntimeError):
    """Raised at construction when the livekit/livekit-api packages are not
    installed — see the `livekit` optional extra in pyproject.toml."""


def create_livekit_room_token(
    room: str,
    participant_identity: str,
    *,
    can_publish: bool = True,
    can_subscribe: bool = True,
    ttl_seconds: int = 120,
) -> str:
    """Mint a short-TTL, scope-bound LiveKit room-join token.

    Requires LIVEKIT_API_KEY/LIVEKIT_API_SECRET (the LiveKit project's own
    credentials, distinct from AGENTOS_MASTER_KEY — this token is verified
    by the LiveKit server, not by this codebase).
    """
    try:
        from livekit import api
    except ImportError as exc:
        raise LiveKitUnavailableError(
            "create_livekit_room_token requires the livekit-api package (pyproject.toml's 'livekit' extra)"
        ) from exc

    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise RuntimeError("LIVEKIT_API_KEY / LIVEKIT_API_SECRET must be set to mint a LiveKit room token")

    grants = api.VideoGrants(room_join=True, room=room, can_publish=can_publish, can_subscribe=can_subscribe)
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(participant_identity)
        .with_grants(grants)
        .with_ttl(timedelta(seconds=ttl_seconds))
    )
    return token.to_jwt()


class LiveKitVoiceObservationProvider:
    """ObservationProvider (kernel/pipeline/observations.py) backed by a
    live LiveKit room's audio, transcribed by a separate native macOS
    process (scripts/voice_service/transcription_service.py) reached over
    HTTP -- see this module's own docstring for why that's a separate
    process rather than in-process Whisper here.

    `observe()` itself must stay synchronous and non-blocking to satisfy
    the Protocol (matching WorldPollingProvider's contract) — the actual
    remote-session lifecycle and polling run in a background asyncio task
    started by `start()`; `observe()` only drains whatever transcripts that
    background task has already pulled from the remote service since the
    last call.
    """

    def __init__(self, room_name: str, *, participant_identity: str = "cognitiveos-listener") -> None:
        self._room_name = room_name
        self._participant_identity = participant_identity
        self._buffer: list[Observation] = []
        self._lock = threading.Lock()
        self._task: asyncio.Task | None = None
        self._remote_session_id: str | None = None

    def start(self) -> None:
        """Launch the background poller on the CURRENT running event loop.
        Must be called from async startup code (e.g. when an actor's
        CognitiveRuntime is constructed inside an async route handler) —
        never raises; a failure to start just means observe() stays empty."""
        if self._task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "LiveKitVoiceObservationProvider.start() called outside a running event loop — voice capture will not run"
            )
            return
        self._task = loop.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._remote_session_id is not None and VOICE_TRANSCRIPTION_SERVICE_URL:
            import httpx

            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.delete(f"{VOICE_TRANSCRIPTION_SERVICE_URL}/sessions/{self._remote_session_id}")
            except Exception:
                logger.debug("LiveKitVoiceObservationProvider: remote session cleanup failed", exc_info=True)
            self._remote_session_id = None

    async def _run(self) -> None:
        if not VOICE_TRANSCRIPTION_SERVICE_URL:
            logger.error(
                "VOICE_TRANSCRIPTION_SERVICE_URL not set — LiveKitVoiceObservationProvider cannot start a remote session"
            )
            return

        import httpx

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{VOICE_TRANSCRIPTION_SERVICE_URL}/sessions",
                    json={"room": self._room_name, "participant_identity": self._participant_identity},
                )
                resp.raise_for_status()
                self._remote_session_id = resp.json()["session_id"]
            logger.info(
                "LiveKitVoiceObservationProvider started remote transcription session %s for room %r",
                self._remote_session_id,
                self._room_name,
            )
            while True:
                await asyncio.sleep(_REMOTE_POLL_INTERVAL_SECONDS)
                await self._poll_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("LiveKitVoiceObservationProvider: failed to start remote transcription session")

    async def _poll_once(self) -> None:
        if self._remote_session_id is None:
            return
        import httpx

        # voice.transcription (spec's own event name) -- one span per poll
        # that actually finds new transcripts, not per audio frame/window
        # anymore (utterance boundaries are the remote service's own Silero
        # VAD decision now, not a fixed timer here). No raw audio or
        # transcript text in the span, only counts/lengths, matching
        # "don't put sensitive raw prompts/transcripts into telemetry by
        # default".
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{VOICE_TRANSCRIPTION_SERVICE_URL}/sessions/{self._remote_session_id}/transcripts"
                )
                resp.raise_for_status()
                transcripts = resp.json().get("transcripts", [])
        except Exception:
            # Transient poll failure -- never surfaced as a crash, matches
            # this class's pre-existing "never raise" contract for its
            # background task. The next poll tries again.
            logger.debug("LiveKitVoiceObservationProvider: poll failed", exc_info=True)
            return

        if not transcripts:
            return

        with get_bridge().span(
            "voice.transcription",
            layer="realtime",
            participant=self._participant_identity,
        ) as span:
            span.set_attribute("transcript_count", len(transcripts))
            with self._lock:
                for item in transcripts:
                    text = (item.get("text") or "").strip()
                    if not text:
                        continue
                    self._buffer.append(
                        Observation(
                            entity=self._participant_identity,
                            attribute="voice_transcript",
                            value=text,
                            confidence=0.7,
                            provenance=Provenance(
                                source="livekit_voice", method="mlx_whisper_transcribe", reliability=0.7
                            ),
                        )
                    )

    def observe(self, actor_id: str, world: Any) -> ObservationSet:
        """Never raises — matches WorldPollingProvider's contract. Drains
        whatever transcripts the background poller has pulled from the
        remote transcription service since the last call; empty when
        nothing was said, the remote session isn't up yet, or that service
        is unreachable."""
        try:
            with self._lock:
                observations = tuple(self._buffer)
                self._buffer.clear()
        except Exception:
            logger.debug("observe: suppressed exception", exc_info=True)
            observations = ()
        return ObservationSet(observations=observations, actor_id=actor_id)
