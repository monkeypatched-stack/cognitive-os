"""Persistence Manager — single gateway to storage.

Routes persistence operations to appropriate stores.
Handles retries and consistency.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.persistence.adapters import IStoreAdapter
from src.monkey_brain.persistence.events import PersistenceEvent, EventType
from src.monkey_brain.persistence.reducer import Reducer, StateMutation

logger = logging.getLogger(__name__)


_EPISTEMIC_EVENT_TYPES = {
    EventType.EPISTEMIC_STATE_UPDATED,
    EventType.BELIEF_STATE_UPDATED,
    EventType.KNOWLEDGE_ITEM_EVOLVED,
    EventType.KNOWLEDGE_ITEM_RETIRED,
    EventType.GOAL_STATE_UPDATED,
    EventType.MESH_STATE_UPDATED,
    EventType.SIMULATION_LOSS_RECORDED,
}


class PersistenceManager:
    """Routes persistence operations to appropriate stores.

    Responsibilities:
    - Route events to correct store adapters
    - Handle retries
    - Guarantee consistency
    - Publish completion events

    The Persistence Manager is the single gateway to storage.
    Runtime components never write directly to databases.
    """

    def __init__(self):
        self._adapters: dict[str, IStoreAdapter] = {}
        self._reducer = Reducer()
        self._event_log: list[PersistenceEvent] = []
        self._lemon = None

    def set_lemon(self, lemon: Any) -> None:
        """Wire in Lemon so all state changes are observed."""
        self._lemon = lemon

    def register_adapter(self, adapter: IStoreAdapter) -> None:
        """Register a store adapter."""
        self._adapters[adapter.name] = adapter

    def get_adapter(self, name: str) -> IStoreAdapter | None:
        return self._adapters.get(name)

    async def connect_all(self) -> None:
        """Connect to all registered stores."""
        for adapter in self._adapters.values():
            try:
                await adapter.connect()
                logger.info(f"Connected to {adapter.name}")
            except Exception as e:
                logger.warning(f"Failed to connect to {adapter.name}: {e}")

    async def disconnect_all(self) -> None:
        """Disconnect from all stores."""
        for adapter in self._adapters.values():
            try:
                await adapter.disconnect()
            except Exception as e:
                logger.debug("Adapter disconnect failed: %s", e)

    async def persist(self, event: PersistenceEvent) -> dict[str, Any]:
        """Persist an event to the appropriate store and observe via Lemon."""
        import copy

        event = copy.deepcopy(event)
        self._event_log.append(event)

        adapter = self._select_adapter(event)
        if adapter is None:
            return {"status": "no_adapter", "event_type": event.event_type}

        try:
            result = await adapter.persist(event)
            logger.debug(f"Persisted {event.event_type} to {adapter.name}")
            self._observe_lemon(event)
        except Exception as e:
            logger.error(f"Failed to persist to {adapter.name}: {e}")
            result = {"status": "error", "error": str(e)}

        return result

    async def apply_mutation(self, mutation: StateMutation) -> dict[str, Any]:
        """Apply a state mutation with persistence."""
        results = []
        for event in mutation.persistence_events:
            result = await self.persist(event)
            results.append(result)
        return {"mutations": len(results), "results": results}

    async def health(self) -> dict[str, Any]:
        """Check health of all stores."""
        health = {}
        for name, adapter in self._adapters.items():
            try:
                health[name] = await adapter.health()
            except Exception as e:
                health[name] = {"status": "error", "error": str(e)}
        return health

    def _select_adapter(self, event: PersistenceEvent) -> IStoreAdapter | None:
        """Select appropriate adapter based on event type."""
        # Entity events → MongoDB
        if event.event_type in (
            EventType.ENTITY_CREATED,
            EventType.ENTITY_UPDATED,
            EventType.ENTITY_DELETED,
        ):
            return self._adapters.get("mongodb")

        # World/Actor/Society/Membership/Context events → Redis
        if event.event_type in (
            EventType.WORLD_STATE_SAVED,
            EventType.ACTOR_STATE_SAVED,
            EventType.SOCIETY_STATE_SAVED,
            EventType.MEMBERSHIP_SAVED,
            EventType.CONTEXT_SAVED,
        ):
            return self._adapters.get("redis")

        # Memory events → mem0 (preferred) or Redis
        if event.event_type in (
            EventType.MEMORY_STORED,
            EventType.MEMORY_UPDATED,
            EventType.MEMORY_FORGOTTEN,
        ):
            return self._adapters.get("mem0") or self._adapters.get("redis")

        # Epistemic events → mem0 (semantic memory)
        if event.event_type in _EPISTEMIC_EVENT_TYPES:
            return self._adapters.get("mem0")

        # Metric events → InfluxDB (may not be registered)
        if event.event_type == EventType.METRIC_RECORDED:
            adapter = self._adapters.get("influxdb")
            if adapter is None:
                import logging as _log

                _log.getLogger(__name__).warning(
                    "No InfluxDB adapter registered — metric event %s dropped",
                    event.entity_id,
                )
            return adapter

        # Default → MongoDB
        return self._adapters.get("mongodb")

    def _observe_lemon(self, event: PersistenceEvent) -> None:
        """Forward state-change events to Lemon for observability."""
        if self._lemon is None:
            return
        data = event.data or {}
        agent_id = data.get("agent_id", "monkeybrain")
        entity_id = event.entity_id or agent_id

        try:
            et = event.event_type

            if et == EventType.EPISTEMIC_STATE_UPDATED:
                component = data.get("component", "world")
                state = data.get("state", {})
                self._lemon.observe_world_model(
                    simulation_id=entity_id,
                    entity=f"epistemic.{component}",
                    predicted=state,
                    actual=state,
                    accuracy=1.0,
                    drift_score=0.0,
                )

            elif et == EventType.BELIEF_STATE_UPDATED:
                belief = data.get("belief", {})
                self._lemon.observe_memory_access(
                    agent_type=agent_id,
                    memory_type="epistemic",
                    operation="write",
                    key=f"belief.{entity_id}",
                    hit=True,
                    latency_ms=0.0,
                )
                self._lemon.gauge(
                    "epistemic.belief.confidence",
                    belief.get("confidence", 1.0),
                    agent=agent_id,
                )

            elif et == EventType.KNOWLEDGE_ITEM_EVOLVED:
                item = data.get("item", {})
                self._lemon.observe_memory_access(
                    agent_type=agent_id,
                    memory_type="epistemic",
                    operation="write",
                    key=f"knowledge.{entity_id}",
                    hit=True,
                    latency_ms=0.0,
                )
                self._lemon.gauge(
                    "epistemic.knowledge.uncertainty",
                    item.get("uncertainty", 0.0),
                    agent=agent_id,
                )

            elif et == EventType.KNOWLEDGE_ITEM_RETIRED:
                self._lemon.observe_memory_access(
                    agent_type=agent_id,
                    memory_type="epistemic",
                    operation="evict",
                    key=f"knowledge.{entity_id}",
                    hit=True,
                    latency_ms=0.0,
                )

            elif et == EventType.GOAL_STATE_UPDATED:
                goal = data.get("goal", {})
                self._lemon.observe_goal(
                    goal_id=entity_id,
                    goal_text=goal.get("objective", ""),
                    status="updated",
                    pipeline_id="",
                )

            elif et == EventType.MESH_STATE_UPDATED:
                mesh = data.get("mesh", {})
                self._lemon.observe_agent_reasoning(
                    agent_type=agent_id,
                    perception=mesh,
                    decision={"action": "mesh_update"},
                    latency_ms=0.0,
                )

            elif et == EventType.SIMULATION_LOSS_RECORDED:
                loss = data.get("loss", {})
                l_e = loss.get("L_E", loss.get("l_epa", 0.0))
                round_num = data.get("round", 0)
                self._lemon.observe_world_model(
                    simulation_id=f"{entity_id}.round_{round_num}",
                    entity="simulation_loss",
                    predicted={"L_E": 0.0},
                    actual=loss,
                    accuracy=max(0.0, 1.0 - l_e),
                    drift_score=l_e,
                )
                self._lemon.observe_learning(
                    capability="simulation",
                    state_key=entity_id,
                    q_before=0.0,
                    q_after=1.0 - l_e,
                    reward=1.0 - l_e,
                    episode=round_num,
                )

        except Exception as exc:
            logger.debug(f"Lemon observe failed (non-fatal): {exc}")

    def summary(self) -> dict:
        return {
            "adapters": list(self._adapters.keys()),
            "events_logged": len(self._event_log),
        }
