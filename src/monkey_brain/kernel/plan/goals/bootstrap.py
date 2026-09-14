"""Goal Bootstrap — manages goal registry."""

from __future__ import annotations

from typing import Any


class GoalBootstrap:
    """Goal registry singleton."""

    _instance = None
    _goals: dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_goal(self, name: str) -> Any:
        """Get a registered goal."""
        return self._goals.get(name)

    def register_goal(self, name: str, goal: Any) -> None:
        """Register a goal."""
        self._goals[name] = goal

    def summary(self) -> dict[str, Any]:
        """Get summary of all goals."""
        return {
            "goals": {
                name: {
                    "required_inputs": getattr(goal, "required_inputs", []),
                    "required_outputs": getattr(goal, "required_outputs", []),
                    "pipeline_id": getattr(goal, "pipeline_id", name),
                }
                for name, goal in self._goals.items()
            }
        }
