"""Learner — updates Q-values from transitions.

Implements Q-learning update rule:
Q(s,a) ← Q(s,a) + α[r + γ·max_a' Q(s',a') - Q(s,a)]
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class LearningMetrics:
    """Metrics from a learning update."""

    td_error: float = 0.0
    old_q: float = 0.0
    new_q: float = 0.0
    target: float = 0.0


class Learner:
    """Q-learning updater.

    Responsibilities:
    - Convert transitions to Q-value updates
    - Manage learning rate
    - Track learning progress

    The Learner never:
    - Selects pipelines
    - Executes capabilities
    - Manages threads
    """

    def __init__(
        self,
        learning_rate: float | None = None,
        discount_factor: float | None = None,
    ):
        self._lr = learning_rate if learning_rate is not None else float(os.getenv("LEARNING_RATE", "0.1"))
        self._gamma = discount_factor if discount_factor is not None else float(os.getenv("DISCOUNT_FACTOR", "0.95"))
        self._update_count = 0

    def update_q(
        self,
        q_table: dict[tuple[str, str], float],
        state_hash: str,
        action: str,
        reward: float,
        next_state_hash: str,
        next_actions: list[str],
        done: bool,
    ) -> LearningMetrics:
        """Q-learning update rule.

        Q(s,a) ← Q(s,a) + α[r + γ·max_a' Q(s',a') - Q(s,a)]
        """
        current_q = q_table.get((state_hash, action), 0.0)

        if done:
            target = reward
        else:
            next_q_values = [q_table.get((next_state_hash, a), 0.0) for a in next_actions]
            target = reward + self._gamma * max(next_q_values) if next_q_values else reward

        new_q = current_q + self._lr * (target - current_q)
        q_table[(state_hash, action)] = new_q
        self._update_count += 1

        return LearningMetrics(
            td_error=abs(target - current_q),
            old_q=current_q,
            new_q=new_q,
            target=target,
        )

    @property
    def learning_rate(self) -> float:
        return self._lr

    @property
    def discount_factor(self) -> float:
        return self._gamma

    @property
    def update_count(self) -> int:
        return self._update_count

    def summary(self) -> dict:
        return {
            "learning_rate": self._lr,
            "discount_factor": self._gamma,
            "update_count": self._update_count,
        }
