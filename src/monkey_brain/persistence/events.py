"""Persistence Events — immutable runtime events.

Every persistence operation becomes an event.
Events are append-only, never updated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class EventType(str, Enum):
    # Entity events
    ENTITY_CREATED = "entity.created"
    ENTITY_UPDATED = "entity.updated"
    ENTITY_DELETED = "entity.deleted"

    # Relationship events
    RELATIONSHIP_CREATED = "relationship.created"
    RELATIONSHIP_DELETED = "relationship.deleted"

    # Memory events
    MEMORY_STORED = "memory.stored"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_FORGOTTEN = "memory.forgotten"

    # Workflow events
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"

    # Workload events
    WORKLOAD_EXECUTED = "workload.executed"
    CAPABILITY_INVOKED = "capability.invoked"

    # Session events
    SESSION_CREATED = "session.created"
    SESSION_EXPIRED = "session.expired"

    # Audit events
    AUDIT_RECORDED = "audit.recorded"

    # Search events
    SEARCH_INDEXED = "search.indexed"

    # Metric events
    METRIC_RECORDED = "metric.recorded"

    # World state events (PlanetaryRuntime)
    WORLD_STATE_SAVED = "world.state.saved"
    WORLD_STATE_LOADED = "world.state.loaded"

    # Actor state events (PlanetaryRuntime)
    ACTOR_STATE_SAVED = "actor.state.saved"
    ACTOR_STATE_LOADED = "actor.state.loaded"

    # Society state events (PlanetaryRuntime)
    SOCIETY_STATE_SAVED = "society.state.saved"
    SOCIETY_STATE_LOADED = "society.state.loaded"

    # Membership events (PlanetaryRuntime)
    MEMBERSHIP_SAVED = "membership.saved"
    MEMBERSHIP_LOADED = "membership.loaded"

    # Context stream events (PlanetaryRuntime)
    CONTEXT_SAVED = "context.saved"
    CONTEXT_LOADED = "context.loaded"

    # Epistemic state events — routed to mem0
    EPISTEMIC_STATE_UPDATED = "epistemic.state.updated"
    BELIEF_STATE_UPDATED = "epistemic.belief.updated"
    KNOWLEDGE_ITEM_EVOLVED = "epistemic.knowledge.evolved"
    KNOWLEDGE_ITEM_RETIRED = "epistemic.knowledge.retired"
    GOAL_STATE_UPDATED = "epistemic.goal.updated"
    MESH_STATE_UPDATED = "epistemic.mesh.updated"
    SIMULATION_LOSS_RECORDED = "epistemic.loss.recorded"


@dataclass
class PersistenceEvent:
    """An immutable persistence event."""

    event_id: str = field(default_factory=lambda: f"evt-{uuid4().hex[:12]}")
    event_type: EventType = EventType.ENTITY_CREATED
    entity_type: str = ""
    entity_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityCreated(PersistenceEvent):
    event_type: EventType = EventType.ENTITY_CREATED


@dataclass
class EntityUpdated(PersistenceEvent):
    event_type: EventType = EventType.ENTITY_UPDATED


@dataclass
class EntityDeleted(PersistenceEvent):
    event_type: EventType = EventType.ENTITY_DELETED


@dataclass
class RelationshipCreated(PersistenceEvent):
    event_type: EventType = EventType.RELATIONSHIP_CREATED


@dataclass
class MemoryStored(PersistenceEvent):
    event_type: EventType = EventType.MEMORY_STORED


@dataclass
class WorkflowStarted(PersistenceEvent):
    event_type: EventType = EventType.WORKFLOW_STARTED


@dataclass
class WorkflowCompleted(PersistenceEvent):
    event_type: EventType = EventType.WORKFLOW_COMPLETED


@dataclass
class MetricRecorded(PersistenceEvent):
    event_type: EventType = EventType.METRIC_RECORDED
