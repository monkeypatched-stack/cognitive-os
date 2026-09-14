"""GPUWorld — GPU-optimized, immutable Global World Model.

The Global World is the single source of truth.
It is shared by all actors and updated only via the Context Stream.

GPU optimization:
- Dense transition matrices (float32) for fast matmul
- State embeddings as dense vectors
- Batch operations via numpy/scipy
- Precomputed row-normalized operators

Immutability:
- Actors receive a read-only view
- Only ContextStream may call update()
- Version tracking for change detection
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

logger = logging.getLogger("agentos.gpu_world")


class GPUWorld:
    """GPU-optimized, immutable Global World Model.

    The world transition graph is built once and reused.
    Only the Context Stream may modify it via update().
    """

    def __init__(self, world: Any = None) -> None:
        self._source = world
        self._version = 0
        self._last_build_time = 0.0

        self._state_index: dict[str, int] = {}
        self._index_state: list[str] = []
        self._transition_matrix: np.ndarray | None = None
        self._normalized_matrix: np.ndarray | None = None
        self._state_embeddings: np.ndarray | None = None
        self._dirty = True

    def _build_if_needed(self) -> None:
        if not self._dirty and self._transition_matrix is not None:
            return
        self._rebuild()
        self._dirty = False
        self._last_build_time = time.time()

    def _rebuild(self) -> None:
        if self._source is None or (hasattr(self._source, "nnz") and self._source.nnz() == 0):
            self._state_index = {}
            self._index_state = []
            self._transition_matrix = np.zeros((0, 0), dtype=np.float32)
            self._normalized_matrix = np.zeros((0, 0), dtype=np.float32)
            self._state_embeddings = np.zeros((0, 64), dtype=np.float32)
            return

        states = self._source.states()
        self._index_state = list(states)
        self._state_index = {s: i for i, s in enumerate(states)}
        n = len(states)

        mat = np.zeros((n, n), dtype=np.float32)
        if hasattr(self._source, "__iter__"):
            from src.monkey_brain.kernel.compile.tensor import Feature

            for src, dst in self._source:
                if src in self._state_index and dst in self._state_index:
                    i, j = self._state_index[src], self._state_index[dst]
                    feat = (
                        self._source.feature(src, dst, Feature.PROBABILITY) if hasattr(self._source, "feature") else 1.0
                    )
                    mat[i, j] = float(feat)

        self._transition_matrix = mat

        row_sums = mat.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        self._normalized_matrix = mat / row_sums

        dim = min(64, max(8, n))
        rng = np.random.RandomState(42)
        emb = rng.randn(n, dim).astype(np.float32)
        emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-8)
        self._state_embeddings = emb

        logger.debug(
            "[gpu_world] rebuilt: %d states, %d transitions, dim=%d",
            n,
            int(mat.sum()),
            dim,
        )

    # ── Read-only queries (actors call these) ─────────────────────────────────

    @property
    def transition_matrix(self) -> np.ndarray:
        self._build_if_needed()
        return self._normalized_matrix

    @property
    def raw_matrix(self) -> np.ndarray:
        self._build_if_needed()
        return self._transition_matrix

    @property
    def state_embeddings(self) -> np.ndarray:
        self._build_if_needed()
        return self._state_embeddings

    @property
    def state_index(self) -> dict[str, int]:
        self._build_if_needed()
        return self._state_index

    @property
    def index_state(self) -> list[str]:
        self._build_if_needed()
        return self._index_state

    @property
    def n_states(self) -> int:
        self._build_if_needed()
        return len(self._index_state)

    @property
    def version(self) -> int:
        return self._version

    def successors(self, state: str) -> list[str]:
        self._build_if_needed()
        i = self._state_index.get(state)
        if i is None or self._normalized_matrix is None:
            return []
        row = self._normalized_matrix[i]
        return [self._index_state[j] for j in range(len(row)) if row[j] > 0]

    def batch_successors(self, states: list[str]) -> np.ndarray:
        """GPU-optimized: get successor matrix for batch of states."""
        self._build_if_needed()
        if self._normalized_matrix is None or not states:
            return np.zeros((0, self.n_states), dtype=np.float32)
        indices = [self._state_index[s] for s in states if s in self._state_index]
        if not indices:
            return np.zeros((0, self.n_states), dtype=np.float32)
        return self._normalized_matrix[indices]

    def matmul(self, vectors: np.ndarray) -> np.ndarray:
        """GPU-optimized: multiply vectors by transition matrix."""
        self._build_if_needed()
        if self._normalized_matrix is None or vectors.size == 0:
            return np.zeros_like(vectors)
        return vectors @ self._normalized_matrix

    def clone(self) -> GPUWorld:
        """Clone the world for prediction (never mutates original)."""
        clone = GPUWorld.__new__(GPUWorld)
        clone._source = None
        clone._version = self._version
        clone._state_index = dict(self._state_index)
        clone._index_state = list(self._index_state)
        clone._transition_matrix = self._transition_matrix.copy() if self._transition_matrix is not None else None
        clone._normalized_matrix = self._normalized_matrix.copy() if self._normalized_matrix is not None else None
        clone._state_embeddings = self._state_embeddings.copy() if self._state_embeddings is not None else None
        clone._dirty = False
        clone._last_build_time = self._last_build_time
        return clone

    # ── Write path (only ContextStream calls these) ───────────────────────────

    def update(self) -> None:
        """Mark world as dirty. Called by ContextStream after modification."""
        self._version += 1
        self._dirty = True
        logger.debug("[gpu_world] marked dirty (v%d)", self._version)
