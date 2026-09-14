"""Goal model.

Goals are structured intents with additional execution context.
BaseGoal defines WHAT the system should do.
Goal defines HOW and WHY, carrying forward all of BaseGoal's properties.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class GoalType(str, Enum):
    QUERY = "query"
    SEARCH = "search"
    AGGREGATE = "aggregate"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ANALYZE = "analyze"
    OTHER = "other"


class GoalSource(str, Enum):
    CHAT = "chat"
    EVENT = "event"
    SCHEDULER = "scheduler"
    AGENT = "agent"
    API = "api"
    UNKNOWN = "unknown"


@dataclass
class BaseGoal:
    """Base intent — the classification of what the user wants."""

    name: str
    description: str = ""
    required_inputs: list[str] = field(default_factory=list)
    required_outputs: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass
class Goal(BaseGoal):
    """Goal inherits from BaseGoal — carries forward all of its properties.

    Adds execution context: entities, metadata, source, pipeline routing.
    BaseGoal defines WHAT. Goal defines HOW and WHY.
    """

    goal_id: str = field(default_factory=lambda: f"goal-{uuid4().hex[:12]}")
    goal_type: GoalType = GoalType.QUERY
    source: GoalSource = GoalSource.UNKNOWN
    entities: list[str] = field(default_factory=list)
    augmentations: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_intent(cls, intent: BaseGoal, **overrides: Any) -> Goal:
        """Create a Goal from a BaseGoal, inheriting all its properties."""
        return cls(
            name=intent.name,
            description=intent.description,
            required_inputs=list(intent.required_inputs),
            required_outputs=list(intent.required_outputs),
            constraints=dict(intent.constraints),
            confidence=intent.confidence,
            **overrides,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "goal_type": self.goal_type.value,
            "name": self.name,
            "description": self.description,
            "source": self.source.value,
            "required_inputs": self.required_inputs,
            "required_outputs": self.required_outputs,
            "constraints": self.constraints,
            "confidence": self.confidence,
            "entities": self.entities,
            "augmentations": self.augmentations,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Goal:
        """Reconstruct a Goal from to_dict()'s output — e.g. from an IntentIR
        that round-tripped through the client between /plan and /execute."""
        return cls(
            goal_id=d.get("goal_id") or f"goal-{uuid4().hex[:12]}",
            goal_type=GoalType(d.get("goal_type", GoalType.QUERY.value)),
            name=d["name"],
            description=d.get("description", ""),
            source=GoalSource(d.get("source", GoalSource.UNKNOWN.value)),
            required_inputs=list(d.get("required_inputs") or []),
            required_outputs=list(d.get("required_outputs") or []),
            constraints=dict(d.get("constraints") or {}),
            confidence=float(d.get("confidence", 0.0)),
            entities=list(d.get("entities") or []),
            augmentations=list(d.get("augmentations") or []),
            metadata=dict(d.get("metadata") or {}),
        )


def build_goal_from_question(
    question: str,
    intent_name: str,
    entities: list[str] | None = None,
    **metadata: Any,
) -> Goal:
    """Build a Goal from a question and classified intent.

    goal_type and required_outputs are sourced from intent_registry.py's
    IntentDefinition config (resolve_goal_type / resolve_required_outputs),
    which covers every registered intent plus a small extra table for the
    handful of intents that aren't registered as an IntentDefinition.
    Entities are extracted from the question.
    """
    from src.monkey_brain.kernel.plan.intents.intent_registry import (
        resolve_goal_type,
        resolve_required_outputs,
    )

    return Goal(
        name=intent_name,
        description=f"Goal for '{question}'",
        goal_type=GoalType(resolve_goal_type(intent_name)),
        required_outputs=resolve_required_outputs(intent_name),
        entities=entities or [],
        metadata={"question": question, **metadata},
    )
