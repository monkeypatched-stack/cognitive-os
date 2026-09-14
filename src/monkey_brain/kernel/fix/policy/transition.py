"""Transition — RL transition model.

Represents: (state, action, reward, next_state, done)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
import hashlib
import json


@dataclass
class Transition:
    """A single transition in the RL model."""

    transition_id: str = field(default_factory=lambda: f"trans-{uuid4().hex[:8]}")
    state: dict[str, Any] = field(default_factory=dict)
    action: str = ""
    reward: float = 0.0
    next_state: dict[str, Any] = field(default_factory=dict)
    done: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, eq=True)
class TransitionKey:
    """Key for transition table lookup."""

    state_hash: str
    action: str


def hash_state(state: dict[str, Any]) -> str:
    """Hash state for table lookup."""
    state_str = json.dumps(state, sort_keys=True, default=str)
    return hashlib.md5(state_str.encode()).hexdigest()[:16]


class TransitionTable:
    """Table storing observed transitions.

    Responsibilities:
    - Store transitions
    - Query by state/action
    - Sample for learning
    - Evict old entries
    """

    def __init__(self, max_size: int = 10000):
        self._transitions: dict[TransitionKey, list[Transition]] = {}
        self._max_size = max_size
        self._count = 0

    def store(self, transition: Transition) -> None:
        """Store a transition."""
        key = TransitionKey(
            state_hash=hash_state(transition.state),
            action=transition.action,
        )
        if key not in self._transitions:
            self._transitions[key] = []
        self._transitions[key].append(transition)
        self._count += 1

        if self._count > self._max_size:
            self._evict()

    def query(self, state: dict, action: str | None = None) -> list[Transition]:
        """Query transitions by state and optionally action."""
        state_hash = hash_state(state)
        results = []
        for key, trans in self._transitions.items():
            if key.state_hash == state_hash:
                if action is None or key.action == action:
                    results.extend(trans)
        return results

    def sample(self, n: int = 32) -> list[Transition]:
        """Sample random transitions for learning."""
        import random

        all_transitions = []
        for trans in self._transitions.values():
            all_transitions.extend(trans)
        return random.sample(all_transitions, min(n, len(all_transitions)))

    def get_actions(self, state: dict) -> list[str]:
        """Get all actions taken in a given state."""
        state_hash = hash_state(state)
        actions = set()
        for key in self._transitions.keys():
            if key.state_hash == state_hash:
                actions.add(key.action)
        return list(actions)

    def _evict(self) -> None:
        """Evict oldest transitions."""
        keys = list(self._transitions.keys())
        for key in keys[: len(keys) // 2]:
            del self._transitions[key]
        self._count = sum(len(v) for v in self._transitions.values())

    def count(self) -> int:
        return self._count

    def clear(self) -> None:
        self._transitions.clear()
        self._count = 0


class ReadOnlyTransitionView:
    """Read-only view of a TransitionTable for safe concurrent access."""

    def __init__(self, table: TransitionTable):
        self._table = table

    def query(self, state: dict, action: str | None = None) -> list[Transition]:
        return self._table.query(state, action)

    def sample(self, n: int = 32) -> list[Transition]:
        return self._table.sample(n)

    def get_actions(self, state: dict) -> list[str]:
        return self._table.get_actions(state)

    def count(self) -> int:
        return self._table.count()
