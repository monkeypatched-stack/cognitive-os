"""Reducer — pure state mutation.

Reducers receive:
  Current Runtime State * Execution Event
  ↓
  Updated Runtime State
  ↓
  Persistence Commands

Reducers remain pure.
Reducers never perform I/O.
Reducers emit persistence operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.monkey_brain.persistence.events import PersistenceEvent, EventType


@dataclass
class StateMutation:
    """A state mutation with persistence commands."""

    state_updates: dict[str, Any] = field(default_factory=dict)
    persistence_events: list[PersistenceEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class Reducer:
    """Pure state reducer.

    Responsibilities:
    - Transform state based on events
    - Emit persistence operations
    - Never perform I/O
    """

    def __init__(self):
        self._reducers: dict[str, Callable] = {}

    def register(self, event_type: str, reducer_fn: Callable) -> None:
        """Register a reducer for an event type."""
        self._reducers[event_type] = reducer_fn

    def reduce(self, state: dict[str, Any], event: dict[str, Any]) -> StateMutation:
        """Apply reducer to state and event."""
        event_type = event.get("type", "unknown")

        reducer_fn = self._reducers.get(event_type)
        if reducer_fn:
            return reducer_fn(state, event)

        return StateMutation()

    def entity_created(
        self,
        entity_type: str,
        entity_id: str,
        data: dict[str, Any],
    ) -> StateMutation:
        """Emit entity created mutation."""
        return StateMutation(
            state_updates={entity_type: {entity_id: data}},
            persistence_events=[
                PersistenceEvent(
                    event_type=EventType.ENTITY_CREATED,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    data=data,
                )
            ],
        )

    def entity_updated(
        self,
        entity_type: str,
        entity_id: str,
        data: dict[str, Any],
    ) -> StateMutation:
        """Emit entity updated mutation."""
        return StateMutation(
            state_updates={entity_type: {entity_id: data}},
            persistence_events=[
                PersistenceEvent(
                    event_type=EventType.ENTITY_UPDATED,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    data=data,
                )
            ],
        )

    def entity_deleted(
        self,
        entity_type: str,
        entity_id: str,
    ) -> StateMutation:
        """Emit entity deleted mutation."""
        return StateMutation(
            state_updates={entity_type: {entity_id: None}},
            persistence_events=[
                PersistenceEvent(
                    event_type=EventType.ENTITY_DELETED,
                    entity_type=entity_type,
                    entity_id=entity_id,
                )
            ],
        )

    # ── Epistemic state reducers — each emits MEMORY events routed to mem0 ───

    def epistemic_state_updated(
        self,
        E_t: dict[str, Any],
        agent_id: str,
    ) -> StateMutation:
        """Full E_t = (S, B, A, M) updated — emit one MEMORY_STORED per component."""
        events = []
        for component, key in [
            ("world", "S"),
            ("belief", "B"),
            ("affordance", "A"),
            ("mesh", "M"),
        ]:
            events.append(
                PersistenceEvent(
                    event_type=EventType.EPISTEMIC_STATE_UPDATED,
                    entity_type=f"epistemic.{component}",
                    entity_id=agent_id,
                    data={
                        "component": component,
                        "state": E_t.get(key, {}),
                        "agent_id": agent_id,
                    },
                )
            )
        return StateMutation(
            state_updates={"epistemic_state": {agent_id: E_t}},
            persistence_events=events,
        )

    def belief_updated(
        self,
        belief: dict[str, Any],
        agent_id: str,
    ) -> StateMutation:
        """BeliefState B = (K, C, U) updated."""
        return StateMutation(
            state_updates={"belief_state": {agent_id: belief}},
            persistence_events=[
                PersistenceEvent(
                    event_type=EventType.BELIEF_STATE_UPDATED,
                    entity_type="epistemic.belief",
                    entity_id=agent_id,
                    data={"belief": belief, "agent_id": agent_id},
                )
            ],
        )

    def knowledge_item_evolved(
        self,
        item: dict[str, Any],
        agent_id: str,
    ) -> StateMutation:
        """One KnowledgeItem updated — emit KNOWLEDGE_ITEM_EVOLVED."""
        item_id = item.get("id", "unknown")
        return StateMutation(
            state_updates={"knowledge": {item_id: item}},
            persistence_events=[
                PersistenceEvent(
                    event_type=EventType.KNOWLEDGE_ITEM_EVOLVED,
                    entity_type="epistemic.knowledge",
                    entity_id=item_id,
                    data={"item": item, "agent_id": agent_id},
                )
            ],
        )

    def knowledge_item_retired(
        self,
        item_id: str,
        agent_id: str,
    ) -> StateMutation:
        """KnowledgeItem retired — emit KNOWLEDGE_ITEM_RETIRED (→ mem0 delete)."""
        return StateMutation(
            state_updates={"knowledge": {item_id: None}},
            persistence_events=[
                PersistenceEvent(
                    event_type=EventType.KNOWLEDGE_ITEM_RETIRED,
                    entity_type="epistemic.knowledge",
                    entity_id=item_id,
                    data={"item_id": item_id, "agent_id": agent_id},
                )
            ],
        )

    def goal_updated(
        self,
        goal: dict[str, Any],
        agent_id: str,
    ) -> StateMutation:
        """GoalState G_t updated."""
        return StateMutation(
            state_updates={"goal_state": {agent_id: goal}},
            persistence_events=[
                PersistenceEvent(
                    event_type=EventType.GOAL_STATE_UPDATED,
                    entity_type="epistemic.goal",
                    entity_id=agent_id,
                    data={"goal": goal, "agent_id": agent_id},
                )
            ],
        )

    def mesh_updated(
        self,
        mesh_summary: dict[str, Any],
        agent_id: str,
    ) -> StateMutation:
        """AgentMesh topology updated."""
        return StateMutation(
            state_updates={"mesh_state": {agent_id: mesh_summary}},
            persistence_events=[
                PersistenceEvent(
                    event_type=EventType.MESH_STATE_UPDATED,
                    entity_type="epistemic.mesh",
                    entity_id=agent_id,
                    data={"mesh": mesh_summary, "agent_id": agent_id},
                )
            ],
        )

    def simulation_loss_recorded(
        self,
        loss: dict[str, float],
        agent_id: str,
        round_num: int = 0,
    ) -> StateMutation:
        """SimulationLoss recorded after a G→A→R round."""
        return StateMutation(
            state_updates={},
            persistence_events=[
                PersistenceEvent(
                    event_type=EventType.SIMULATION_LOSS_RECORDED,
                    entity_type="epistemic.loss",
                    entity_id=agent_id,
                    data={"loss": loss, "round": round_num, "agent_id": agent_id},
                )
            ],
        )
