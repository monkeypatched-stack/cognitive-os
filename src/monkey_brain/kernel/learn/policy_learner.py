"""PolicyLearner — updates PolicyStore from (S, A, R, S') transitions.

Phase 3 of the World-Policy Separation Refactor.

PolicyLearner receives:
  - Environment reward (what outcome occurred)
  - Actor loss from comparator (why the outcome was good or bad)

These are distinct signals. The reward tells the learner WHAT happened.
The actor_loss tells it HOW WRONG the prediction was.

PolicyLearner updates the PolicyStore via Bellman TD updates.
It never modifies the world tensor.

Architectural invariant:
    Policy learning updates only PolicyStore.
    It never modifies transition frequencies.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger("agentos.policy_learner")


class PolicyLearner:
    """Updates PolicyStore from (S, A, R, S') transitions.

    Consumes two distinct signals:
      - environment_reward: what outcome occurred (from env/evaluator)
      - actor_loss: why the outcome was good/bad (from comparator)

    Thread-safe: all mutations are guarded by a lock.
    """

    def __init__(self, policy_store: Any) -> None:
        self._store = policy_store
        self._update_count: int = 0
        self._lock = threading.Lock()

    def update(
        self,
        state: str,
        action: str,
        reward: float,
        next_state: str = "",
        actor_loss: float = 0.0,
    ) -> dict:
        """Bellman update with environment reward and comparator loss.

        The reward signal drives Q-value updates:
            Q(S,A) ← Q(S,A) + α[R + γ·max_a' Q(S',a') - Q(S,A)]

        The actor_loss signal is recorded for diagnostics but does NOT
        directly modify Q-values. It tells the learner how wrong the
        prediction was, which can be used by advanced learning algorithms
        (e.g. prioritized replay, loss-weighted updates).

        When next_state is empty, the transition is terminal: target = reward.

        Returns metrics: {td_error, old_q, new_q, target, actor_loss}.
        """
        with self._lock:
            old_q = self._store.value(state, action)
            if next_state:
                next_q = self._store._max_q(next_state)
                target = reward + self._store._discount * next_q
            else:
                target = reward
            td_error = abs(target - old_q)
            self._store.update(state, action, reward, next_state)
            self._update_count += 1

            return {
                "td_error": round(td_error, 6),
                "old_q": round(old_q, 6),
                "new_q": round(self._store.value(state, action), 6),
                "target": round(target, 6),
                "actor_loss": round(actor_loss, 6),
            }

    def update_batch(self, transitions: list[dict]) -> list[dict]:
        """Batch update from a list of transition dicts.

        Each dict: {state, action, reward, next_state?, actor_loss?}
        Returns a list of metrics per transition.
        """
        return [
            self.update(
                t["state"],
                t["action"],
                t["reward"],
                t.get("next_state", ""),
                t.get("actor_loss", 0.0),
            )
            for t in transitions
        ]

    @property
    def update_count(self) -> int:
        return self._update_count

    def summary(self) -> dict:
        return {
            "update_count": self._update_count,
            "policy_size": self._store.size,
            "avg_q": self._store.summary()["avg_q"],
        }
