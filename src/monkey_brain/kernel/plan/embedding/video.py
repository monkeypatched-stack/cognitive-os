"""Video embedding provider — uniform frame sampling + CLIP image encoding.

item.content should be a file path to a video file (mp4, avi, mov, …).

Dependency chain:
    VideoEmbedder → CLIPImageEmbedder → CLIPTextEmbedder (fallback chain)

Frame extraction requires opencv-python (cv2). When cv2 is unavailable the
provider falls back to treating content as a single image or caption.

Replace with a temporal model:
    registry.register("video", VideoMAEProvider())
    registry.register("video", TimeSformerProvider())
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from src.monkey_brain.kernel.plan.embedding._utils import EMBEDDING_DIM, _l2
from src.monkey_brain.kernel.plan.embedding.provider import Embedding, EmbeddingEmbedder

logger = logging.getLogger(__name__)


class _FrameItem:
    """Thin wrapper so a numpy frame looks like a knowledge item."""

    def __init__(self, frame: np.ndarray, original: Any) -> None:
        self.content = frame
        self.modality = getattr(original, "modality", "image")
        self.provenance = getattr(original, "provenance", 0.5)
        self.freshness = getattr(original, "freshness", 0.5)


class VideoEmbedder(EmbeddingEmbedder):
    """Video embedding via uniform frame sampling + averaged CLIP image encodings.

    Samples n_frames frames uniformly from the video, encodes each with
    CLIPImageEmbedder, and returns their L2-normalised mean as the embedding.

    Falls back to CLIPImageEmbedder on the raw content (treats it as a single
    image or caption) when frame extraction is not possible.

    Replace via registry:
        registry.register("video", VideoMAEProvider())
    """

    @property
    def name(self) -> str:
        return "video"

    def __init__(self, n_frames: int = 8) -> None:
        self._n_frames = n_frames
        self._image_provider: EmbeddingEmbedder | None = None

    def _get_image_provider(self) -> EmbeddingEmbedder:
        if self._image_provider is None:
            from src.monkey_brain.kernel.plan.embedding.image import CLIPImageEmbedder

            self._image_provider = CLIPImageEmbedder()
        return self._image_provider

    def embed(self, item: Any) -> Embedding:
        content = getattr(item, "content", item)
        frames = self._extract_frames(content)

        if not frames:
            logger.debug("VideoEmbedder: no frames extracted, delegating to CLIPImageEmbedder")
            return self._get_image_provider().embed(item)

        vectors = []
        for frame in frames:
            result = self._get_image_provider().embed(_FrameItem(frame, item))
            vectors.append(result.vector)

        avg = _l2(np.mean(np.stack(vectors), axis=0))
        meta = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        meta[0] = float(getattr(item, "provenance", 0.5))
        meta[1] = float(getattr(item, "freshness", 0.5))
        return Embedding(
            vector=np.tanh(0.95 * avg + 0.05 * meta),
            modality=str(getattr(item, "modality", "")),
            provider=self.name,
        )

    def _extract_frames(self, content: Any) -> list[np.ndarray]:
        try:
            import cv2

            path = str(content)
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                return []
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total <= 0:
                cap.release()
                return []
            indices = np.linspace(0, total - 1, min(self._n_frames, total), dtype=int)
            frames: list[np.ndarray] = []
            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
                ok, frame = cap.read()
                if ok:
                    frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            cap.release()
            return frames
        except Exception as exc:
            logger.debug("VideoEmbedder frame extraction failed: %s", exc)
            return []
