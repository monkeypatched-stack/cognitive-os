"""EmbeddingRegistry — maps modality string to EmbeddingEmbedder.

The registry is the single point of configuration for the embedding layer.
Swapping a provider for any modality is a one-liner:

    from monkey_brain.kernel.embedding import get_registry
    from my_models import ChronosProvider
    get_registry().register("telemetry", ChronosProvider())

build_default_registry() wires the production defaults:
    - SBERT for text modalities (document, manual)
    - CLIP text encoder for vision modalities (image, video)
    - Feature-engineering encoders for structured modalities

All paths ultimately fall back to HashEmbeddingEmbedder if ML models
are unavailable (e.g., in CI without model weights).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np

from src.monkey_brain.kernel.plan.embedding._utils import EMBEDDING_DIM
from src.monkey_brain.kernel.plan.embedding.provider import (
    Embedding,
    EmbeddingEmbedder,
    HashEmbeddingEmbedder,
)

logger = logging.getLogger(__name__)


class FunctionBackedEmbedder(EmbeddingEmbedder):
    """Wraps a plain function (item → np.ndarray) as an EmbeddingEmbedder.

    Used by register_fn() to accept raw backend functions without requiring
    callers to subclass EmbeddingEmbedder.
    """

    def __init__(self, fn: Callable[[Any], np.ndarray], modality: str = "") -> None:
        self._fn = fn
        self._modality = modality
        self._fallback = HashEmbeddingEmbedder()

    @property
    def name(self) -> str:
        return f"function_{self._modality}"

    def embed(self, item: Any) -> Embedding:
        try:
            result = self._fn(item)
            if isinstance(result, np.ndarray) and result.shape == (EMBEDDING_DIM,):
                return Embedding(
                    vector=result.astype(np.float32),
                    modality=str(getattr(item, "modality", "")),
                    provider=self.name,
                )
        except Exception as e:
            logger.debug("FunctionBackedEmbedder failed: %s", e)
        return self._fallback.embed(item)


class EmbeddingRegistry:
    """Maps modality string → EmbeddingEmbedder.

    Thread-safety: register() and embed() are not protected by a lock.
    Provider swaps should happen at startup, before concurrent inference.
    """

    def __init__(self) -> None:
        self._providers: dict[str, EmbeddingEmbedder] = {}
        self._default: EmbeddingEmbedder = HashEmbeddingEmbedder()

    def register(self, modality: str, provider: EmbeddingEmbedder) -> None:
        """Register a provider for a modality.

        Replaces any existing registration for that modality. Takes effect
        immediately for all subsequent embed() calls.
        """
        self._providers[modality.lower()] = provider

    def register_fn(self, modality: str, fn: Callable[[Any], np.ndarray]) -> None:
        """Register a raw backend function as a provider.

        fn(knowledge_item) → np.ndarray of shape (EMBEDDING_DIM,).
        Invalid shapes fall back to HashEmbeddingEmbedder.
        """
        self._providers[modality.lower()] = FunctionBackedEmbedder(fn, modality)

    def get(self, modality: str) -> EmbeddingEmbedder:
        """Return the provider registered for modality, or the hash fallback."""
        return self._providers.get(modality.lower(), self._default)

    def embed(self, item: Any) -> Embedding:
        """Encode item using the provider registered for its modality."""
        modality = str(getattr(item, "modality", "document")).lower()
        return self.get(modality).embed(item)

    def list_modalities(self) -> list[str]:
        return sorted(self._providers.keys())


def build_default_registry() -> EmbeddingRegistry:
    """Build the default provider registry with all production defaults wired.

    Provider hierarchy per modality:
        document / manual  → SBERTEmbedder (falls back to BOWEmbedder)
        image              → CLIPImageEmbedder (falls back to CLIPTextEmbedder)
        video              → VideoEmbedder (frame sampling + CLIPImageEmbedder)
        audio              → WhisperAudioEmbedder (transcript + SBERT)
        telemetry / sensor → TelemetryEmbedder (statistical features)
        simulation         → SimulationEmbedder
        graph              → GraphEmbedder (structural features)
        ontology           → OntologyEmbedder
        database           → DatabaseEmbedder (SQL features + TF-IDF)
        code               → CodeEmbedder (SBERT 60% + lexical 40%)
        cad                → CADEmbedder (geometry features + TF-IDF)
        (all others)       → HashEmbeddingEmbedder (default fallback)
    """
    from src.monkey_brain.kernel.plan.embedding.text import SBERTEmbedder
    from src.monkey_brain.kernel.plan.embedding.telemetry import (
        TelemetryEmbedder,
        SensorEmbedder,
        SimulationEmbedder,
    )
    from src.monkey_brain.kernel.plan.embedding.graph import (
        GraphEmbedder,
        OntologyEmbedder,
    )
    from src.monkey_brain.kernel.plan.embedding.database import DatabaseEmbedder
    from src.monkey_brain.kernel.plan.embedding.code import CodeEmbedder
    from src.monkey_brain.kernel.plan.embedding.cad import CADEmbedder
    from src.monkey_brain.kernel.plan.embedding.image import CLIPImageEmbedder
    from src.monkey_brain.kernel.plan.embedding.video import VideoEmbedder
    from src.monkey_brain.kernel.plan.embedding.audio import WhisperAudioEmbedder

    registry = EmbeddingRegistry()

    sbert = SBERTEmbedder()  # shared instance — lazy-loads model once

    registry.register("document", sbert)
    registry.register("manual", sbert)

    registry.register("image", CLIPImageEmbedder())
    registry.register("video", VideoEmbedder())
    registry.register("audio", WhisperAudioEmbedder())

    registry.register("telemetry", TelemetryEmbedder())
    registry.register("sensor", SensorEmbedder())
    registry.register("simulation", SimulationEmbedder())

    registry.register("graph", GraphEmbedder())
    registry.register("ontology", OntologyEmbedder())

    registry.register("database", DatabaseEmbedder())
    registry.register("code", CodeEmbedder())
    registry.register("cad", CADEmbedder())

    return registry
