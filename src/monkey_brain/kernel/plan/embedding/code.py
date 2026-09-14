"""Code embedding provider — SBERT semantic + lexical AST approximation."""

from __future__ import annotations

import logging
import re as _re
from typing import Any

import numpy as np

from src.monkey_brain.kernel.plan.embedding._utils import EMBEDDING_DIM
from src.monkey_brain.kernel.plan.embedding.provider import Embedding, EmbeddingEmbedder
from src.monkey_brain.kernel.plan.embedding.text import BOWEmbedder, SBERTEmbedder

logger = logging.getLogger(__name__)


class CodeEmbedder(EmbeddingEmbedder):
    """Code embedding: SBERT (semantic) + lexical AST approximation (structural).

    Blends 60% SBERT with 40% lexical features covering 8 language fingerprints,
    cyclomatic complexity proxy, type annotation density, and async signals.
    Replace: CodeBERT / StarEncoder / UniXcoder via registry.register("code", …).
    """

    @property
    def name(self) -> str:
        return "code"

    def __init__(self) -> None:
        self._sbert = SBERTEmbedder()
        self._bow = BOWEmbedder()

    def embed(self, item: Any) -> Embedding:
        content = str(getattr(item, "content", item) or "")
        feat = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        lines = content.split("\n")
        n_lines = len(lines)
        tokens = _re.findall(r"[a-zA-Z_]\w*", content)

        feat[0] = min(n_lines, 2000) / 2000.0
        feat[1] = min(len(tokens), 10000) / 10000.0
        feat[2] = (
            min(
                len(_re.findall(r"^\s*(?:import|from|require|use|include)\s", content, _re.M)),
                100,
            )
            / 100.0
        )
        feat[3] = min(len(_re.findall(r"(?:def|function|func|fn)\s+\w+\s*\(", content)), 100) / 100.0
        feat[4] = min(len(_re.findall(r"(?:class|struct|interface|trait)\s+\w+", content)), 50) / 50.0
        feat[5] = (
            min(
                len(
                    _re.findall(
                        r"\b(?:if|elif|else|for|while|match|case|catch|except|switch)\b",
                        content,
                    )
                ),
                200,
            )
            / 200.0
        )
        comments = [l for l in lines if _re.match(r"\s*(?:#|//|/\*|\*|<!-)", l)]
        feat[6] = len(comments) / max(n_lines, 1)
        non_empty = [l for l in lines if l.strip()]
        feat[7] = min(sum(len(l) for l in non_empty) / max(len(non_empty), 1), 200) / 200.0

        lang_sigs = [
            (r"\bdef\b[^(]*\(.*\)\s*:", 8),
            (r"\bpub\s+fn\b|\bimpl\b|\blet\s+mut\b", 9),
            (r"[{};]\s*(?:\n|$)", 10),
            (r"^\s*package\s+\w|\bimport\s+\"[\w./]+\"", 11),
            (r"\bval\b\s+\w|\bdata\s+class\b|\bfun\b\s+\w", 12),
            (r"\bSELECT\b|\bINSERT\b|\bFROM\b|\bJOIN\b", 13),
            (r"\$\{|\bconsole\.log\b|\bconst\b\s+\w+\s*=", 14),
            (r"::\s*\w|\btemplate\s*<", 15),
        ]
        for pattern, slot in lang_sigs:
            feat[slot] = 1.0 if _re.search(pattern, content, _re.M | _re.I) else 0.0

        type_kw = _re.findall(
            r"\b(?:int|float|str|bool|Optional|List|Dict|Union|Any|None|type)\b",
            content,
        )
        feat[16] = min(len(type_kw), 100) / 100.0
        feat[17] = (
            1.0
            if _re.search(
                r"\basync\b|\bawait\b|\bgoroutine\b|\bthread\b|\bspawn\b",
                content,
                _re.I,
            )
            else 0.0
        )
        feat[18] = float((getattr(item, "provenance", 0.85) + 1.0 - getattr(item, "uncertainty", 0.15)) / 2)
        feat[19] = float(getattr(item, "provenance", 0.5))
        lexical = np.tanh(feat)

        try:
            sbert_emb = self._sbert.embed(item)
            if sbert_emb.provider == "sbert":
                return Embedding(
                    vector=np.tanh(0.6 * sbert_emb.vector + 0.4 * lexical),
                    modality=str(getattr(item, "modality", "")),
                    provider=self.name,
                )
        except Exception as e:
            logger.debug("SBERT embedding failed, using lexical fallback: %s", e)

        return Embedding(
            vector=lexical,
            modality=str(getattr(item, "modality", "")),
            provider=self.name,
        )
