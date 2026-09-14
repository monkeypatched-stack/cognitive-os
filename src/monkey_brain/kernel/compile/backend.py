"""Accelerated math backend — GPU when available, else CPU-vectorized, else pure Python.

All heavy sparse math (SpMV propagation, SpMM composition, reductions) routes through
here. Because cupy mirrors the numpy API, one code path runs on the GPU (cupy) or the CPU
(numpy) depending only on which array module is present; with neither installed the pure
Python fallback keeps the compile package correct everywhere.

Select explicitly with MB_MATH_BACKEND = cupy | numpy | python | auto (default auto).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Sequence

logger = logging.getLogger("agentos.compile.backend")

_xp: Any = None  # the array module: cupy, numpy, or None (pure python)
_name: str | None = None


def _detect() -> None:
    global _xp, _name
    if _name is not None:
        return
    pref = os.getenv("MB_MATH_BACKEND", "auto").strip().lower()
    if pref in ("auto", "cupy", "gpu"):
        try:
            import cupy as cp  # type: ignore

            _xp, _name = cp, "cupy"
            logger.info("[backend] cupy (GPU) enabled")
            return
        except Exception:
            if pref in ("cupy", "gpu"):
                logger.warning("[backend] cupy requested but unavailable — falling back")
    if pref in ("auto", "numpy", "cpu"):
        try:
            import numpy as np

            _xp, _name = np, "numpy"
            logger.info("[backend] numpy (CPU vectorized) enabled")
            return
        except Exception:
            logger.debug("_detect: suppressed exception", exc_info=True)
    # A typo'd MB_MATH_BACKEND ("nunpy", "GPU ", …) matched neither branch and dropped
    # silently to the pure-Python path — orders of magnitude slower, with nothing said.
    if pref not in ("auto", "cupy", "gpu", "numpy", "cpu", "python"):
        logger.warning(
            "[backend] MB_MATH_BACKEND=%r is not one of cupy|gpu|numpy|cpu|python|auto — "
            "using the pure-Python fallback",
            pref,
        )
    _xp, _name = None, "python"
    logger.info("[backend] pure-Python fallback")


def _scatter_add(xp: Any, y: Any, idx: Any, contrib: Any) -> None:
    """y[idx] += contrib, accumulating duplicate indices.

    `numpy.add.at` is a NumPy ufunc method. CuPy's own documented scatter-add is
    `cupyx.scatter_add` — reaching for `cupy.add.at` is relying on a NumPy idiom being
    mirrored, and if it is not, EVERY propagation on a GPU box dies here (auto-detect
    PREFERS cupy, so simply installing cupy would be enough to break it). Use each
    library's own API, and keep the ufunc form as the fallback.

    NOTE: this project's environment has no cupy, so the GPU branch is unexercised by
    the test suite — it is written against cupy's documented scatter API rather than
    verified on hardware.
    """
    if _name == "cupy":
        try:
            from cupyx import scatter_add  # type: ignore

            scatter_add(y, idx, contrib)
            return
        except Exception:
            logger.debug("_scatter_add: suppressed exception", exc_info=True)
    xp.add.at(y, idx, contrib)


def backend_name() -> str:
    _detect()
    return _name or "python"


def on_gpu() -> bool:
    return backend_name() == "cupy"


class PreparedCSR:
    """A CSR matrix converted to the active backend's arrays ONCE, so repeated SpMV
    steps (propagate_k does k of them) don't re-upload the whole operator every call.

    On the pure-Python backend this just holds the lists. On numpy/cupy it holds the
    device arrays plus the precomputed `rows` (source-row-per-nonzero) index — both
    matrix-only, so recomputing them per step (the old spmv_forward did) was pure waste,
    and on GPU it was a full host→device transfer of the operator on every one of the k
    steps.
    """

    __slots__ = ("n", "indptr", "indices", "data", "rows", "empty", "python")

    def __init__(
        self,
        indptr: Sequence[int],
        indices: Sequence[int],
        data: Sequence[float],
        n: int,
    ) -> None:
        if len(indptr) != n + 1:
            raise ValueError(f"spmv: CSR has {len(indptr) - 1} row(s) but x has {n} entries")
        self.n = n
        if _xp is None:
            self.python = True
            self.indptr = indptr
            self.indices = indices
            self.data = data
            self.rows = None
            self.empty = len(data) == 0
        else:
            xp = _xp
            self.python = False
            self.indices = xp.asarray(indices)
            self.data = xp.asarray(data, dtype=xp.float64)
            self.empty = self.data.size == 0
            self.indptr = None
            self.rows = None if self.empty else xp.repeat(xp.arange(n), xp.diff(xp.asarray(indptr)))

    def spmv(self, x: Sequence[float]) -> list[float]:
        """One forward step y = Mᵀx for this prepared matrix."""
        if len(x) != self.n:
            raise ValueError(f"spmv: prepared for {self.n} rows but x has {len(x)} entries")
        if self.python:
            y = [0.0] * self.n
            for i in range(self.n):
                xi = x[i]
                if xi:
                    for p in range(self.indptr[i], self.indptr[i + 1]):
                        y[self.indices[p]] += self.data[p] * xi
            return y
        if self.empty:
            return [0.0] * self.n
        xp = _xp
        xv = xp.asarray(x, dtype=xp.float64)
        contrib = self.data * xv[self.rows]
        y = xp.zeros(self.n, dtype=xp.float64)
        _scatter_add(xp, y, self.indices, contrib)
        return [float(v) for v in (y.get() if _name == "cupy" else y)]


def prepare_csr(indptr: Sequence[int], indices: Sequence[int], data: Sequence[float], n: int) -> PreparedCSR:
    """Convert a CSR matrix to backend arrays once, for repeated SpMV (see PreparedCSR)."""
    _detect()
    return PreparedCSR(indptr, indices, data, n)


def spmv_forward(
    indptr: Sequence[int],
    indices: Sequence[int],
    data: Sequence[float],
    x: Sequence[float],
) -> list[float]:
    """Forward propagation  y = Mᵀx  over CSR (row i's nonzeros flow mass to their
    columns). Runs on GPU (cupy) or CPU (numpy) with one vectorized path; pure-Python
    scatter otherwise. Returns a length-n Python list.

    One-shot convenience: for k>1 steps, prepare_csr() once and reuse .spmv().
    """
    _detect()
    return PreparedCSR(indptr, indices, data, len(x)).spmv(x)


def l1_diff(a_vals: Sequence[float], b_vals: Sequence[float]) -> float:
    """Σ|aᵢ − bᵢ| — the reduction under epistemic loss. Vectorized on GPU/CPU.

    The two backends used to DISAGREE on mismatched lengths: the pure-Python path zipped,
    which truncates to the shorter vector and silently drops the tail — reporting 0.0
    (i.e. "the two agree perfectly") for vectors that are not even the same length — while
    numpy raised a broadcast error. Same inputs, one backend inventing a zero loss and the
    other crashing. Callers align on a shared basis (see sparse.epistemic_loss), so this
    should never fire; if it does, it is a bug in the caller and must not be papered over
    with a fake zero.
    """
    _detect()
    if len(a_vals) != len(b_vals):
        raise ValueError(
            f"l1_diff: length mismatch ({len(a_vals)} vs {len(b_vals)}) — "
            "the two vectors must be projected onto a shared basis first"
        )
    if _xp is None:
        return sum(abs(x - y) for x, y in zip(a_vals, b_vals))
    xp = _xp
    d = xp.abs(xp.asarray(a_vals, dtype=xp.float64) - xp.asarray(b_vals, dtype=xp.float64)).sum()
    return float(d.get() if _name == "cupy" else d)
