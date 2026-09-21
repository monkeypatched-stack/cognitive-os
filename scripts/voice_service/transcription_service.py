"""Native macOS voice transcription service.

Runs OUTSIDE any container so it can reach Apple's Metal/MPS GPU via
mlx-whisper -- Docker Desktop's Linux VM has no GPU passthrough at all,
same reason llama-server (kernel/pipeline/llm_planner.py's own backend)
runs natively on this Mac instead of as a k8s sidecar, not in-cluster.

agentos's own LiveKitVoiceObservationProvider (kernel/edge/
livekit_adapter.py) is a thin HTTP client to this service now, instead of
joining LiveKit and running Whisper in-process itself. This service does
the real work:

  1. Joins the LiveKit room as a subscribe-only participant (mirrors what
     LiveKitVoiceObservationProvider used to do directly).
  2. Runs Silero VAD in streaming mode (VADIterator) over 512-sample
     chunks to find real utterance boundaries -- speech onset to a real
     pause -- instead of the old fixed 4-second window, which routinely
     cut mid-word ("waypoint Alpha" -> "we point 1") and fed Whisper
     windows that were mostly silence.
  3. Transcribes each finished utterance with mlx-whisper's medium model
     on Metal (temperature=0.0, same reasoning as the old CPU
     implementation: skips the multi-temperature fallback loop where a
     real upstream Whisper bug lives on marginal audio).

Usage:
    LIVEKIT_URL=wss://your-project.livekit.cloud \\
    LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... \\
        .venv/bin/python transcription_service.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import mlx_whisper
import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from livekit import api, rtc
from pydantic import BaseModel
from silero_vad import VADIterator, load_silero_vad

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("voice_transcription_service")

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
LIVEKIT_API_KEY = os.environ["LIVEKIT_API_KEY"]
LIVEKIT_API_SECRET = os.environ["LIVEKIT_API_SECRET"]

# mlx-community/whisper-medium-mlx is the full fp16 multilingual MLX
# conversion -- matches "medium" as asked, not a quantized or .en variant.
# Override via env if you want whisper-medium.en-mlx (faster, English-only)
# or a quantized (-4bit/-8bit) build instead.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL_REPO", "mlx-community/whisper-medium-mlx")

SAMPLE_RATE = 16000
# Silero VAD's streaming API requires EXACTLY this chunk size at 16kHz --
# not a tunable, a hard model requirement (256 samples at 8kHz).
VAD_CHUNK_SAMPLES = 512
VAD_THRESHOLD = float(os.environ.get("VAD_THRESHOLD", "0.5"))
# 600ms, not Silero's own 100ms default -- 100ms cuts on an ordinary
# mid-sentence breath, not a real end-of-utterance pause. Tune down if
# commands start feeling laggy to finalize, up if mid-sentence pauses
# still split one command into two utterances.
VAD_MIN_SILENCE_MS = int(os.environ.get("VAD_MIN_SILENCE_MS", "600"))
VAD_SPEECH_PAD_MS = int(os.environ.get("VAD_SPEECH_PAD_MS", "200"))
# Safety valve, not a normal path: forces a cut so one long, mis-detected
# "still speaking" state can't grow the buffer unboundedly.
MAX_UTTERANCE_SECONDS = float(os.environ.get("MAX_UTTERANCE_SECONDS", "20"))
MIN_UTTERANCE_SECONDS = 0.2

_vad_model = load_silero_vad()
logger.info("Silero VAD model loaded")


@dataclass
class Transcript:
    text: str
    at: float


@dataclass
class Session:
    session_id: str
    room_name: str
    participant_identity: str
    room: rtc.Room = field(default_factory=rtc.Room)
    transcripts: list[Transcript] = field(default_factory=list)
    task: asyncio.Task | None = None


_sessions: dict[str, Session] = {}


def _mint_token(room: str, identity: str) -> str:
    grants = api.VideoGrants(room_join=True, room=room, can_publish=False, can_subscribe=True)
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_ttl(timedelta(hours=6))
        .with_grants(grants)
    )
    return token.to_jwt()


def _transcribe_sync(waveform: np.ndarray) -> str:
    try:
        import mlx.core as mx
        device = mx.gpu if mx.metal.is_available() else mx.cpu
        mx.set_default_device(device)
    except Exception as exc:
        logger.debug("mlx stream setup: %s", exc)
    result = mlx_whisper.transcribe(waveform, path_or_hf_repo=WHISPER_MODEL, temperature=0.0)
    return str(result.get("text") or "")


async def _finalize_utterance(session: Session, chunks: list[np.ndarray]) -> None:
    if not chunks:
        return
    pcm = np.concatenate(chunks)
    duration_s = len(pcm) / SAMPLE_RATE
    if duration_s < MIN_UTTERANCE_SECONDS:
        return
    waveform = pcm.astype(np.float32) / 32768.0
    loop = asyncio.get_running_loop()
    try:
        text = await loop.run_in_executor(None, _transcribe_sync, waveform)
    except Exception:
        logger.exception("session %s: mlx-whisper transcription failed", session.session_id)
        return
    text = text.strip()
    if not text:
        return
    session.transcripts.append(Transcript(text=text, at=time.time()))
    logger.info("session %s: transcript=%r (%.1fs)", session.session_id, text, duration_s)


async def _consume_track(session: Session, track: rtc.Track) -> None:
    logger.info("session %s: starting audio consumption for track %s", session.session_id, track.sid)
    audio_stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
    vad_iterator = VADIterator(
        _vad_model,
        threshold=VAD_THRESHOLD,
        sampling_rate=SAMPLE_RATE,
        min_silence_duration_ms=VAD_MIN_SILENCE_MS,
        speech_pad_ms=VAD_SPEECH_PAD_MS,
    )
    pcm_buffer = np.zeros(0, dtype=np.int16)
    utterance_chunks: list[np.ndarray] = []
    in_speech = False
    utterance_started_at = 0.0

    try:
        async for event in audio_stream:
            frame = event.frame
            samples = np.frombuffer(frame.data, dtype=np.int16)
            pcm_buffer = np.concatenate([pcm_buffer, samples])

            while len(pcm_buffer) >= VAD_CHUNK_SAMPLES:
                chunk = pcm_buffer[:VAD_CHUNK_SAMPLES].copy()
                pcm_buffer = pcm_buffer[VAD_CHUNK_SAMPLES:]
                chunk_tensor = torch.from_numpy(chunk.astype(np.float32) / 32768.0)
                vad_event = vad_iterator(chunk_tensor)

                if in_speech:
                    utterance_chunks.append(chunk)

                if vad_event is not None and "start" in vad_event and not in_speech:
                    in_speech = True
                    utterance_started_at = time.monotonic()
                    utterance_chunks = [chunk]
                    logger.info("session %s: speech onset detected", session.session_id)
                elif vad_event is not None and "end" in vad_event and in_speech:
                    in_speech = False
                    finished, utterance_chunks = utterance_chunks, []
                    vad_iterator.reset_states()
                    logger.info("session %s: speech end detected (%d chunks)", session.session_id, len(finished))
                    asyncio.ensure_future(_finalize_utterance(session, finished))
                elif in_speech and (time.monotonic() - utterance_started_at) > MAX_UTTERANCE_SECONDS:
                    in_speech = False
                    logger.info("session %s: max utterance length reached, cutting", session.session_id)
                    finished, utterance_chunks = utterance_chunks, []
                    vad_iterator.reset_states()
                    asyncio.ensure_future(_finalize_utterance(session, finished))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("session %s: audio consumption failed", session.session_id)


async def _run_session(session: Session) -> None:
    try:
        @session.room.on("track_subscribed")
        def _on_track_subscribed(track: Any, publication: Any, participant: Any) -> None:
            logger.info("session %s: track_subscribed kind=%s from %s", session.session_id, track.kind, getattr(participant, 'identity', 'unknown'))
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.ensure_future(_consume_track(session, track))

        token = _mint_token(session.room_name, session.participant_identity)
        await session.room.connect(LIVEKIT_URL, token)
        logger.info("session %s: connected to room %r", session.session_id, session.room_name)

        # Consume any audio tracks from remote participants already in the room
        for participant in session.room.remote_participants.values():
            for pub in participant.track_publications.values():
                if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    logger.info("session %s: consuming existing audio track from %s", session.session_id, participant.identity)
                    asyncio.ensure_future(_consume_track(session, pub.track))

        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("session %s: room connection failed", session.session_id)


app = FastAPI(title="voice-transcription-service")


class StartSessionRequest(BaseModel):
    room: str
    participant_identity: str


class StartSessionResponse(BaseModel):
    session_id: str


class TranscriptOut(BaseModel):
    text: str
    at: float


class TranscriptsResponse(BaseModel):
    transcripts: list[TranscriptOut]


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "model": WHISPER_MODEL, "sessions": len(_sessions)}


@app.post("/sessions", response_model=StartSessionResponse)
async def start_session(body: StartSessionRequest) -> StartSessionResponse:
    session_id = uuid.uuid4().hex
    session = Session(session_id=session_id, room_name=body.room, participant_identity=body.participant_identity)
    _sessions[session_id] = session
    session.task = asyncio.create_task(_run_session(session))
    return StartSessionResponse(session_id=session_id)


@app.get("/sessions/{session_id}/transcripts", response_model=TranscriptsResponse)
async def get_transcripts(session_id: str) -> TranscriptsResponse:
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    drained, session.transcripts = session.transcripts, []
    return TranscriptsResponse(transcripts=[TranscriptOut(text=t.text, at=t.at) for t in drained])


@app.delete("/sessions/{session_id}")
async def stop_session(session_id: str) -> dict[str, str]:
    session = _sessions.pop(session_id, None)
    if session is None:
        return {"status": "not_found"}
    if session.task is not None:
        session.task.cancel()
    await session.room.disconnect()
    return {"status": "stopped"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8091"))
    uvicorn.run(app, host="0.0.0.0", port=port)
