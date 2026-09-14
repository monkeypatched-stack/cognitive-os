"""Goal IR — structured intermediate representation produced by the Intent Compiler.

This is the output of Stage 1 (Intent Compiler) and the input to Stage 2
(Graph Synthesizer). It captures:
- Domain and intent classification
- Entities extracted from natural language
- Constraints (budget, quantity, preferences)
- Relationships between entities
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GoalNode:
    """A single goal in a goal tree, with priority, beneficiary, and conditionals."""

    id: str  # unique identifier (e.g. "goal_1")
    text: str  # goal text (e.g. "Buy whole milk")
    priority: str  # "high", "medium", "low"
    beneficiary: str | None = None  # who benefits (e.g. "infant", "teenager")
    parent_id: str | None = None  # parent goal id for hierarchy
    condition: str | None = None  # conditional logic (e.g. "budget_permits", "milk_acquired")
    attributes: dict[str, Any] = field(default_factory=dict)  # goal-specific metadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "priority": self.priority,
            "beneficiary": self.beneficiary,
            "parent_id": self.parent_id,
            "condition": self.condition,
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GoalNode:
        return cls(
            id=d.get("id", ""),
            text=d.get("text", ""),
            priority=d.get("priority", "medium"),
            beneficiary=d.get("beneficiary"),
            parent_id=d.get("parent_id"),
            condition=d.get("condition"),
            attributes=d.get("attributes", {}),
        )


@dataclass
class GoalIR:
    """Structured goal representation extracted from natural language.

    The Intent Compiler transforms raw NL into this IR, which the
    Graph Synthesizer then uses to generate candidate execution graphs.
    """

    intent_type: str  # e.g. "purchase", "build", "query", "coordinate"
    domain: str  # e.g. "shopping", "software", "research"
    goal: str  # original natural language goal
    entities: list[EntityIR] = field(default_factory=list)
    constraints: list[ConstraintIR] = field(default_factory=list)
    relationships: list[RelationshipIR] = field(default_factory=list)
    goal_tree: list[GoalNode] = field(default_factory=list)  # hierarchical goal structure
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_type": self.intent_type,
            "domain": self.domain,
            "goal": self.goal,
            "entities": [e.to_dict() for e in self.entities],
            "constraints": [c.to_dict() for c in self.constraints],
            "relationships": [r.to_dict() for r in self.relationships],
            "goal_tree": [g.to_dict() for g in self.goal_tree],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GoalIR:
        return cls(
            intent_type=d.get("intent_type", "unknown"),
            domain=d.get("domain", "general"),
            goal=d.get("goal", ""),
            entities=[EntityIR.from_dict(e) for e in d.get("entities", [])],
            constraints=[ConstraintIR.from_dict(c) for c in d.get("constraints", [])],
            relationships=[RelationshipIR.from_dict(r) for r in d.get("relationships", [])],
            goal_tree=[GoalNode.from_dict(g) for g in d.get("goal_tree", [])],
            metadata=d.get("metadata", {}),
        )


@dataclass
class EntityIR:
    """An entity extracted from the user's intent."""

    name: str  # e.g. "whole milk", "frozen pizza"
    entity_type: str  # e.g. "product", "person", "service"
    attributes: dict[str, Any] = field(default_factory=dict)  # e.g. {"quantity": "2 liters"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.entity_type,
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EntityIR:
        return cls(
            name=d.get("name", ""),
            entity_type=d.get("type", "unknown"),
            attributes=d.get("attributes", {}),
        )


@dataclass
class ConstraintIR:
    """A constraint that must be satisfied."""

    constraint_type: str  # e.g. "budget", "preference", "time", "quantity"
    description: str  # human-readable description
    parameters: dict[str, Any] = field(default_factory=dict)  # e.g. {"limit": 50, "currency": "USD"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.constraint_type,
            "description": self.description,
            "parameters": self.parameters,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConstraintIR:
        return cls(
            constraint_type=d.get("type", "unknown"),
            description=d.get("description", ""),
            parameters=d.get("parameters", {}),
        )


@dataclass
class RelationshipIR:
    """A relationship between entities."""

    source: str  # entity name
    target: str  # entity name
    relationship_type: str  # e.g. "requires", "coordinates", "shares"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.relationship_type,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RelationshipIR:
        return cls(
            source=d.get("source", ""),
            target=d.get("target", ""),
            relationship_type=d.get("type", "unknown"),
        )
