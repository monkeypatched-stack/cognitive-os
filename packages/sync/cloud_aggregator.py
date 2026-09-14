"""Cloud Aggregator — aggregates data from edge nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class AggregatedMetrics:
    """Aggregated metrics from all edge nodes."""

    fleet_id: str = ""
    total_nodes: int = 0
    total_executions: int = 0
    avg_success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class CloudAggregator:
    """Aggregates data from edge nodes."""

    def __init__(self):
        self._node_metrics: dict[str, list[dict[str, Any]]] = {}
        self._fleet_metrics: list[AggregatedMetrics] = []

    def record_node_metrics(self, node_id: str, metrics: dict[str, Any]) -> None:
        """Record metrics from an edge node."""
        if node_id not in self._node_metrics:
            self._node_metrics[node_id] = []
        self._node_metrics[node_id].append(metrics)

    def aggregate_fleet(self, fleet_id: str = "default") -> AggregatedMetrics:
        """Aggregate metrics across all nodes."""
        all_metrics = []
        for node_metrics in self._node_metrics.values():
            if node_metrics:
                all_metrics.append(node_metrics[-1])

        if not all_metrics:
            return AggregatedMetrics(fleet_id=fleet_id)

        fleet = AggregatedMetrics(
            fleet_id=fleet_id,
            total_nodes=len(all_metrics),
            total_executions=sum(m.get("executions", 0) for m in all_metrics),
            avg_success_rate=sum(m.get("success_rate", 0) for m in all_metrics)
            / len(all_metrics),
            avg_latency_ms=sum(m.get("latency_ms", 0) for m in all_metrics)
            / len(all_metrics),
        )

        self._fleet_metrics.append(fleet)
        return fleet

    def get_fleet_history(self, limit: int = 100) -> list[AggregatedMetrics]:
        return self._fleet_metrics[-limit:]

    def summary(self) -> dict:
        return {
            "nodes": len(self._node_metrics),
            "fleet_snapshots": len(self._fleet_metrics),
        }
