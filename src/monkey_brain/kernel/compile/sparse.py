"""Sparse matrix arithmetic over the world tensor (ADR 0021).

State and epistemic loss share one basis (the F features), so every operation is
element-wise sparse arithmetic on the same object:

    action / observe / Bellman  →  add        (touch a few cells)
    epistemic loss              →  sub, then reduce over F
    constraint injection        →  mask
    propagation                 →  matvec / power   (x → Wx → W²x → …)
    actor effect / composition  →  matmul

A single feature slice W[d,:,:,f] is a SparseMatrix; the full tensor is a stack of
them. CPU-friendly by construction (COO dicts, pure element-wise ops); the same shapes
vectorize on GPU later.
"""

from __future__ import annotations

import math
from typing import Callable, Iterable, Mapping

from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor
from src.monkey_brain.kernel.compile.types import CompiledOperator

Vector = dict[str, float]


class SparseMatrix:
    """A COO sparse matrix over named states: {(row, col): value}."""

    def __init__(self, entries: Mapping[tuple[str, str], float] | None = None) -> None:
        self._m: dict[tuple[str, str], float] = {}
        self._states: set[str] = set()
        if entries:
            for (r, c), v in entries.items():
                self.set(r, c, v)

    # ── build ────────────────────────────────────────────────────────────────────

    def set(self, r: str, c: str, v: float) -> None:
        if v == 0.0:
            self._m.pop((r, c), None)
        else:
            self._m[(r, c)] = v
            self._states.add(r)
            self._states.add(c)

    def add_at(self, r: str, c: str, dv: float) -> None:
        """A local transition update — the 'add milk' primitive: touch one cell."""
        self.set(r, c, self._m.get((r, c), 0.0) + dv)

    def get(self, r: str, c: str, default: float = 0.0) -> float:
        return self._m.get((r, c), default)

    def states(self) -> list[str]:
        return sorted(self._states)

    def nnz(self) -> int:
        return len(self._m)

    def items(self) -> Iterable[tuple[tuple[str, str], float]]:
        return self._m.items()

    # ── element-wise arithmetic (F-space is a stack of these) ────────────────────

    def __add__(self, other: "SparseMatrix") -> "SparseMatrix":
        out = SparseMatrix(self._m)
        for (r, c), v in other._m.items():
            out.add_at(r, c, v)
        return out

    def __sub__(self, other: "SparseMatrix") -> "SparseMatrix":
        out = SparseMatrix(self._m)
        for (r, c), v in other._m.items():
            out.add_at(r, c, -v)
        return out

    def scale(self, k: float) -> "SparseMatrix":
        return SparseMatrix({rc: v * k for rc, v in self._m.items()})

    def hadamard(self, other: "SparseMatrix") -> "SparseMatrix":
        return SparseMatrix({rc: v * other._m.get(rc, 0.0) for rc, v in self._m.items()})

    def mask(self, keep: Callable[[str, str, float], bool]) -> "SparseMatrix":
        """Constraint injection: keep only permitted transitions."""
        return SparseMatrix({(r, c): v for (r, c), v in self._m.items() if keep(r, c, v)})

    # ── reductions (loss norms) ──────────────────────────────────────────────────

    def frobenius(self) -> float:
        return math.sqrt(sum(v * v for v in self._m.values()))

    def l1(self) -> float:
        return sum(abs(v) for v in self._m.values())

    # ── propagation ──────────────────────────────────────────────────────────────

    def propagate(self, x: Vector) -> Vector:
        """One forward step: y_j = Σ_i x_i · M[i, j]. Activation flows along transitions."""
        y: Vector = {}
        for (i, j), v in self._m.items():
            xi = x.get(i, 0.0)
            if xi:
                y[j] = y.get(j, 0.0) + xi * v
        return y

    def propagate_k(self, x: Vector, k: int) -> list[Vector]:
        """k forward steps: [x, Mx, M²x, …, Mᵏx] — the transition chain an action fires.

        Builds CSR once and runs the accelerated backend (GPU cupy / CPU numpy / pure
        Python) for each step, so long chains and large operators vectorize."""
        from src.monkey_brain.kernel.compile import backend

        states = self.states()
        n = len(states)
        idx = {s: i for i, s in enumerate(states)}
        rows: dict[int, list[tuple[int, float]]] = {}
        for (r, c), v in self._m.items():
            rows.setdefault(idx[r], []).append((idx[c], v))
        indptr = [0]
        indices: list[int] = []
        data: list[float] = []
        for i in range(n):
            for c, v in rows.get(i, ()):
                indices.append(c)
                data.append(v)
            indptr.append(len(indices))

        # Convert the operator to backend arrays ONCE, then step k times. The old form
        # called spmv_forward(indptr, indices, data, xv) each iteration, re-uploading the
        # whole matrix on every one of the k steps — wasted allocation on CPU and k full
        # host→device transfers on GPU, which would dominate the actual compute.
        prepared = backend.prepare_csr(indptr, indices, data, n)
        xv = [x.get(s, 0.0) for s in states]
        trace = [dict(x)]
        for _ in range(k):
            xv = prepared.spmv(xv)
            trace.append({states[i]: xv[i] for i in range(n) if xv[i] != 0.0})
        return trace

    def matmul(self, other: "SparseMatrix") -> "SparseMatrix":
        """(self @ other)[i, k] = Σ_j self[i, j] · other[j, k]  (operator composition)."""
        # index other by its rows for the join
        rows: dict[str, list[tuple[str, float]]] = {}
        for (j, k), v in other._m.items():
            rows.setdefault(j, []).append((k, v))
        out = SparseMatrix()
        for (i, j), v in self._m.items():
            for k, w in rows.get(j, ()):
                out.add_at(i, k, v * w)
        return out

    # ── adapters ─────────────────────────────────────────────────────────────────

    @classmethod
    def from_operator(cls, op: CompiledOperator) -> "SparseMatrix":
        out = cls()
        for i in range(op.n):
            for p in range(op.indptr[i], op.indptr[i + 1]):
                out.set(op.node_of[i], op.node_of[op.indices[p]], op.data[p])
        return out

    @classmethod
    def from_tensor(cls, world: SparseTransitionTensor, f: Feature = Feature.PROBABILITY) -> "SparseMatrix":
        return cls({(s, d): world.feature(s, d, f) for (s, d) in world})


def epistemic_loss(a: SparseMatrix, b: SparseMatrix) -> float:
    """Epistemic loss = the F-space distance between two operators, normalized.

    Same terms as the state (this is a subtract + reduce on the shared basis), and the
    same quantity the Comparator computes for predicted-vs-actual — here generalized to
    any two operators. 0 = identical, 1 = maximal disagreement.
    """
    from src.monkey_brain.kernel.compile import backend

    union = list(set(a._m) | set(b._m))
    if not union:
        return 0.0
    av = [a._m.get(rc, 0.0) for rc in union]
    bv = [b._m.get(rc, 0.0) for rc in union]
    diff = backend.l1_diff(av, bv)  # vectorized on GPU/CPU
    scale = sum(max(abs(x), abs(y)) for x, y in zip(av, bv)) or 1.0
    return diff / scale
