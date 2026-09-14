"""Actor Architecture Layers

Each layer is a dataclass representing a stage in the actor architecture flow.
Layer numbers are shown in comments for reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List
from uuid import uuid4


@dataclass
class Request:
    """#0: API REQUEST"""

    actor_id: str
    question: str
    run_id: str = field(default_factory=lambda: f"run_{uuid4().hex[:8]}")


@dataclass
class Intent:
    """#1: INTENT - Intent Classification"""

    intent: dict
    confidence: float
    workload_id: str


@dataclass
class Goal:
    """#2: GOAL - Goal Resolution"""

    name: str
    description: str
    required_inputs: List[str]
    required_outputs: List[str]
    constraints: dict
    confidence: float


@dataclass
@dataclass
class Entity:
    """Entity extracted from natural language"""

    name: str
    entity_type: str
    attributes: dict = field(default_factory=dict)


@dataclass
class Constraint:
    """Constraint extracted from natural language"""

    constraint_type: str
    value: Any
    description: str = ""


@dataclass
class Relationship:
    """Relationship between entities"""

    source: str
    target: str
    relationship_type: str
    attributes: dict = field(default_factory=dict)


@dataclass
class GoalIR:
    """#4: GoalIR - Entities, Constraints, Relationships"""

    intent_type: str
    domain: str
    goal: str
    entities: List[Entity] = field(default_factory=list)
    constraints: List[Constraint] = field(default_factory=list)
    relationships: List[Relationship] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class World:
    """#5: WORLD - Build/Load Transition Map"""

    transitions: int
    domains: List[str]
    states: List[str]
    built: bool


# layers.py:Actor removed — use CognitiveActor or a dict for layer #6 results


@dataclass
class ActorLayerMetrics:
    """#7: RUNTIME - Belief, Trust, Reward, Memory"""

    belief_version: int
    trust_score: float
    reward_weight: float
    memory_size: int


@dataclass
class Explore:
    """#8: EXPLORE - Domain-aware, Learn, Build Φ"""

    observations_fused: int
    transitions_learned: int
    phi_entries: int
    explored: bool


@dataclass
class Plan:
    """#9: PLAN - RuntimeConstraintEngine"""

    start_state: str
    goal_state: str
    plan: List[str]
    steps: int
    confidence: float
    violations: int


@dataclass
class Execute:
    """#10: EXECUTE - Observe, Learn, Bellman, Φ, Predict"""

    executed_steps: int
    observed: List[tuple]
    bellman_updates: int
    phi_compiled: bool
    predicted_states: List[str]
    goal_complete: bool


@dataclass
class Response:
    """#15: RESPONSE - Final Actor State + Final World State"""

    run_id: str
    actor_id: str
    question: str
    intent_ir_id: str
    intent_type: str
    goal_ir_domain: str
    start_state: str
    goal_state: str
    reached_goal: bool
    belief_transitions: int
    steps: int
    result: dict
    # P1: World state observability (audit trail for "Context updates world")
    world_transitions: int = 0
    world_states_count: int = 0
    world_domains: List[str] = field(default_factory=list)
