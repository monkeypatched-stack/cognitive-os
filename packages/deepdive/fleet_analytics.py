"""Fleet Analytics — cross-node analytics and metrics.

Aggregates metrics and analytics across all edge nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class NodeMetrics:
    """Metrics from a single edge node."""

    node_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    executions: int = 0
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    capabilities_active: int = 0
    memory_usage_bytes: int = 0
    cpu_usage_percent: float = 0.0
    storage_usage_bytes: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FleetMetrics:
    """Aggregated metrics across all nodes."""

    fleet_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_nodes: int = 0
    total_executions: int = 0
    avg_success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    total_capabilities: int = 0
    total_memory_bytes: int = 0
    total_storage_bytes: int = 0
    node_metrics: list[NodeMetrics] = field(default_factory=list)


class FleetAnalytics:
    """Aggregates analytics across edge nodes.

    Responsibilities:
    - Collect node metrics
    - Aggregate fleet-wide analytics
    - Track trends over time
    - Support dashboards

    Aggregation never performs cognition.
    Aggregation never controls execution.
    """

    def __init__(self, elasticsearch_adapter: Any = None):
        self._es = elasticsearch_adapter
        self._node_metrics: dict[str, list[NodeMetrics]] = {}
        self._fleet_metrics: list[FleetMetrics] = []

    def record_node_metrics(self, metrics: NodeMetrics) -> None:
        """Record metrics from an edge node."""
        if metrics.node_id not in self._node_metrics:
            self._node_metrics[metrics.node_id] = []
        self._node_metrics[metrics.node_id].append(metrics)

        if len(self._node_metrics[metrics.node_id]) > 1000:
            self._node_metrics[metrics.node_id] = self._node_metrics[metrics.node_id][
                -500:
            ]

    def aggregate_fleet(self, fleet_id: str = "default") -> FleetMetrics:
        """Aggregate metrics across all nodes."""
        all_metrics = []
        for node_metrics in self._node_metrics.values():
            if node_metrics:
                all_metrics.append(node_metrics[-1])

        if not all_metrics:
            return FleetMetrics(fleet_id=fleet_id)

        fleet = FleetMetrics(
            fleet_id=fleet_id,
            total_nodes=len(all_metrics),
            total_executions=sum(m.executions for m in all_metrics),
            avg_success_rate=sum(m.success_rate for m in all_metrics)
            / len(all_metrics),
            avg_latency_ms=sum(m.avg_latency_ms for m in all_metrics)
            / len(all_metrics),
            total_capabilities=sum(m.capabilities_active for m in all_metrics),
            total_memory_bytes=sum(m.memory_usage_bytes for m in all_metrics),
            total_storage_bytes=sum(m.storage_usage_bytes for m in all_metrics),
            node_metrics=all_metrics,
        )

        self._fleet_metrics.append(fleet)
        return fleet

    def get_node_history(self, node_id: str, limit: int = 100) -> list[NodeMetrics]:
        return self._node_metrics.get(node_id, [])[-limit:]

    def get_fleet_history(self, limit: int = 100) -> list[FleetMetrics]:
        return self._fleet_metrics[-limit:]

    async def persist_to_elasticsearch(self) -> dict[str, Any]:
        """Persist aggregated metrics to Elasticsearch."""
        if not self._es:
            return {"status": "no_elasticsearch"}

        if self._fleet_metrics:
            from src.monkey_brain.persistence.events import PersistenceEvent, EventType

            latest = self._fleet_metrics[-1]
            event = PersistenceEvent(
                event_type=EventType.METRIC_RECORDED,
                entity_type="fleet_metrics",
                entity_id=latest.fleet_id,
                data={
                    "total_nodes": latest.total_nodes,
                    "total_executions": latest.total_executions,
                    "avg_success_rate": latest.avg_success_rate,
                    "avg_latency_ms": latest.avg_latency_ms,
                },
            )
            return await self._es.persist(event)

        return {"status": "no_data"}

    def summary(self) -> dict:
        return {
            "nodes_tracked": len(self._node_metrics),
            "total_fleet_snapshots": len(self._fleet_metrics),
        }
