"""Cloud Aggregator — aggregates data from edge nodes and maintains global world.

Layer 1 (World Inference) for cloud-side aggregation.

The CloudAggregator:
- Receives observations from edge nodes
- Updates the global world tensor via WorldLearner
- Provides world snapshots for edge synchronization
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor
from src.monkey_brain.kernel.learn.world_learner import WorldLearner

logger = logging.getLogger("agentos.cloud_aggregator")


@dataclass
class AggregatedMetrics:
    """Aggregated metrics from all edge nodes."""

    fleet_id: str = ""
    total_nodes: int = 0
    total_executions: int = 0
    avg_success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CloudAggregator:
    """Aggregates data from edge nodes and maintains global world.

    Layer 1: World Inference
        W_{t+1} = f(W_t, O_t)

    The cloud receives observations from edge nodes and fuses them
    into a consensus world model.
    """

    def __init__(self) -> None:
        self._world = SparseTransitionTensor()  # Global world tensor
        self._world_learner = WorldLearner(self._world)  # Fusion function f
        self._node_metrics: dict[str, list[dict[str, Any]]] = {}
        self._fleet_metrics: list[AggregatedMetrics] = []

    @property
    def world(self) -> SparseTransitionTensor:
        """The global world tensor."""
        return self._world

    @property
    def world_learner(self) -> WorldLearner:
        """The world learner (fusion function)."""
        return self._world_learner

    # ── Edge → Cloud sync ────────────────────────────────────────────────

    def receive_edge_sync(self, sync_data: dict) -> None:
        """Receive observations from edge node and update world.

        Layer 1: World Inference
            W_{t+1} = f(W_t, O_t)
        """
        node_id = sync_data.get("node_id", "unknown")
        observations = sync_data.get("observations", [])

        for obs in observations:
            src = obs.get("src", "")
            dst = obs.get("dst", "")
            if src and dst:
                self._world_learner.observe_transition(
                    src,
                    dst,
                    domain=obs.get("domain", "default"),
                    origin=node_id,
                )

        logger.info(
            "CloudAggregator: received %d observations from %s",
            len(observations),
            node_id,
        )

    # ── Cloud → Edge sync ────────────────────────────────────────────────

    def get_world_snapshot(self) -> dict:
        """Package world for edge sync.

        Returns a snapshot of the current world state.
        """
        transitions = []
        for src, dst in self._world:
            prob = self._world.feature(src, dst, Feature.PROBABILITY)
            transitions.append(
                {
                    "src": src,
                    "dst": dst,
                    "domain": self._world.domain_of(src),
                    "probability": prob,
                }
            )

        return {
            "revision": self._world.revision,
            "states": self._world.states(),
            "transitions": transitions,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ── Metrics aggregation ──────────────────────────────────────────────

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
            avg_success_rate=sum(m.get("success_rate", 0) for m in all_metrics) / len(all_metrics),
            avg_latency_ms=sum(m.get("latency_ms", 0) for m in all_metrics) / len(all_metrics),
        )

        self._fleet_metrics.append(fleet)
        return fleet

    def get_fleet_history(self, limit: int = 100) -> list[AggregatedMetrics]:
        return self._fleet_metrics[-limit:]

    def summary(self) -> dict:
        return {
            "nodes": len(self._node_metrics),
            "fleet_snapshots": len(self._fleet_metrics),
            "world_states": len(self._world.states()),
            "world_transitions": self._world.nnz(),
        }
