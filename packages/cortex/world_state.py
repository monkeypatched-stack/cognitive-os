"""WorldState — complete world state for simulation.

Maintains three views of reality:
- Current: what is actually happening
- Expected: what should be happening
- Counterfactual: what could happen
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class WorldSnapshot:
    """A point-in-time snapshot of world state."""

    snapshot_id: str = field(default_factory=lambda: f"wsnap-{uuid4().hex[:8]}")
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    current: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    counterfactual: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class WorldState:
    """Complete world state for simulation.

    Responsibilities:
    - Maintain current, expected, and counterfactual states
    - Track state history
    - Support snapshots and rollback

    The WorldState never:
    - Executes capabilities
    - Makes policy decisions
    - Modifies real-world state
    """

    def __init__(self):
        self._current: dict[str, Any] = {}
        self._expected: dict[str, Any] = {}
        self._counterfactual: dict[str, Any] = {}
        self._history: list[WorldSnapshot] = []

    @property
    def current(self) -> dict[str, Any]:
        return dict(self._current)

    @property
    def expected(self) -> dict[str, Any]:
        return dict(self._expected)

    @property
    def counterfactual(self) -> dict[str, Any]:
        return dict(self._counterfactual)

    def update_current(self, state: dict[str, Any]) -> None:
        """Update current world state."""
        self._current.update(state)

    def update_expected(self, state: dict[str, Any]) -> None:
        """Update expected world state."""
        self._expected.update(state)

    def update_counterfactual(self, state: dict[str, Any]) -> None:
        """Update counterfactual world state."""
        self._counterfactual.update(state)

    def replace_current(self, state: dict[str, Any]) -> None:
        """Replace entire current state."""
        self._current = dict(state)

    def replace_expected(self, state: dict[str, Any]) -> None:
        """Replace entire expected state."""
        self._expected = dict(state)

    def replace_counterfactual(self, state: dict[str, Any]) -> None:
        """Replace entire counterfactual state."""
        self._counterfactual = dict(state)

    def snapshot(self, metadata: dict[str, Any] | None = None) -> WorldSnapshot:
        """Take a snapshot of all states."""
        snap = WorldSnapshot(
            current=dict(self._current),
            expected=dict(self._expected),
            counterfactual=dict(self._counterfactual),
            metadata=metadata or {},
        )
        self._history.append(snap)
        return snap

    def rollback(self, snapshot_id: str) -> bool:
        """Rollback to a previous snapshot."""
        for snap in self._history:
            if snap.snapshot_id == snapshot_id:
                self._current = dict(snap.current)
                self._expected = dict(snap.expected)
                self._counterfactual = dict(snap.counterfactual)
                return True
        return False

    def history(self) -> list[WorldSnapshot]:
        return list(self._history)

    def clear(self) -> None:
        """Clear all state."""
        self._current.clear()
        self._expected.clear()
        self._counterfactual.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "current": dict(self._current),
            "expected": dict(self._expected),
            "counterfactual": dict(self._counterfactual),
            "history_length": len(self._history),
        }
