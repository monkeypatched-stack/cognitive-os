"""Database / SQL embedding provider."""

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


class DatabaseEmbedder(EmbeddingEmbedder):
    """Relational / document database feature extractor.

    Captures SQL structure, schema complexity, and query characteristics.
    Blends structural SQL features (60%) with TF-IDF schema names (40%).
    Replace: TAPAS for table understanding / schema encoder.
    """

    @property
    def name(self) -> str:
        return "database"

    def embed(self, item: Any) -> Embedding:
        content = str(getattr(item, "content", item) or "")
        feat = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        feat[0] = min(len(_re.findall(r"\bSELECT\b|\bFROM\b", content, _re.I)), 20) / 20.0
        feat[1] = (
            min(
                len(_re.findall(r"\bJOIN\b|\bLEFT JOIN\b|\bINNER JOIN\b", content, _re.I)),
                20,
            )
            / 20.0
        )
        feat[2] = min(len(_re.findall(r"\bWHERE\b|\bHAVING\b", content, _re.I)), 10) / 10.0
        feat[3] = min(len(_re.findall(r"\bGROUP BY\b|\bORDER BY\b", content, _re.I)), 10) / 10.0
        feat[4] = (
            min(
                len(_re.findall(r"\bCOUNT\b|\bSUM\b|\bAVG\b|\bMAX\b|\bMIN\b", content, _re.I)),
                10,
            )
            / 10.0
        )
        feat[5] = (
            min(
                len(_re.findall(r"\bINSERT\b|\bUPDATE\b|\bDELETE\b", content, _re.I)),
                10,
            )
            / 10.0
        )
        feat[6] = (
            min(
                len(_re.findall(r"\bCREATE TABLE\b|\bALTER TABLE\b", content, _re.I)),
                10,
            )
            / 10.0
        )
        feat[7] = (
            min(
                len(_re.findall(r"\bINDEX\b|\bPRIMARY KEY\b|\bFOREIGN KEY\b", content, _re.I)),
                10,
            )
            / 10.0
        )
        feat[8] = (
            min(
                len(_re.findall(r"\bSUBQUERY\b|\bEXISTS\b|\bIN\s*\(", content, _re.I)),
                10,
            )
            / 10.0
        )
        arr = _parse_numbers(content)
        if arr.size > 0:
            feat[9] = min(arr.size, 1000) / 1000.0
            feat[10] = float(np.clip(np.mean(arr) / max(np.max(np.abs(arr)), 1), -1, 1))
        feat[11] = float((getattr(item, "provenance", 0.95) + 1.0 - getattr(item, "uncertainty", 0.05)) / 2)
        feat[12] = float(getattr(item, "provenance", 0.5))
        text_feat = _bow_project(content)
        return Embedding(
            vector=np.tanh(0.6 * feat + 0.4 * text_feat),
            modality=str(getattr(item, "modality", "")),
            provider=self.name,
        )
