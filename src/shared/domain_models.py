"""Shared Domain Models — dataclasses used across kernel and actor boundaries.

These models were previously in actor/layers.py but are needed by both
kernel and actor. Moving them here breaks the circular import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Entity:
    """Entity extracted from natural language"""

    name: str
    entity_type: str
    attributes: dict = field(default_factory=dict)


@dataclass
class Constraint:
    """Constraint on entity or relationship"""

    constraint_type: str
    value: Any = None


@dataclass
class Relationship:
    """Relationship between entities"""

    source: str
    target: str
    relationship_type: str
    attributes: dict = field(default_factory=dict)


@dataclass
class ActorLayerMetrics:
    """#7: RUNTIME - Belief, Trust, Reward, Memory"""

    belief_version: int
    trust_score: float
    reward_weight: float
    memory_size: int
