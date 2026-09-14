"""Experience Store — stores every execution as an Experience.

Experience contains:
- State
- Action
- Reward
- Loss
- Next State

Support replay, offline learning, and benchmark replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class Experience:
    """A single experience from execution."""

    experience_id: str = field(default_factory=lambda: f"exp-{uuid4().hex[:12]}")
    state: dict[str, Any] = field(default_factory=dict)
    action: str = ""
    reward: float = 0.0
    loss: float = 0.0
    next_state: dict[str, Any] = field(default_factory=dict)
    done: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ExperienceStore:
    """Stores every execution as an Experience.

    Supports:
    - Replay
    - Offline Learning
    - Benchmark Replay
    - Regression Validation
    """

    def __init__(self, max_size: int = 10000):
        self._experiences: list[Experience] = []
        self._max_size = max_size

    def store(self, experience: Experience) -> None:
        """Store an experience."""
        self._experiences.append(experience)

        if len(self._experiences) > self._max_size:
            self._experiences = self._experiences[-self._max_size // 2 :]

    def sample(self, n: int = 32) -> list[Experience]:
        """Sample random experiences for learning."""
        import random

        return random.sample(self._experiences, min(n, len(self._experiences)))

    def get_recent(self, n: int = 100) -> list[Experience]:
        """Get recent experiences."""
        return self._experiences[-n:]

    def get_by_action(self, action: str) -> list[Experience]:
        """Get experiences for a specific action."""
        return [e for e in self._experiences if e.action == action]

    def count(self) -> int:
        return len(self._experiences)

    def summary(self) -> dict:
        if not self._experiences:
            return {"total": 0}

        rewards = [e.reward for e in self._experiences]
        return {
            "total": len(self._experiences),
            "avg_reward": sum(rewards) / len(rewards),
            "actions": len(set(e.action for e in self._experiences)),
        }
