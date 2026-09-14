"""Canonical base types for the Predict-phase Solver Mesh.

Every solver sub-package re-exports from here so there is one definition.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np

logger = logging.getLogger("agentos.predict.base")


# ── Solver taxonomy ──────────────────────────────────────────────────────────


class SolverClass(StrEnum):
    GRAPH = "graph"
    SAT_SMT = "sat_smt"
    CONSTRAINT = "constraint"
    RULE_ENGINE = "rule_engine"
    MODEL_CHECKER = "model_checker"
    OPTIMIZER = "optimizer"
    JEPA = "jepa"
    MONTE_CARLO = "monte_carlo"


# ── Solver result ─────────────────────────────────────────────────────────────


@dataclass
class SolverResult:
    solver_name: str
    solver_class: SolverClass
    solution: dict[str, Any]
    confidence: float = 0.0
    proof: str = ""
    counterexamples: list[str] = field(default_factory=list)


# ── Solver interface ──────────────────────────────────────────────────────────


class ISolver(abc.ABC):
    name: str
    solver_class: SolverClass

    @abc.abstractmethod
    def can_solve(self, problem: dict) -> float:
        """Return a [0,1] score — higher means this solver is more appropriate."""

    @abc.abstractmethod
    async def solve(self, problem: dict) -> SolverResult:
        """Solve the problem and return a structured result."""


# ── Modality registry (used by JEPA) ─────────────────────────────────────────


class _ModalityRegistry:
    """Default registry: hash-projection encoder + provenance-weighted mean fusion."""

    _EMBED_DIM = 32

    def __init__(self) -> None:
        rng = np.random.default_rng(0)
        self._proj: np.ndarray = rng.normal(0, 1.0, (self._EMBED_DIM, 256)).astype(np.float32)

    def encode(self, item: Any) -> np.ndarray:
        """Encode a knowledge item into a fixed-size embedding."""
        raw = np.zeros(256, dtype=np.float32)
        try:
            text = str(getattr(item, "content", item))
            for i, ch in enumerate(text[:256]):
                raw[i % 256] += float(ord(ch)) / 128.0
        except Exception:
            logger.debug("encode: suppressed exception", exc_info=True)
        feat = self._proj @ raw
        norm = float(np.linalg.norm(feat)) + 1e-8
        return feat / norm

    def fuse(self, embeddings: list[np.ndarray], weights: list[float]) -> np.ndarray:
        """Provenance-weighted mean of embeddings."""
        if not embeddings:
            return np.zeros(self._EMBED_DIM, dtype=np.float32)
        ws = [max(w, 1e-8) for w in weights] if weights else [1.0] * len(embeddings)
        total = sum(ws)
        fused = sum(w * e for w, e in zip(ws, embeddings)) / total
        return fused.astype(np.float32)


_MODALITY_REGISTRY = _ModalityRegistry()
