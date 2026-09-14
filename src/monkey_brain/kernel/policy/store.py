"""PolicyStore — Layer 3: Decision.

Per-actor Q(S,A) storage, independent of the world tensor.

Layer 3 Equation:
    Action_a = π(Belief_a)

The PolicyStore owns Q-values and replay buffers. It never mutates the world tensor.
During Phase 1, QTable mirror-writes to PolicyStore alongside its own tensor-based
storage. Phase 2 flips authority: QTable delegates entirely to PolicyStore.

Architectural invariant:
    Policy storage never mutates the world tensor.
    Policy operates on belief, not world directly.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger("agentos.policy_store")


class PolicyStore:
    """Per-actor Q(S,A) storage.

    Stores Q-values in a plain dict keyed by (state, action) pairs.
    Independent of the world tensor — never reads or writes tensor cells.

    Thread-safe: all mutations are guarded by a lock.

    Ownership enforcement (Per-Actor CognitiveOS Isolation refactor,
    PolicyStore hardening): the (state, action) key shape has no actor_id
    component — this class has always relied on every real caller
    constructing its OWN instance (ActorBelief.__init__, CognitiveActor.
    __init__ each build a fresh one) rather than on anything structural
    preventing two actors from sharing one instance. owner_id makes that
    assumption an enforced invariant instead of a convention: once bound
    (at construction, or via bind_owner()), rebinding to a DIFFERENT
    owner_id raises immediately rather than silently mixing two actors'
    Q-values. owner_id=None (the default) preserves every existing
    construction site's behavior unchanged until it opts in.
    """

    _DEFAULT_LR_ENV = "POLICY_LEARNING_RATE"
    _DEFAULT_DISCOUNT_ENV = "POLICY_DISCOUNT_FACTOR"
    _DEFAULT_REPLAY_CAPACITY_ENV = "POLICY_REPLAY_CAPACITY"

    def __init__(
        self,
        lr: float | None = None,
        discount: float | None = None,
        replay_capacity: int | None = None,
        owner_id: str | None = None,
    ) -> None:
        self._q: dict[tuple[str, str], float] = {}
        self._lr = lr if lr is not None else float(os.getenv(self._DEFAULT_LR_ENV, "0.1"))
        self._discount = discount if discount is not None else float(os.getenv(self._DEFAULT_DISCOUNT_ENV, "0.95"))
        self._update_count: int = 0
        self._replay_buffer: list[dict] = []
        self._replay_capacity = replay_capacity or int(os.getenv(self._DEFAULT_REPLAY_CAPACITY_ENV, "1000"))
        self._lock = threading.Lock()
        self._owner_id: str | None = owner_id

    @property
    def owner_id(self) -> str | None:
        return self._owner_id

    def bind_owner(self, actor_id: str) -> None:
        """Bind this store to actor_id, once. A second bind to a
        DIFFERENT actor_id means this instance is about to be shared
        across actors — exactly the latent leak this hardening closes —
        so it raises instead of silently proceeding. Binding to the SAME
        actor_id again (e.g. a re-attach on actor restart) is a no-op."""
        with self._lock:
            if self._owner_id is not None and self._owner_id != actor_id:
                raise RuntimeError(
                    f"PolicyStore already owned by actor {self._owner_id!r} — "
                    f"cannot rebind to {actor_id!r}. One PolicyStore, one actor; "
                    f"construct a new PolicyStore for a new actor."
                )
            self._owner_id = actor_id

    # ── Core Q-value interface ─────────────────────────────────────────────

    def value(self, state: str, action: str) -> float:
        """Q(state, action). Returns 0.5 (neutral prior) if not yet learned."""
        with self._lock:
            return self._q.get((state, action), 0.5)

    def update(self, state: str, action: str, reward: float, next_state: str = "") -> None:
        """Bellman update: Q(s,a) ← Q(s,a) + α[r + γ·max_a' Q(s',a') - Q(s,a)].

        When next_state is empty, the transition is terminal: target = reward.
        """
        with self._lock:
            old_q = self._q.get((state, action), 0.5)
            if next_state:
                # Bootstrap off best Q of next state
                next_q = self._max_q_locked(next_state)
                target = reward + self._discount * next_q
            else:
                target = reward
            new_q = old_q + self._lr * (target - old_q)
            self._q[(state, action)] = new_q
            self._update_count += 1

            self._replay_buffer.append(
                {
                    "state": state,
                    "action": action,
                    "reward": reward,
                    "next_state": next_state,
                    "old_q": old_q,
                    "new_q": new_q,
                    "td_error": abs(target - old_q),
                    "timestamp": time.time(),
                }
            )
            if len(self._replay_buffer) > self._replay_capacity:
                self._replay_buffer = self._replay_buffer[-self._replay_capacity :]

    def append_replay(
        self,
        state: str,
        action: str,
        reward: float,
        next_state: str = "",
        old_q: float = 0.5,
        new_q: float = 0.5,
        td_error: float = 0.0,
    ) -> None:
        """Append a transition to the replay buffer without a Bellman update.

        Use when the caller has already computed the Q-value update externally
        and only needs to record the transition for future replay.
        """
        with self._lock:
            self._replay_buffer.append(
                {
                    "state": state,
                    "action": action,
                    "reward": reward,
                    "next_state": next_state,
                    "old_q": old_q,
                    "new_q": new_q,
                    "td_error": abs(td_error),
                    "timestamp": time.time(),
                }
            )
            if len(self._replay_buffer) > self._replay_capacity:
                self._replay_buffer = self._replay_buffer[-self._replay_capacity :]

    def best_action(self, state: str, legal_actions: list[str]) -> str:
        """Select the action with highest Q-value from the legal set.

        Returns the legal action with highest Q. Ties broken by insertion order.
        If no legal actions, returns empty string.
        """
        if not legal_actions:
            return ""
        with self._lock:
            return max(legal_actions, key=lambda a: self._q.get((state, a), 0.5))

    def explore_action(self, state: str, legal_actions: list[str], epsilon: float = 0.1) -> str:
        """Epsilon-greedy action selection.

        With probability epsilon, explore a random action.
        With probability 1-epsilon, exploit the best action.
        """
        import random

        if not legal_actions:
            return ""
        if random.random() < epsilon:
            return random.choice(legal_actions)
        return self.best_action(state, legal_actions)

    def legal_actions(self, state: str, all_actions: list[str], threshold: float = 0.0) -> list[str]:
        """Filter actions by Q-value threshold.

        Returns actions with Q(state, action) >= threshold.
        """
        with self._lock:
            return [a for a in all_actions if self._q.get((state, a), 0.5) >= threshold]

    # ── Batch operations ───────────────────────────────────────────────────

    def update_batch(self, transitions: list[dict]) -> int:
        """Batch update from a list of {state, action, reward, next_state} dicts.

        Returns the number of updates applied.
        """
        count = 0
        for t in transitions:
            self.update(
                t["state"],
                t["action"],
                t["reward"],
                t.get("next_state", ""),
            )
            count += 1
        return count

    def apply_delta(self, delta: dict[tuple[str, str], float]) -> None:
        """Apply a dict of (state, action) → Q-value overrides."""
        with self._lock:
            self._q.update(delta)

    # ── Snapshot / Restore ─────────────────────────────────────────────────

    def snapshot(self) -> dict[str, float]:
        """Return a copy of all Q-values as {(state, action): q}.

        Keys are serialized as "state|action" for JSON compatibility.
        """
        with self._lock:
            return {f"{s}|{a}": q for (s, a), q in self._q.items()}

    def restore(self, data: dict[str, float]) -> None:
        """Restore Q-values from a snapshot dict.

        Keys are "state|action" strings.
        """
        with self._lock:
            self._q.clear()
            for key, q in data.items():
                parts = key.split("|", 1)
                if len(parts) == 2:
                    self._q[(parts[0], parts[1])] = q

    def save(self, path: Path | str) -> None:
        """Persist Q-values to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.snapshot(), indent=2))

    def load(self, path: Path | str) -> None:
        """Load Q-values from a JSON file."""
        path = Path(path)
        if path.exists():
            self.restore(json.loads(path.read_text()))

    # ── Replay buffer ──────────────────────────────────────────────────────

    def replay_sample(self, batch_size: int = 32) -> list[dict]:
        """Sample a random batch from the replay buffer."""
        import random

        with self._lock:
            return random.sample(self._replay_buffer, min(batch_size, len(self._replay_buffer)))

    def replay_update(self, batch_size: int = 32) -> dict:
        """Sample and replay-update a batch of transitions.

        Returns stats: {updated, avg_td_error}.
        """
        sample = self.replay_sample(batch_size)
        if not sample:
            return {"updated": 0, "avg_td_error": 0}

        total_td = 0
        for exp in sample:
            state = exp["state"]
            action = exp["action"]
            reward = exp["reward"]
            next_state = exp.get("next_state", "")
            old_q = self.value(state, action)
            if next_state:
                next_q = self._max_q(next_state)
                target = reward + self._discount * next_q
            else:
                target = reward
            td_error = target - old_q
            new_q = old_q + self._lr * td_error
            with self._lock:
                self._q[(state, action)] = new_q
            total_td += abs(td_error)
            self._update_count += 1

        return {"updated": len(sample), "avg_td_error": total_td / max(len(sample), 1)}

    @property
    def replay_size(self) -> int:
        """Current replay buffer size."""
        with self._lock:
            return len(self._replay_buffer)

    # ── Introspection ──────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        """Number of Q-values stored."""
        with self._lock:
            return len(self._q)

    @property
    def update_count(self) -> int:
        return self._update_count

    @property
    def learning_rate(self) -> float:
        return self._lr

    @property
    def discount_factor(self) -> float:
        return self._discount

    def values(self) -> dict[tuple[str, str], float]:
        """Return a copy of the internal Q dict (for inspection)."""
        with self._lock:
            return dict(self._q)

    def summary(self) -> dict:
        with self._lock:
            q_vals = list(self._q.values())
        return {
            "size": len(q_vals),
            "avg_q": sum(q_vals) / max(len(q_vals), 1),
            "min_q": min(q_vals) if q_vals else 0.5,
            "max_q": max(q_vals) if q_vals else 0.5,
            "update_count": self._update_count,
            "replay_size": self.replay_size,
            "learning_rate": self._lr,
            "discount_factor": self._discount,
        }

    # ── Internal helpers ───────────────────────────────────────────────────

    def _max_q(self, state: str) -> float:
        """Max Q over all actions for a given state. Used for TD bootstrap."""
        with self._lock:
            return self._max_q_locked(state)

    def _max_q_locked(self, state: str) -> float:
        """Max Q over all actions for a given state. Caller must hold self._lock."""
        state_qs = [q for (s, _), q in self._q.items() if s == state]
        return max(state_qs) if state_qs else 0.5
