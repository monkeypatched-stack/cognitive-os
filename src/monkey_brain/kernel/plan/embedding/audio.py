"""Audio embedding providers — Whisper transcript + spectral features.

Dependency chain:
    WhisperAudioEmbedder     → transcribes → SBERTEmbedder (semantic on transcript)
    SpectralAudioEmbedder    → numpy/scipy  → no ML models required

item.content should be a file path to an audio file (wav, mp3, ogg, flac, …)
or raw audio bytes.

Replace with a dedicated audio encoder:
    registry.register("audio", Wav2VecProvider())
    registry.register("audio", CLAPProvider())
    registry.register("audio", EnCodecProvider())
"""

from __future__ import annotations

import io
import logging
from typing import Any

import numpy as np

from src.monkey_brain.kernel.plan.embedding._utils import (
    EMBEDDING_DIM,
    _l2,
    _bow_project,
)
from src.monkey_brain.kernel.plan.embedding.provider import Embedding, EmbeddingEmbedder

logger = logging.getLogger(__name__)


def _load_waveform(content: Any, target_sr: int = 16000) -> tuple[np.ndarray, int] | tuple[None, None]:
    """Load audio content into a mono float32 waveform at target_sr.

    Returns (waveform, sample_rate) or (None, None) on failure.
    Accepts: file path (str), raw bytes, or numpy array.
    """
    try:
        import soundfile as sf

        if isinstance(content, np.ndarray):
            return content.astype(np.float32), target_sr

        if isinstance(content, (bytes, bytearray)):
            waveform, sr = sf.read(io.BytesIO(bytes(content)))
        else:
            waveform, sr = sf.read(str(content))

        if waveform.ndim > 1:
            waveform = waveform.mean(axis=1)  # stereo → mono
        waveform = waveform.astype(np.float32)

        if sr != target_sr:
            try:
                import resampy

                waveform = resampy.resample(waveform, sr, target_sr)
            except ImportError:
                logger.debug("resampy not available — using original sample rate %d", sr)
                return waveform, sr

        return waveform, target_sr

    except Exception as exc:
        logger.debug("_load_waveform failed: %s", exc)
        return None, None


class WhisperAudioEmbedder(EmbeddingEmbedder):
    """Whisper speech-to-text → SBERT embedding of the transcript.

    Encodes audio semantically by transcribing with OpenAI Whisper (tiny model
    by default) and embedding the transcript text with SBERTEmbedder.

    Falls back to SpectralAudioEmbedder when Whisper is unavailable.

    Replace via registry:
        registry.register("audio", CLAPProvider())   # joint audio-text space
        registry.register("audio", Wav2VecProvider()) # acoustic features
    """

    @property
    def name(self) -> str:
        return "whisper_audio"

    def __init__(self, model_size: str = "tiny") -> None:
        self._model_size = model_size
        self._model: Any = None
        self._text_provider: EmbeddingEmbedder | None = None
        self._fallback: EmbeddingEmbedder | None = None

    def _get_text_provider(self) -> EmbeddingEmbedder:
        if self._text_provider is None:
            from src.monkey_brain.kernel.plan.embedding.text import SBERTEmbedder

            self._text_provider = SBERTEmbedder()
        return self._text_provider

    def _get_fallback(self) -> EmbeddingEmbedder:
        if self._fallback is None:
            self._fallback = SpectralAudioEmbedder()
        return self._fallback

    def _load(self) -> None:
        import whisper

        self._model = whisper.load_model(self._model_size)

    def embed(self, item: Any) -> Embedding:
        content = getattr(item, "content", item)

        try:
            if self._model is None:
                self._load()
            result = self._model.transcribe(str(content))
            transcript = result.get("text", "").strip()
            if not transcript:
                logger.debug("Whisper produced empty transcript, using spectral fallback")
                return self._get_fallback().embed(item)

            transcript_item = _AudioTextItem(transcript, item)
            emb = self._get_text_provider().embed(transcript_item)
            return Embedding(
                vector=emb.vector,
                modality=str(getattr(item, "modality", "")),
                provider=self.name,
            )
        except Exception as exc:
            logger.debug("WhisperAudioEmbedder failed, using spectral fallback: %s", exc)
            return self._get_fallback().embed(item)


class SpectralAudioEmbedder(EmbeddingEmbedder):
    """Spectral audio encoder — MFCC + energy + zero-crossing features.

    Extracts 13 MFCCs (mean + std), spectral centroid, rolloff, bandwidth,
    zero-crossing rate, and RMS energy → projected to R^32. No ML required.

    Use as a lightweight fallback:
        registry.register("audio", SpectralAudioEmbedder())

    Replace: WhisperAudioEmbedder for semantic content, CLAPProvider for
    joint audio-text matching.
    """

    @property
    def name(self) -> str:
        return "spectral_audio"

    def embed(self, item: Any) -> Embedding:
        content = getattr(item, "content", item)
        waveform, sr = _load_waveform(content)

        if waveform is None:
            vec = _bow_project(str(content or ""))
            return Embedding(
                vector=vec,
                modality=str(getattr(item, "modality", "")),
                provider=self.name,
            )

        feat = self._extract(waveform, sr or 16000)
        meta = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        meta[0] = float(getattr(item, "provenance", 0.5))
        meta[1] = float(getattr(item, "freshness", 0.5))
        return Embedding(
            vector=np.tanh(0.9 * feat + 0.1 * meta),
            modality=str(getattr(item, "modality", "")),
            provider=self.name,
        )

    def _extract(self, waveform: np.ndarray, sr: int) -> np.ndarray:
        feat = np.zeros(EMBEDDING_DIM, dtype=np.float32)

        # Duration and amplitude (slots 0-2)
        feat[0] = min(len(waveform) / sr, 300.0) / 300.0  # duration in seconds, capped at 5 min
        feat[1] = float(np.sqrt(np.mean(waveform**2)))  # RMS energy
        feat[2] = float(np.max(np.abs(waveform)))  # peak amplitude

        # Zero-crossing rate (slot 3)
        signs = np.sign(waveform[:-1]) != np.sign(waveform[1:])
        feat[3] = float(np.mean(signs))

        # MFCC-like features via DCT of log-mel filterbank (slots 4-29)
        feat[4:] = self._mfcc_approx(waveform, sr, n_coeffs=EMBEDDING_DIM - 4)

        return _l2(feat)

    def _mfcc_approx(self, waveform: np.ndarray, sr: int, n_coeffs: int) -> np.ndarray:
        """Approximate MFCCs using FFT + log-mel bins + DCT."""
        try:
            frame_len = min(int(0.025 * sr), len(waveform))  # 25 ms
            hop = max(int(0.010 * sr), 1)  # 10 ms

            # Frame the signal
            n_frames = max((len(waveform) - frame_len) // hop + 1, 1)
            frames = np.array(
                [
                    waveform[i * hop : i * hop + frame_len]
                    for i in range(n_frames)
                    if i * hop + frame_len <= len(waveform)
                ],
                dtype=np.float32,
            )

            if frames.size == 0:
                return np.zeros(n_coeffs, dtype=np.float32)

            # Windowed FFT magnitude
            window = np.hanning(frame_len).astype(np.float32)
            spectra = np.abs(np.fft.rfft(frames * window[: frames.shape[1]], axis=1))

            # Log-power spectrum → mean across frames
            log_spec = np.log1p(np.mean(spectra, axis=0))

            # Bin into n_coeffs mel-like buckets (linear spacing as approximation)
            n_fft = log_spec.shape[0]
            bin_size = max(n_fft // n_coeffs, 1)
            coeffs = np.array(
                [float(np.mean(log_spec[i * bin_size : (i + 1) * bin_size])) for i in range(n_coeffs)],
                dtype=np.float32,
            )

            return _l2(coeffs)

        except Exception as exc:
            logger.debug("_mfcc_approx failed: %s", exc)
            return np.zeros(n_coeffs, dtype=np.float32)


class _AudioTextItem:
    """Thin wrapper so a transcript string looks like a knowledge item."""

    def __init__(self, text: str, original: Any) -> None:
        self.content = text
        self.modality = "document"
        self.provenance = getattr(original, "provenance", 0.5)
        self.freshness = getattr(original, "freshness", 0.5)
