"""Image embedding providers — pixel-level CLIP encoding with histogram fallback.

Dependency chain:
    CLIPImageEmbedder      → falls back to → CLIPTextEmbedder (caption as text)
    PixelHistogramEmbedder → no external dependencies (pure numpy/PIL)

CLIPImageEmbedder and CLIPTextEmbedder share the same random projection seed
so image and text embeddings are in the same latent space and directly comparable.

item.content may be any of:
    str             — file path to an image
    bytes           — raw image bytes
    PIL.Image.Image — already-loaded image
    np.ndarray      — HxWxC uint8 or float32
    str (base64)    — base64-encoded image bytes

Replace with a production encoder:
    registry.register("image", DINOv2Provider())
    registry.register("image", SigLIPProvider())
"""

from __future__ import annotations

import base64
import io
import logging
import re as _re
from typing import Any

import numpy as np

from src.monkey_brain.kernel.plan.embedding._utils import (
    EMBEDDING_DIM,
    _l2,
    _bow_project,
)
from src.monkey_brain.kernel.plan.embedding.provider import Embedding, EmbeddingEmbedder

logger = logging.getLogger(__name__)

_IMAGE_EXT = _re.compile(r"\.(jpe?g|png|gif|bmp|webp|tiff?|svg)$", _re.I)


def load_pil(content: Any):
    """Coerce item.content to a PIL RGB Image. Returns None on failure."""
    try:
        from PIL import Image

        if isinstance(content, Image.Image):
            return content.convert("RGB")

        if isinstance(content, np.ndarray):
            arr = content
            if arr.dtype != np.uint8:
                arr = np.clip(arr * 255, 0, 255).astype(np.uint8)
            return Image.fromarray(arr).convert("RGB")

        if isinstance(content, (bytes, bytearray)):
            return Image.open(io.BytesIO(bytes(content))).convert("RGB")

        if isinstance(content, str):
            # base64-encoded
            if content.startswith("data:image") or (
                len(content) > 100 and not _IMAGE_EXT.search(content) and " " not in content
            ):
                try:
                    _, _, b64 = content.partition(",")
                    raw = base64.b64decode(b64 if b64 else content)
                    return Image.open(io.BytesIO(raw)).convert("RGB")
                except Exception:
                    logger.debug("load_pil: suppressed exception", exc_info=True)
            # file path
            return Image.open(content).convert("RGB")

    except Exception as exc:
        logger.debug("load_pil failed: %s", exc)
    return None


class CLIPImageEmbedder(EmbeddingEmbedder):
    """CLIP visual encoder (openai/clip-vit-base-patch32) → R^32.

    Encodes pixel content via CLIPProcessor + CLIPModel.get_image_features().
    Shares the same random projection seed as CLIPTextEmbedder so image and
    text embeddings are in the same latent space and directly comparable.

    Falls back to CLIPTextEmbedder when CLIP is unavailable or content cannot
    be decoded as an image (treats the content string as a caption).

    Replace via registry:
        registry.register("image", DINOv2Provider())
    """

    @property
    def name(self) -> str:
        return "clip_image"

    def __init__(self) -> None:
        self._model: Any = None
        self._processor: Any = None
        self._proj: np.ndarray | None = None
        self._fallback: EmbeddingEmbedder | None = None

    def _get_fallback(self) -> EmbeddingEmbedder:
        if self._fallback is None:
            from src.monkey_brain.kernel.plan.embedding.text import CLIPTextEmbedder

            self._fallback = CLIPTextEmbedder()
        return self._fallback

    def _load(self) -> None:
        from transformers import CLIPModel, CLIPProcessor

        self._model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
        self._processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        # Same seed as CLIPTextEmbedder — shared latent space
        self._proj = np.random.default_rng(2025).normal(0, 1.0 / np.sqrt(512), (EMBEDDING_DIM, 512)).astype(np.float32)

    def embed(self, item: Any) -> Embedding:
        content = getattr(item, "content", item)
        pil_image = load_pil(content)

        if pil_image is None:
            logger.debug("CLIPImageEmbedder: cannot decode image, using text fallback")
            return self._get_fallback().embed(item)

        try:
            if self._model is None:
                self._load()
            import torch

            inputs = self._processor(images=pil_image, return_tensors="pt")
            with torch.no_grad():
                emb = self._model.get_image_features(**inputs)[0].cpu().numpy()
            meta = np.zeros(EMBEDDING_DIM, dtype=np.float32)
            meta[0] = float(getattr(item, "provenance", 0.5))
            meta[1] = float(getattr(item, "freshness", 0.5))
            vec = _l2(self._proj @ _l2(emb.astype(np.float32)))
            return Embedding(
                vector=np.tanh(0.95 * vec + 0.05 * meta),
                modality=str(getattr(item, "modality", "")),
                provider=self.name,
            )
        except Exception as exc:
            logger.debug("CLIP image encoding failed, using text fallback: %s", exc)
            return self._get_fallback().embed(item)


class PixelHistogramEmbedder(EmbeddingEmbedder):
    """Color histogram image encoder — no ML models required.

    Splits the image into 4 spatial quadrants and computes per-channel mean,
    std, and median → 36 values, plus brightness, saturation, and edge density
    → projected to R^32 via L2-normalised feature vector.

    Use as a lightweight fallback:
        registry.register("image", PixelHistogramEmbedder())

    Replace: CLIPImageEmbedder for production quality.
    """

    @property
    def name(self) -> str:
        return "pixel_histogram"

    def embed(self, item: Any) -> Embedding:
        content = getattr(item, "content", item)
        pil_image = load_pil(content)

        if pil_image is None:
            vec = _bow_project(str(content or ""))
            return Embedding(
                vector=vec,
                modality=str(getattr(item, "modality", "")),
                provider=self.name,
            )

        feat = self._extract(pil_image)
        meta = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        meta[0] = float(getattr(item, "provenance", 0.5))
        meta[1] = float(getattr(item, "freshness", 0.5))
        return Embedding(
            vector=np.tanh(0.9 * feat + 0.1 * meta),
            modality=str(getattr(item, "modality", "")),
            provider=self.name,
        )

    def _extract(self, img) -> np.ndarray:
        img = img.resize((64, 64))
        arr = np.array(img, dtype=np.float32) / 255.0
        feat = np.zeros(EMBEDDING_DIM, dtype=np.float32)

        # Global channel stats (slots 0-8)
        for c in range(3):
            ch = arr[:, :, c]
            feat[c * 3] = float(np.mean(ch))
            feat[c * 3 + 1] = float(np.std(ch))
            feat[c * 3 + 2] = float(np.median(ch))

        # Brightness and saturation proxy (slots 9-10)
        feat[9] = float(np.mean(arr))
        cmax = np.maximum(np.maximum(arr[:, :, 0], arr[:, :, 1]), arr[:, :, 2])
        cmin = np.minimum(np.minimum(arr[:, :, 0], arr[:, :, 1]), arr[:, :, 2])
        feat[10] = float(np.mean(cmax - cmin))

        # Spatial quadrant means — 4 quadrants × 3 channels (slots 11-22)
        quads = [arr[:32, :32], arr[:32, 32:], arr[32:, :32], arr[32:, 32:]]
        slot = 11
        for quad in quads:
            for c in range(3):
                feat[slot] = float(np.mean(quad[:, :, c]))
                slot += 1

        # Edge density via gradient magnitude (slot 23)
        gray = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
        gy, gx = np.diff(gray, axis=0), np.diff(gray, axis=1)
        feat[23] = float(np.mean(np.sqrt(gy[:, :63] ** 2 + gx[:63, :] ** 2)))

        return _l2(feat)
