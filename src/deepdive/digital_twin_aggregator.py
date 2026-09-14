"""Digital Twin Aggregation — cross-node twin synchronization.

Aggregates digital twin state across edge nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class TwinSnapshot:
    """A snapshot of a digital twin from an edge node."""

    snapshot_id: str = field(default_factory=lambda: f"twin-{uuid4().hex[:12]}")
    node_id: str = ""
    entity_type: str = ""
    entity_id: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AggregatedTwin:
    """Aggregated digital twin state across nodes."""

    entity_type: str = ""
    entity_id: str = ""
    latest_state: dict[str, Any] = field(default_factory=dict)
    contributing_nodes: list[str] = field(default_factory=list)
    snapshot_count: int = 0
    last_updated: str = ""


class DigitalTwinAggregator:
    """Aggregates digital twin state across edge nodes.

    Responsibilities:
    - Collect twin snapshots from nodes
    - Merge twin states
    - Track twin versions
    - Support twin sync

    Aggregation never performs cognition.
    Aggregation never controls execution.
    """

    def __init__(self, elasticsearch_adapter: Any = None):
        self._es = elasticsearch_adapter
        self._snapshots: dict[str, list[TwinSnapshot]] = {}
        self._aggregated: dict[str, AggregatedTwin] = {}

    def add_snapshot(self, snapshot: TwinSnapshot) -> None:
        """Add a twin snapshot from a node."""
        key = f"{snapshot.entity_type}:{snapshot.entity_id}"
        if key not in self._snapshots:
            self._snapshots[key] = []
        self._snapshots[key].append(snapshot)

        if len(self._snapshots[key]) > 100:
            self._snapshots[key] = self._snapshots[key][-50:]

        self._update_aggregated(key)

    def _update_aggregated(self, key: str) -> None:
        """Update aggregated twin state."""
        snapshots = self._snapshots.get(key, [])
        if not snapshots:
            return

        latest = snapshots[-1]
        nodes = list(set(s.node_id for s in snapshots))

        self._aggregated[key] = AggregatedTwin(
            entity_type=latest.entity_type,
            entity_id=latest.entity_id,
            latest_state=latest.state,
            contributing_nodes=nodes,
            snapshot_count=len(snapshots),
            last_updated=latest.timestamp,
        )

    def get_aggregated(self, entity_type: str, entity_id: str) -> AggregatedTwin | None:
        key = f"{entity_type}:{entity_id}"
        return self._aggregated.get(key)

    def get_all_aggregated(self, entity_type: str | None = None) -> list[AggregatedTwin]:
        if entity_type:
            return [t for t in self._aggregated.values() if t.entity_type == entity_type]
        return list(self._aggregated.values())

    def get_history(self, entity_type: str, entity_id: str, limit: int = 100) -> list[TwinSnapshot]:
        key = f"{entity_type}:{entity_id}"
        return self._snapshots.get(key, [])[-limit:]

    async def persist_to_elasticsearch(self) -> dict[str, Any]:
        """Persist aggregated twins to Elasticsearch."""
        if not self._es:
            return {"status": "no_elasticsearch"}

        for key, twin in self._aggregated.items():
            from src.monkey_brain.persistence.events import PersistenceEvent, EventType

            event = PersistenceEvent(
                event_type=EventType.ENTITY_UPDATED,
                entity_type="digital_twin",
                entity_id=f"{twin.entity_type}:{twin.entity_id}",
                data={
                    "entity_type": twin.entity_type,
                    "entity_id": twin.entity_id,
                    "latest_state": twin.latest_state,
                    "contributing_nodes": twin.contributing_nodes,
                    "snapshot_count": twin.snapshot_count,
                },
            )
            await self._es.persist(event)

        return {"status": "persisted", "twins": len(self._aggregated)}

    def summary(self) -> dict:
        types = set(t.entity_type for t in self._aggregated.values())
        return {
            "total_twins": len(self._aggregated),
            "entity_types": list(types),
        }
