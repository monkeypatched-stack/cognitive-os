"""CAD / geometry embedding provider."""

from __future__ import annotations

import re as _re
from typing import Any

import numpy as np

from src.monkey_brain.kernel.plan.embedding._utils import (
    EMBEDDING_DIM,
    _bow_project,
    _parse_numbers,
)
from src.monkey_brain.kernel.plan.embedding.provider import Embedding, EmbeddingEmbedder


class CADEmbedder(EmbeddingEmbedder):
    """CAD / geometry encoder — dimension + topology features.

    Extracts geometric metadata: dimension count, unit references, part/assembly
    structure, tolerance signals. Blends numeric features (50%) with TF-IDF (50%).
    Replace: PointNet++ / 3D-ResNet / BrepNet.
    """

    @property
    def name(self) -> str:
        return "cad"

    def embed(self, item: Any) -> Embedding:
        content = str(getattr(item, "content", item) or "")
        feat = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        arr = _parse_numbers(content)
        if arr.size > 0:
            scale = max(float(np.max(np.abs(arr))), 1.0)
            feat[0] = min(arr.size, 500) / 500.0
            feat[1] = float(np.mean(arr)) / scale
            feat[2] = float(np.std(arr)) / scale
            feat[3] = float(np.min(arr)) / scale
            feat[4] = float(np.max(arr)) / scale
        feat[5] = (
            min(
                len(
                    _re.findall(
                        r"\b(?:part|assembly|component|body|solid|surface)\b",
                        content,
                        _re.I,
                    )
                ),
                20,
            )
            / 20.0
        )
        feat[6] = (
            min(
                len(
                    _re.findall(
                        r"\b(?:tolerance|clearance|fit|thread|weld|joint)\b",
                        content,
                        _re.I,
                    )
                ),
                20,
            )
            / 20.0
        )
        feat[7] = min(len(_re.findall(r"\b(?:mm|cm|m|in|ft|°|deg|rad)\b", content, _re.I)), 30) / 30.0
        feat[8] = (
            min(
                len(
                    _re.findall(
                        r"\b(?:bore|hole|slot|fillet|chamfer|extrude|revolve)\b",
                        content,
                        _re.I,
                    )
                ),
                20,
            )
            / 20.0
        )
        feat[9] = float((getattr(item, "provenance", 0.8) + 1.0 - getattr(item, "uncertainty", 0.2)) / 2)
        feat[10] = float(getattr(item, "provenance", 0.5))
        text_feat = _bow_project(content)
        return Embedding(
            vector=np.tanh(0.5 * feat + 0.5 * text_feat),
            modality=str(getattr(item, "modality", "")),
            provider=self.name,
        )
