"""LiveKit voice perception adapter (Layer 2: Realtime & Perception).

This module is intentionally optional: importing CognitiveOS does not
require the livekit/livekit-api packages — same "guarded import at
construction, not at module load" shape as px4_ros_adapter.py uses for
rclpy (see that module's own docstring for the rationale).

Two responsibilities, both real:
  - `LiveKitVoiceObservationProvider` implements kernel/pipeline/
    observations.py's `ObservationProvider` Protocol — the actor-side
    perception contract already existed (WorldPollingProvider was its only
    implementation before this). It joins a LiveKit room, buffers incoming
    audio in fixed windows, transcribes each window via the same Whisper
    primitive WhisperAudioEmbedder already uses (kernel/plan/embedding/
    audio.py) — reused directly here since this is a streaming observation,
    not the batch/retrieval embedding use case that module's own wrapper is
    shaped for — and emits one Observation per non-empty transcript.
  - `create_livekit_room_token()` mints a real LiveKit room-join token via
    the official livekit-api SDK (LiveKit's own server rejects anything not
    in its own signed JWT grant format — a home-grown token, even one
    shaped like kernel/security_boundary.py's, would not work here). The
    short-TTL, scoped-claims, spiffe-style-identity shape still follows
    domains/.../services/auth/helpers/agent_tokens.py::create_pipeline_token
    — that's the pattern being mirrored, not the wire format.

MVP scope (see the gap-fixing plan this was built from): one voice
ObservationProvider, fixed-window transcription (not true VAD-based
utterance detection), single audio track per room. Multi-track/video and
composing this with other ObservationProviders for the same actor are
explicitly out of scope here.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta
import threading
import time
from typing import Any

from src.monkey_brain.kernel.pipeline.observations import (
    Observation,
    ObservationSet,
    Provenance,
)

logger = logging.getLogger("agentos.edge.livekit")

LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")

_AUDIO_WINDOW_SECONDS = 4.0
_TARGET_SAMPLE_RATE = 16000


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
    live LiveKit room's audio.

    `observe()` itself must stay synchronous and non-blocking to satisfy
    the Protocol (matching WorldPollingProvider's contract) — the actual
    room connection and transcription run in a background asyncio task
    started by `start()`; `observe()` only drains whatever transcripts that
    background task has already produced since the last call.
    """

    def __init__(
        self, room_name: str, *, participant_identity: str = "cognitiveos-listener", whisper_model: str = "tiny"
    ) -> None:
        try:
            from livekit import rtc  # noqa: F401  (import-availability check only)
        except ImportError as exc:
            raise LiveKitUnavailableError(
                "LiveKitVoiceObservationProvider requires the livekit package (pyproject.toml's 'livekit' extra)"
            ) from exc

        self._room_name = room_name
        self._participant_identity = participant_identity
        self._whisper_model_size = whisper_model
        self._whisper_model: Any = None
        self._buffer: list[Observation] = []
        self._lock = threading.Lock()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Launch the background room listener on the CURRENT running event
        loop. Must be called from async startup code (e.g. when an actor's
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

    async def _run(self) -> None:
        from livekit import rtc

        if not LIVEKIT_URL:
            logger.error("LIVEKIT_URL not set — LiveKitVoiceObservationProvider cannot connect")
            return

        token = create_livekit_room_token(self._room_name, self._participant_identity, can_publish=False)
        room = rtc.Room()

        @room.on("track_subscribed")
        def _on_track_subscribed(track: Any, publication: Any, participant: Any) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.ensure_future(self._consume_audio_track(track, participant))

        try:
            await room.connect(LIVEKIT_URL, token)
            logger.info("LiveKitVoiceObservationProvider connected to room %r", self._room_name)
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("LiveKitVoiceObservationProvider room connection failed")
        finally:
            await room.disconnect()

    async def _consume_audio_track(self, track: Any, participant: Any) -> None:
        from livekit import rtc

        audio_stream = rtc.AudioStream(track)
        frames: list[Any] = []
        window_started = time.monotonic()

        try:
            async for event in audio_stream:
                frame = event.frame
                frames.append(frame)
                if time.monotonic() - window_started >= _AUDIO_WINDOW_SECONDS:
                    await self._transcribe_window(frames, participant)
                    frames = []
                    window_started = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "LiveKitVoiceObservationProvider audio consumption failed for participant %r",
                getattr(participant, "identity", "?"),
            )

    async def _transcribe_window(self, frames: list[Any], participant: Any) -> None:
        if not frames:
            return
        try:
            waveform = _frames_to_waveform(frames, _TARGET_SAMPLE_RATE)
            if waveform is None or waveform.size == 0:
                return

            loop = asyncio.get_running_loop()
            transcript = await loop.run_in_executor(None, self._transcribe_sync, waveform)
            transcript = (transcript or "").strip()
            if not transcript:
                return

            observation = Observation(
                entity=getattr(participant, "identity", self._participant_identity),
                attribute="voice_transcript",
                value=transcript,
                confidence=0.7,
                provenance=Provenance(source="livekit_voice", method="whisper_transcribe", reliability=0.7),
            )
            with self._lock:
                self._buffer.append(observation)
        except Exception:
            logger.exception("LiveKitVoiceObservationProvider transcription failed")

    def _transcribe_sync(self, waveform: Any) -> str:
        """Runs in a worker thread (via run_in_executor) — Whisper's
        transcribe() is a blocking call, same as WhisperAudioEmbedder's own
        usage (kernel/plan/embedding/audio.py)."""
        import whisper

        if self._whisper_model is None:
            self._whisper_model = whisper.load_model(self._whisper_model_size)
        result = self._whisper_model.transcribe(waveform)
        return str(result.get("text", ""))

    def observe(self, actor_id: str, world: Any) -> ObservationSet:
        """Never raises — matches WorldPollingProvider's contract. Drains
        whatever transcripts the background room listener has produced
        since the last call; empty when nothing was said, the room isn't
        connected yet, or livekit is unreachable."""
        try:
            with self._lock:
                observations = tuple(self._buffer)
                self._buffer.clear()
        except Exception:
            logger.debug("observe: suppressed exception", exc_info=True)
            observations = ()
        return ObservationSet(observations=observations, actor_id=actor_id)


def _frames_to_waveform(frames: list[Any], target_sr: int) -> Any:
    """Concatenate LiveKit AudioFrame.data (int16 PCM) into one mono
    float32 waveform, resampled to target_sr — same target_sr convention
    kernel/plan/embedding/audio.py::_load_waveform uses."""
    import numpy as np

    chunks = []
    source_sr = target_sr
    for frame in frames:
        data = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
        if getattr(frame, "num_channels", 1) > 1:
            data = data.reshape(-1, frame.num_channels).mean(axis=1)
        chunks.append(data)
        source_sr = getattr(frame, "sample_rate", target_sr)

    if not chunks:
        return None
    waveform = np.concatenate(chunks)

    if source_sr != target_sr:
        try:
            import resampy

            waveform = resampy.resample(waveform, source_sr, target_sr)
        except ImportError:
            logger.debug("resampy not available — transcribing at source sample rate %d", source_sr)

    return waveform
