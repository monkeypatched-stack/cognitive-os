"""ActorBelief — actor-specific belief model, independent of the Global World.

Each actor owns an independent Belief Model that evolves from:
- Beliefs (local observations)
- Context Stream events
- Memory
- Trust-weighted observations

Belief(t+1) = f(Belief(t), Context Stream, Observations, Memory)

The belief model NEVER mutates the Global World.
Planning operates against World × Belief × Φ.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger("agentos.actor_belief")


@dataclass
class ActorBeliefSnapshot:
    """Immutable snapshot of an actor's belief at a point in time.

    Step 14 — Architecture Consolidation: renamed from BeliefState, which
    collided by name with the unrelated, actually-canonical
    kernel/pipeline/belief_state.py::BeliefState (the live per-request
    cognitive belief). This class is a point-in-time metrics summary of
    ActorBelief below, not a belief store."""

    version: int
    timestamp: float
    belief_nnz: int
    bellman_updates: int
    phi_entries: int
    memory_size: int


class ActorBelief:
    """Actor-specific belief model.

    Owns:
    - beliefs (local observations of the world)
    - Bellman policy (Q-values)
    - reward history
    - memory
    - trust weights
    - sparse transition operator Φ

    Never mutates the Global World.
    """

    def __init__(self, actor_id: str, world_view: Any = None) -> None:
        self.actor_id = actor_id
        self._world_view = world_view

        self._beliefs: dict[tuple[str, str], float] = {}
        self._reward_history: list[dict] = []
        self._memory: list[dict] = []
        self._trust: dict[str, float] = {}
        self._phi: Any = None

        # Delegate Bellman to PolicyStore (single source of truth for Q-values)
        from src.monkey_brain.kernel.policy.store import PolicyStore

        self._policy_store = PolicyStore(owner_id=actor_id)

        self._belief_version = 0
        self._observation_count = 0
        self._belief_matrix: np.ndarray | None = None
        self._dirty = True

    # ── Belief Updates ────────────────────────────────────────────────────────

    def observe(
        self,
        src: str,
        dst: str,
        *,
        reward: float = 0.0,
        confidence: float = 1.0,
        source: str = "direct",
        domain: str = "default",
    ) -> None:
        """Record an observation into the belief model."""
        key = (src, dst)
        old = self._beliefs.get(key, 0.0)
        self._beliefs[key] = old + confidence
        self._observation_count += 1
        self._belief_version += 1
        self._dirty = True

        self._reward_history.append(
            {
                "src": src,
                "dst": dst,
                "reward": reward,
                "source": source,
                "domain": domain,
                "timestamp": time.time(),
            }
        )
        if len(self._reward_history) > 10000:
            self._reward_history = self._reward_history[-10000:]

    def decay(self, decay_rate: float = 0.01) -> int:
        """Apply exponential decay to all beliefs.

        Beliefs fade over time if not reinforced.
        Returns number of beliefs removed (below threshold).
        """
        removed = 0
        keys_to_remove = []
        for key, weight in self._beliefs.items():
            new_weight = weight * (1.0 - decay_rate)
            if new_weight < 0.01:
                keys_to_remove.append(key)
            else:
                self._beliefs[key] = new_weight

        for key in keys_to_remove:
            del self._beliefs[key]
            removed += 1

        if removed > 0:
            self._belief_version += 1
            self._dirty = True

        return removed

    def update_bellman(
        self,
        state: str,
        action: str,
        reward: float,
        next_state: str,
        lr: float = 0.1,
        discount: float = 0.95,
    ) -> None:
        """Bellman TD update delegated to PolicyStore."""
        self._policy_store.update(state, action, reward, next_state)
        self._dirty = True

    def best_action(self, state: str, legal_actions: list[str]) -> str:
        return self._policy_store.best_action(state, legal_actions)

    def q_value(self, state: str, action: str) -> float:
        return self._policy_store.value(state, action)

    @property
    def _bellman(self) -> dict[tuple[str, str], float]:
        """Backward-compat read-only view of Q-values from PolicyStore."""
        return self._policy_store.values()

    # ── Memory ────────────────────────────────────────────────────────────────

    def remember(self, event: dict) -> None:
        self._memory.append({**event, "timestamp": time.time()})
        if len(self._memory) > 5000:
            self._memory = self._memory[-5000:]

    def detect_conflicts(self, src: str, dst: str, new_reward: float, threshold: float = 0.5) -> dict | None:
        """Detect if a new observation conflicts with existing belief.

        Returns conflict info if detected, None otherwise.
        """
        key = (src, dst)
        old_weight = self._beliefs.get(key, 0.0)
        old_reward = 0.0
        for h in self._reward_history:
            if h["src"] == src and h["dst"] == dst:
                old_reward = h["reward"]
                break

        delta = abs(new_reward - old_reward)
        if delta > threshold and old_weight > 0:
            return {
                "edge": key,
                "old_weight": old_weight,
                "old_reward": old_reward,
                "new_reward": new_reward,
                "delta": delta,
            }
        return None

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        return [m for m in self._memory if query.lower() in str(m).lower()][-limit:]

    # ── Trust ─────────────────────────────────────────────────────────────────

    def set_trust(self, source: str, score: float) -> None:
        self._trust[source] = max(0.0, min(1.0, score))

    def get_trust(self, source: str) -> float:
        return self._trust.get(source, 0.5)

    # ── Belief Matrix (GPU-optimized) ─────────────────────────────────────────

    def get_belief_matrix(self) -> np.ndarray:
        if not self._dirty and self._belief_matrix is not None:
            return self._belief_matrix
        return self._rebuild_belief_matrix()

    def _rebuild_belief_matrix(self) -> np.ndarray:
        if not self._beliefs:
            self._belief_matrix = np.zeros((0, 0), dtype=np.float32)
            self._dirty = False
            return self._belief_matrix

        states = sorted(set(s for (s, _) in self._beliefs) | set(d for (_, d) in self._beliefs))
        n = len(states)
        state_idx = {s: i for i, s in enumerate(states)}

        mat = np.zeros((n, n), dtype=np.float32)
        for (src, dst), weight in self._beliefs.items():
            if src in state_idx and dst in state_idx:
                mat[state_idx[src], state_idx[dst]] = weight

        row_sums = mat.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        self._belief_matrix = mat / row_sums
        self._dirty = False
        return self._belief_matrix

    # ── Φ Compilation ─────────────────────────────────────────────────────────

    def compile_phi(self) -> Any:
        """Compile Bellman policy into sparse transition operator Φ.
        Reads Q-values from PolicyStore via q_value()."""
        from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor

        if not self._beliefs:
            return None

        phi = SparseTransitionTensor()
        states = sorted(set(s for (s, _) in self._beliefs))

        for state in states:
            actions = [d for (s, d) in self._beliefs if s == state]
            if not actions:
                continue
            best = self.best_action(state, actions)
            q = self.q_value(state, best)
            weight = max(0.0, min(1.0, q))
            phi.observe(state, best, domain="default", weight=weight)

        self._phi = phi
        return phi

    # ── State ─────────────────────────────────────────────────────────────────

    def snapshot(self) -> ActorBeliefSnapshot:
        return ActorBeliefSnapshot(
            version=self._belief_version,
            timestamp=time.time(),
            belief_nnz=len(self._beliefs),
            bellman_updates=self._policy_store.update_count,
            phi_entries=self._phi.nnz() if self._phi else 0,
            memory_size=len(self._memory),
        )

    def summary(self) -> dict:
        return {
            "actor": self.actor_id,
            "belief_nnz": len(self._beliefs),
            "bellman_entries": self._policy_store.size,
            "observations": self._observation_count,
            "memory_size": len(self._memory),
            "trust_entries": len(self._trust),
            "phi_compiled": self._phi is not None,
        }
