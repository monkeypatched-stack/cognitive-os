"""Sensor Fusion — multi-modal observation fusion with confidence scoring.

Takes observations from different modalities (text, image, telemetry, etc.),
embeds each via the registry, and produces a fused state vector with per-modality
confidence scores.  The fused vector feeds into the world tensor for improved
next-action prediction.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.monkey_brain.kernel.plan.embedding.registry import (
    EmbeddingRegistry,
    build_default_registry,
)
from src.monkey_brain.kernel.plan.embedding.provider import Embedding

logger = logging.getLogger("agentos.sensor_fusion")


@dataclass
class ModalityObservation:
    """A single observation from one modality."""

    modality: str
    content: Any
    confidence: float = 0.0
    timestamp: float = 0.0
    source: str = ""

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class FusedState:
    """Result of multi-modal fusion."""

    vector: np.ndarray  # fused embedding vector
    modality_scores: dict[str, float] = field(default_factory=dict)  # per-modality confidence
    modalities_used: list[str] = field(default_factory=list)
    fusion_confidence: float = 0.0  # overall confidence
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class SensorFusion:
    """Multi-modal sensor fusion engine.

    Takes a list of ModalityObservation objects, embeds each via the
    appropriate provider, and fuses them into a single FusedState with
    confidence-weighted averaging.
    """

    def __init__(self, registry: EmbeddingRegistry | None = None):
        self._registry = registry or build_default_registry()

    def fuse(self, observations: list[ModalityObservation]) -> FusedState:
        """Fuse multiple modality observations into a single state vector.

        Uses confidence-weighted averaging: higher-confidence modalities
        contribute more to the fused vector.
        """
        if not observations:
            return FusedState(
                vector=np.zeros(32, dtype=np.float32),
                fusion_confidence=0.0,
            )

        embeddings: list[tuple[Embedding, float]] = []
        modality_scores: dict[str, float] = {}

        for obs in observations:
            try:
                item = type(
                    "Item",
                    (),
                    {
                        "content": obs.content,
                        "modality": obs.modality,
                    },
                )()
                embedding = self._registry.embed(item)

                # Compute effective confidence from modality priors + observation confidence
                from src.monkey_brain.kernel.plan.embedding._utils import EMBEDDING_DIM

                if embedding.vector.shape != (EMBEDDING_DIM,):
                    continue

                effective_conf = obs.confidence if obs.confidence > 0 else 0.5
                embeddings.append((embedding, effective_conf))
                modality_scores[obs.modality] = effective_conf
            except Exception as exc:
                logger.debug("Fusion: modality %s failed: %s", obs.modality, exc)

        if not embeddings:
            return FusedState(
                vector=np.zeros(32, dtype=np.float32),
                fusion_confidence=0.0,
            )

        # Confidence-weighted average
        total_weight = sum(c for _, c in embeddings)
        if total_weight <= 0:
            fused = np.mean([e.vector for e, _ in embeddings], axis=0)
        else:
            fused = sum(e.vector * c for e, c in embeddings) / total_weight

        # L2 normalize
        norm = np.linalg.norm(fused)
        if norm > 1e-8:
            fused = fused / norm

        fusion_conf = total_weight / len(embeddings)

        return FusedState(
            vector=fused.astype(np.float32),
            modality_scores=modality_scores,
            modalities_used=[obs.modality for obs in observations],
            fusion_confidence=round(fusion_conf, 4),
        )

    def fuse_from_dict(self, data: dict[str, Any]) -> FusedState:
        """Convenience: fuse from a dict of {modality: content} pairs."""
        observations = [ModalityObservation(modality=k, content=v) for k, v in data.items() if k and v is not None]
        return self.fuse(observations)
