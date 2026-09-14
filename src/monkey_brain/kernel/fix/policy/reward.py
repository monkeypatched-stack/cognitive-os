"""Reward — RL reward model.

Computes rewards from execution outcomes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RewardSignal:
    """A reward signal from execution."""

    reward: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    source: str = ""  # "feedback" | "loss" | "simulation"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class RewardModel:
    """Computes rewards from execution outcomes.

    Sources:
    - User feedback (thumbs, ratings)
    - Loss computation (predicted vs actual)
    - Simulation (predicted vs expected)
    """

    def __init__(self):
        self._history: list[RewardSignal] = []

    def from_feedback(
        self,
        accepted: bool | None = None,
        thumb: str | None = None,
        rating: int | None = None,
        feedback_text: str | None = None,
    ) -> RewardSignal:
        """Compute reward from user feedback."""
        reward = 0.0
        components = {}

        if accepted is True:
            reward += 1.0
            components["accepted"] = 1.0
        elif accepted is False:
            reward -= 1.0
            components["accepted"] = -1.0

        if thumb == "up":
            reward += 1.0
            components["thumb_up"] = 1.0
        elif thumb == "down":
            reward -= 1.0
            components["thumb_down"] = -1.0

        if rating is not None:
            r = max(-1.0, min(1.0, (rating - 3) / 2))
            reward += r
            components["rating"] = r

        normalized = (feedback_text or "").lower()
        if re.search(r"\b(?:wrong|incorrect|bad|still wrong)\b", normalized):
            reward -= 1.0
            components["negative_text"] = -1.0
        if re.search(r"\b(?:good|correct|works|right)\b", normalized):
            reward += 0.75
            components["positive_text"] = 0.75

        signal = RewardSignal(
            reward=max(-2.0, min(2.0, reward)),
            components=components,
            source="feedback",
        )
        self._history.append(signal)
        return signal

    def from_loss(self, loss_value: float) -> RewardSignal:
        """Compute reward from loss (loss=0 → reward=1)."""
        reward = 1.0 - loss_value
        signal = RewardSignal(
            reward=reward,
            components={"loss": loss_value},
            source="loss",
        )
        self._history.append(signal)
        return signal

    def from_simulation(
        self,
        predicted: dict[str, Any],
        actual: dict[str, Any],
    ) -> RewardSignal:
        """Compute reward from simulation comparison."""
        from src.monkey_brain.kernel.fix.loss.loss import compute_loss

        loss = compute_loss(predicted, actual)
        return self.from_loss(loss.value)

    def history(self) -> list[RewardSignal]:
        return list(self._history)

    def summary(self) -> dict:
        if not self._history:
            return {"count": 0, "avg_reward": 0.0}

        rewards = [s.reward for s in self._history]
        return {
            "count": len(self._history),
            "avg_reward": sum(rewards) / len(rewards),
            "min_reward": min(rewards),
            "max_reward": max(rewards),
        }
