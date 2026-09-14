"""EmbeddingEmbedder — the abstraction that decouples the kernel from ML models.

The kernel depends only on this interface. All model-specific code (SBERT, CLIP,
feature engineering) lives in subclasses. Swapping a provider requires no kernel changes.

    EmbeddingEmbedder.embed(item) → Embedding
    Embedding.vector ∈ R^EMBEDDING_DIM
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np

from src.monkey_brain.kernel.plan.embedding._utils import EMBEDDING_DIM


@dataclass
class Embedding:
    """Output of any EmbeddingEmbedder."""

    vector: np.ndarray  # shape (EMBEDDING_DIM,), L2-normalised
    modality: str = ""
    provider: str = ""

    @property
    def dim(self) -> int:
        return len(self.vector)


class EmbeddingEmbedder(ABC):
    """Interface for all embedding providers.

    Implementations:
        HashEmbeddingEmbedder  — deterministic, no ML, for testing
        BOWEmbedder            — TF-IDF character bigrams, no ML
        SBERTEmbedder          — sentence-transformers
        CLIPTextEmbedder       — CLIP text encoder (HuggingFace)
        TelemetryEmbedder      — time-series statistical features
        GraphEmbedder          — structural graph features
        ... (one per modality)

    Swapping providers is always backward-compatible: same interface,
    same output dimension, different internals.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def output_dim(self) -> int:
        return EMBEDDING_DIM

    @abstractmethod
    def embed(self, item: Any) -> Embedding:
        """Encode item into Embedding with vector ∈ R^output_dim."""
        ...


class HashEmbeddingEmbedder(EmbeddingEmbedder):
    """Deterministic hash-based provider. No ML models required.

    Suitable for:
        - Unit tests (fast, reproducible)
        - CI without model weights
        - Smoke-testing workload plumbing before production providers arrive

    SHA-256 digest → 32 bytes → 32 float32 values → L2-normalised.
    Different content always produces different embeddings.
    """

    @property
    def name(self) -> str:
        return "hash"

    def embed(self, item: Any) -> Embedding:
        content = str(getattr(item, "content", item) or "")
        h = hashlib.sha256(content.encode("utf-8", errors="replace")).digest()
        vec = np.frombuffer(h, dtype=np.uint8).astype(np.float32) / 127.5 - 1.0
        n = float(np.linalg.norm(vec))
        if n > 1e-8:
            vec = vec / n
        return Embedding(
            vector=vec,
            modality=str(getattr(item, "modality", "")),
            provider=self.name,
        )
