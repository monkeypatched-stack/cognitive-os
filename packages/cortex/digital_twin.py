"""Digital Twin — virtual replica of a real-world entity.

Digital Twins maintain state and respond to commands
exactly like their real counterparts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class TwinState:
    """State snapshot of a digital twin."""

    state_id: str = field(default_factory=lambda: f"state-{uuid4().hex[:8]}")
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    data: dict[str, Any] = field(default_factory=dict)


class DigitalTwin:
    """A virtual replica of a real-world entity.

    Responsibilities:
    - Maintain current state
    - Track state history
    - Predict state transitions
    - Support rollback

    The DigitalTwin never:
    - Executes capabilities
    - Makes policy decisions
    - Manages connections
    """

    def __init__(self, entity_type: str, entity_id: str):
        self.entity_type = entity_type
        self.entity_id = entity_id
        self._state: dict[str, Any] = {}
        self._history: list[TwinState] = []
        self._id = f"twin-{uuid4().hex[:8]}"

    @property
    def id(self) -> str:
        return self._id

    def get_state(self) -> dict[str, Any]:
        """Get current twin state."""
        return dict(self._state)

    def set_state(self, state: dict[str, Any]) -> None:
        """Update twin state, preserving history."""
        self._history.append(TwinState(data=dict(self._state)))
        self._state.update(state)

    def replace_state(self, state: dict[str, Any]) -> None:
        """Replace entire twin state."""
        self._history.append(TwinState(data=dict(self._state)))
        self._state = dict(state)

    def predict(self, action: dict[str, Any]) -> dict[str, Any]:
        """Predict state after action (without executing)."""
        predicted = dict(self._state)
        effects = action.get("effects", {})
        predicted.update(effects)
        return predicted

    def rollback(self, steps: int = 1) -> dict[str, Any] | None:
        """Rollback to previous state."""
        if len(self._history) >= steps:
            for _ in range(steps):
                prev = self._history.pop()
            self._state = dict(prev.data)
            return self._state
        return None

    def history(self) -> list[TwinState]:
        return list(self._history)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self._id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "state": dict(self._state),
            "history_length": len(self._history),
        }
