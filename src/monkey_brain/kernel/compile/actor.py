"""ActorModel — a user's actions as an operator over the world (ADR 0021).

A single user's behaviour is a sparse matrix

    A ∈ ℝ^{L×M}      L = actions the user took,  M = world source states
    A[l, m] = how much action l engages world state m

Composed with a world-model slice (a CompiledOperator)

    W ∈ ℝ^{M×N}      M source states → N destination states

gives

    A · W ∈ ℝ^{L×N}

    (A·W)[l, n] = Σ_m A[l, m] · W[m, n]
                = "the effect of taking action l in the given world" — the
                  distribution over destination states the action induces.

Actor and world share the SAME state index (both intern into the tensor), so M
aligns for the multiplication. Nothing is hardcoded: A is built by recording the
actions a user actually takes; W is a slice of the observed world tensor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping

import numpy as np
from scipy.sparse import lil_matrix, csr_matrix

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
from src.monkey_brain.kernel.compile.types import CompiledOperator

if TYPE_CHECKING:
    from src.monkey_brain.kernel.compile.actor_runtime import ActorRuntime

logger = logging.getLogger("agentos.compile.actor")


@dataclass
class EffectMatrix:
    """A·W ∈ ℝ^{L×N} — per action, the induced distribution over destination states."""

    _rows: Mapping[str, Mapping[str, float]] = field(default_factory=dict)

    def actions(self) -> list[str]:
        return sorted(self._rows)

    def effect_of(self, action: str) -> dict[str, float]:
        return dict(self._rows.get(action, {}))

    def top_effects(self, action: str, k: int = 5) -> list[tuple[str, float]]:
        return sorted(self._rows.get(action, {}).items(), key=lambda kv: -kv[1])[:k]

    def as_dense(self, actions: list[str], states: list[str]) -> list[list[float]]:
        """The L×N dense view, rows = actions, cols = destination states."""
        return [[self._rows.get(a, {}).get(s, 0.0) for s in states] for a in actions]


class ActorModel:
    # this is the base model to be extended by person / enterprise / government / etc. actors. It is a sparse matrix of actions over world states.

    """A[l, m] — one user's actions over world states. Compose with a world operator
    W (M×N) to get A·W (L×N): the effect of each action in the world."""

    def __init__(
        self,
        user_id: str,
        world: SparseTransitionTensor,
        runtime: ActorRuntime | None = None,
    ) -> None:
        self.user_id = user_id
        self._world = world  # shared state index → the M axis aligns
        self._runtime = runtime  # orchestrator; None when used standalone
        self._actions: list[str] = []  # L
        self._action_index: dict[str, int] = {}
        self._states: list[str] = []  # M
        self._state_index: dict[str, int] = {}
        self._A: lil_matrix | None = None  # L×M sparse, built incrementally

    def _ensure_capacity(self) -> None:
        """Grow the lil_matrix when new rows/cols are added."""
        L, M = len(self._actions), len(self._states)
        if self._A is None:
            self._A = lil_matrix((L, M), dtype=np.float64)
            return
        if L > self._A.shape[0] or M > self._A.shape[1]:
            new = lil_matrix((max(L, self._A.shape[0]), max(M, self._A.shape[1])), dtype=np.float64)
            new[: self._A.shape[0], : self._A.shape[1]] = self._A
            self._A = new

    def _intern_action(self, action: str) -> int:
        idx = self._action_index.get(action)
        if idx is None:
            idx = len(self._actions)
            self._action_index[action] = idx
            self._actions.append(action)
        return idx

    def _intern_state(self, state: str) -> int:
        idx = self._state_index.get(state)
        if idx is None:
            idx = len(self._states)
            self._state_index[state] = idx
            self._states.append(state)
        return idx

    def record(self, action: str, state: str, *, domain: str = "default", weight: float = 1.0) -> None:
        """The user took `action`, engaging world `state`. Interns both (state into the
        shared world index so M aligns) and accumulates A[action, state] += weight."""
        row = self._intern_action(action)
        col = self._intern_state(state)
        self._world.intern(state, domain)  # keep A's columns on the world's state axis
        self._ensure_capacity()
        self._A[row, col] = self._A[row, col] + weight
        if self._runtime is not None:
            self._runtime.record_action(action, state, domain=domain, weight=weight)

    @property
    def L(self) -> int:
        return len(self._actions)

    @property
    def M(self) -> int:
        return len(self._states)

    def actions(self) -> list[str]:
        return list(self._actions)

    def to_csr(self) -> csr_matrix:
        """Snapshot A as a CSR matrix (immutable, efficient for arithmetic)."""
        if self._A is None:
            return csr_matrix((0, 0), dtype=np.float64)
        return self._A.tocsr()

    def weight(self, action: str, state: str) -> float:
        row = self._action_index.get(action)
        col = self._state_index.get(state)
        if row is None or col is None or self._A is None:
            return 0.0
        return float(self._A[row, col])

    def compose(self, world: CompiledOperator, *, normalize: bool = True) -> EffectMatrix:
        """A · W  →  EffectMatrix (L×N).

        For each nonzero A[l, m], scatter A[l,m]·W[m, :] into row l. `normalize` scales
        each action row to a distribution over destination states.
        """
        if self._A is None or self._A.nnz == 0:
            return EffectMatrix()

        a_csr = self.to_csr()
        rows: dict[str, dict[str, float]] = {}

        for l_idx in range(a_csr.shape[0]):
            action = self._actions[l_idx]
            for p in range(a_csr.indptr[l_idx], a_csr.indptr[l_idx + 1]):
                m_col = a_csr.indices[p]
                a_val = float(a_csr.data[p])
                state = self._states[m_col]
                wm = world.index_of.get(state)
                if wm is None:
                    continue
                dst_row = rows.setdefault(action, {})
                for q in range(world.indptr[wm], world.indptr[wm + 1]):
                    dst = world.node_of[world.indices[q]]
                    dst_row[dst] = dst_row.get(dst, 0.0) + a_val * world.data[q]

        if normalize:
            for dst_row in rows.values():
                total = sum(dst_row.values())
                if total > 0:
                    for k in dst_row:
                        dst_row[k] /= total

        return EffectMatrix(rows)

    def summary(self) -> dict:
        return {
            "user": self.user_id,
            "actions_L": self.L,
            "world_states_M": self.M,
            "nnz": int(self._A.nnz) if self._A is not None else 0,
            "has_runtime": self._runtime is not None,
        }

    def get_current_state(self) -> dict:
        """Return the current world state and internal context from the runtime."""
        if self._runtime is None:
            return {"actions": self.actions(), "states": list(self._states)}
        state = self._runtime.get_world_state()
        state["actions"] = self.actions()
        state["states"] = list(self._states)
        return state
