"""Text embedding providers — SBERT, CLIP, and BOW fallback.

Dependency chain:
    CLIPTextEmbedder  → falls back to → SBERTEmbedder
    SBERTEmbedder     → falls back to → BOWEmbedder
    BOWEmbedder       → no external dependencies

Replace via register() in EmbeddingRegistry:
    registry.register("document", E5Provider())    # drop-in for SBERTEmbedder
    registry.register("image", CLIPImageEmbedder()) # pixel-level encoding
"""

from __future__ import annotations

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


class BOWEmbedder(EmbeddingEmbedder):
    """TF-IDF bag-of-words with character bigrams. No ML models required.

    Produces semantically distinct embeddings for different content — unlike
    HashEmbeddingEmbedder, correlated content produces correlated embeddings.

    Replace with SBERTEmbedder for better semantic similarity.
    """

    @property
    def name(self) -> str:
        return "bow"

    def embed(self, item: Any) -> Embedding:
        content = str(getattr(item, "content", item) or "")
        meta = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        meta[0] = float(getattr(item, "provenance", 0.5))
        meta[1] = float(getattr(item, "freshness", 0.5))
        feat = _bow_project(content)
        return Embedding(
            vector=np.tanh(0.9 * feat + 0.1 * meta),
            modality=str(getattr(item, "modality", "")),
            provider=self.name,
        )


class SBERTEmbedder(EmbeddingEmbedder):
    """sentence-transformers all-MiniLM-L6-v2 → R^32 via random projection.

    Uses J-L lemma random projection to map 384-d → 32-d while preserving
    relative distances. Falls back to BOWEmbedder if the library is absent.

    Replace with E5Provider or NVEmbedProvider by subclassing:
        class E5Provider(SBERTEmbedder):
            def _load(self):
                self._model = SentenceTransformer("intfloat/e5-small-v2")
                ...
    """

    @property
    def name(self) -> str:
        return "sbert"

    def __init__(self) -> None:
        self._model: Any = None
        self._proj: Any = None
        self._fallback: EmbeddingEmbedder = BOWEmbedder()

    def _load(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer("all-MiniLM-L6-v2")
        d = (
            self._model.get_embedding_dimension()
            if hasattr(self._model, "get_embedding_dimension")
            else self._model.get_sentence_embedding_dimension()
        )
        self._proj = np.random.default_rng(2024).normal(0, 1.0 / np.sqrt(d), (EMBEDDING_DIM, d)).astype(np.float32)

    def embed(self, item: Any) -> Embedding:
        content = str(getattr(item, "content", item) or "")
        try:
            if self._model is None:
                self._load()
            raw = self._model.encode(content[:2048], convert_to_numpy=True, show_progress_bar=False)
            meta = np.zeros(EMBEDDING_DIM, dtype=np.float32)
            meta[0] = float(getattr(item, "provenance", 0.5))
            meta[1] = float(getattr(item, "freshness", 0.5))
            vec = _l2(self._proj @ _l2(raw.astype(np.float32)))
            return Embedding(
                vector=np.tanh(0.95 * vec + 0.05 * meta),
                modality=str(getattr(item, "modality", "")),
                provider=self.name,
            )
        except Exception as e:
            logger.debug("SBERT unavailable, falling back to BOW: %s", e)
            return self._fallback.embed(item)


class CLIPTextEmbedder(EmbeddingEmbedder):
    """CLIP text encoder (HuggingFace openai/clip-vit-base-patch32) → R^32.

    Embeds image descriptions and transcripts in CLIP's joint image-text space.
    When a CLIP image provider is registered via register(), the text and image
    embeddings are directly comparable (same latent space).

    Falls back to SBERTEmbedder → BOWEmbedder on import failure.

    To add pixel-level image encoding without changing downstream code:
        from PIL import Image
        import torch
        def clip_image_fn(ki):
            img = Image.open(ki.content)
            inputs = processor(images=img, return_tensors="pt")
            with torch.no_grad():
                emb = model.get_image_features(**inputs)[0].numpy()
            return proj @ l2(emb.astype(np.float32))
        registry.register("image", FunctionBackedEmbedder(clip_image_fn, "image"))
    """

    @property
    def name(self) -> str:
        return "clip_text"

    def __init__(self) -> None:
        self._model: Any = None
        self._tokenizer: Any = None
        self._proj: Any = None
        self._fallback: EmbeddingEmbedder = SBERTEmbedder()

    def _load(self) -> None:
        from transformers import CLIPModel, CLIPTokenizer

        self._model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
        self._tokenizer = CLIPTokenizer.from_pretrained("openai/clip-vit-base-patch32")
        self._proj = np.random.default_rng(2025).normal(0, 1.0 / np.sqrt(512), (EMBEDDING_DIM, 512)).astype(np.float32)

    def embed(self, item: Any) -> Embedding:
        content = str(getattr(item, "content", item) or "")
        try:
            if self._model is None:
                self._load()
            import torch

            inputs = self._tokenizer(
                content[:200],
                return_tensors="pt",
                truncation=True,
                max_length=77,
                padding=True,
            )
            with torch.no_grad():
                emb = self._model.get_text_features(**inputs)[0].cpu().numpy()
            meta = np.zeros(EMBEDDING_DIM, dtype=np.float32)
            meta[2] = float(getattr(item, "provenance", 0.5))
            meta[3] = float(getattr(item, "freshness", 0.5))
            meta[4] = float(getattr(item, "provenance", 0.5))
            vec = _l2(self._proj @ _l2(emb.astype(np.float32)))
            return Embedding(
                vector=np.tanh(0.9 * vec + 0.1 * meta),
                modality=str(getattr(item, "modality", "")),
                provider=self.name,
            )
        except Exception as e:
            logger.debug("CLIP unavailable, falling back to SBERT: %s", e)
            return self._fallback.embed(item)
