"""Telemetry, sensor, and simulation embedding providers."""

from __future__ import annotations

import re as _re
from typing import Any

import numpy as np

from src.monkey_brain.kernel.plan.embedding._utils import (
    EMBEDDING_DIM,
    _l2,
    _bow_project,
    _parse_numbers,
    _ts_features,
)
from src.monkey_brain.kernel.plan.embedding.provider import Embedding, EmbeddingEmbedder


class TelemetryEmbedder(EmbeddingEmbedder):
    """Statistical time-series features.

    Produces different embeddings for flat / rising / oscillating / spike series.
    Replace: Chronos / PatchTST / Moirai.
    """

    @property
    def name(self) -> str:
        return "telemetry"

    def embed(self, item: Any) -> Embedding:
        content = str(getattr(item, "content", item) or "")
        arr = _parse_numbers(content)
        feat = _ts_features(arr) if arr.size > 0 else np.zeros(EMBEDDING_DIM, dtype=np.float32)
        feat[20] = float((getattr(item, "provenance", 0.85) + 1.0 - getattr(item, "uncertainty", 0.15)) / 2)
        feat[21] = float(getattr(item, "freshness", 0.5))
        feat[22] = float(getattr(item, "provenance", 0.85))
        feat[23] = float(getattr(item, "uncertainty", 0.0))
        return Embedding(
            vector=_l2(np.tanh(feat)),
            modality=str(getattr(item, "modality", "")),
            provider=self.name,
        )


class SensorEmbedder(TelemetryEmbedder):
    """Sensor stream provider — same feature extractor as telemetry.

    Replace: sensor-specific encoder (e.g. vibration FFT, pressure waveform).
    """

    @property
    def name(self) -> str:
        return "sensor"


class SimulationEmbedder(EmbeddingEmbedder):
    """Simulation result encoder — statistical + convergence signals.

    Captures numeric output distributions and convergence / divergence markers.
    Replace: domain-specific simulation result transformer.
    """

    @property
    def name(self) -> str:
        return "simulation"

    def embed(self, item: Any) -> Embedding:
        content = str(getattr(item, "content", item) or "")
        arr = _parse_numbers(content)
        feat = _ts_features(arr) if arr.size > 0 else _bow_project(content)
        feat[16] = 1.0 if _re.search(r"\bconverg", content, _re.I) else 0.0
        feat[17] = 1.0 if _re.search(r"\bdiverg|unstab", content, _re.I) else 0.0
        feat[18] = float((getattr(item, "provenance", 0.6) + 1.0 - getattr(item, "uncertainty", 0.4)) / 2)
        return Embedding(
            vector=_l2(np.tanh(feat)),
            modality=str(getattr(item, "modality", "")),
            provider=self.name,
        )
