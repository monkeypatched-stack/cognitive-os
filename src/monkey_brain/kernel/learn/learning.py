"""Learning - graph-centric topological learning updates.

Epistemic loss remains owned by the existing epistemic / Bellman path.
This module only turns graph transitions into graph-level topology rewards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.config import LEARNING_RATE


@dataclass
class GraphDelta:
    graph_id: str = ""
    added_nodes: list[str] = field(default_factory=list)
    removed_nodes: list[str] = field(default_factory=list)
    reordered_nodes: list[str] = field(default_factory=list)
    added_edges: list[dict[str, Any]] = field(default_factory=list)
    removed_edges: list[dict[str, Any]] = field(default_factory=list)
    modified_nodes: list[dict[str, Any]] = field(default_factory=list)
    modified_edges: list[dict[str, Any]] = field(default_factory=list)
    predicted_state: dict[str, Any] = field(default_factory=dict)
    observed_state: dict[str, Any] = field(default_factory=dict)
    node_rewards: dict[str, float] = field(default_factory=dict)
    edge_rewards: dict[str, float] = field(default_factory=dict)
    graph_reward: float = 0.0
    epistemic_loss: float = 0.0
    topological_loss: float = 0.0
    execution_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "added_nodes": list(self.added_nodes),
            "removed_nodes": list(self.removed_nodes),
            "reordered_nodes": list(self.reordered_nodes),
            "added_edges": list(self.added_edges),
            "removed_edges": list(self.removed_edges),
            "modified_nodes": list(self.modified_nodes),
            "modified_edges": list(self.modified_edges),
            "predicted_state": dict(self.predicted_state),
            "observed_state": dict(self.observed_state),
            "node_rewards": dict(self.node_rewards),
            "edge_rewards": dict(self.edge_rewards),
            "graph_reward": self.graph_reward,
            "epistemic_loss": self.epistemic_loss,
            "topological_loss": self.topological_loss,
            "execution_metrics": dict(self.execution_metrics),
        }


@dataclass
class GraphTransition:
    simulation_graph: dict[str, Any] = field(default_factory=dict)
    execution_graph: dict[str, Any] = field(default_factory=dict)
    graph_delta: GraphDelta = field(default_factory=GraphDelta)
    graph_reward: float = 0.0
    epistemic_loss: float = 0.0
    topological_loss: float = 0.0
    execution_metrics: dict[str, Any] = field(default_factory=dict)
    solver: str = ""
    consensus_score: float = 0.0
    transition_id: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.transition_id:
            self.transition_id = f"gtrans-{uuid4().hex[:8]}"
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "timestamp": self.timestamp,
            "simulation_graph": self.simulation_graph,
            "execution_graph": self.execution_graph,
            "graph_delta": self.graph_delta.to_dict(),
            "graph_reward": self.graph_reward,
            "epistemic_loss": self.epistemic_loss,
            "topological_loss": self.topological_loss,
            "execution_metrics": dict(self.execution_metrics),
            "solver": self.solver,
            "consensus_score": self.consensus_score,
        }


Transition = GraphTransition


class Learning:
    """Graph-centric learner for topology-only updates."""

    def __init__(self, learning_rate: float = LEARNING_RATE):
        self._learning_rate = learning_rate
        self._transitions: list[GraphTransition] = []
        self._scores: dict[str, float] = {}
        self._counts: dict[str, int] = {}

    def update(
        self,
        graph_id: str,
        graph_delta: GraphDelta,
        *,
        capability: str = "",
        topological_loss: float | None = None,
    ) -> GraphTransition:
        """Convert graph structure differences into a topology reward update.

        Epistemic loss is intentionally not recomputed here; callers pass it
        through on the graph delta and the existing epistemic pipeline owns it.
        """
        topo_loss = float(topological_loss if topological_loss is not None else graph_delta.topological_loss)
        reward = 1.0 - topo_loss
        graph_delta.graph_id = graph_delta.graph_id or graph_id
        graph_delta.topological_loss = topo_loss
        graph_delta.graph_reward = reward

        transition = GraphTransition(
            simulation_graph=graph_delta.predicted_state,
            execution_graph=graph_delta.observed_state,
            graph_delta=graph_delta,
            graph_reward=reward,
            epistemic_loss=graph_delta.epistemic_loss,
            topological_loss=topo_loss,
            execution_metrics=dict(graph_delta.execution_metrics),
            solver=capability,
        )
        self._transitions.append(transition)

        old_score = self._scores.get(capability, 0.0)
        count = self._counts.get(capability, 0)
        lr = self._learning_rate if count < 5 else self._learning_rate * 0.5
        self._scores[capability] = old_score + lr * (reward - old_score)
        self._counts[capability] = count + 1
        return transition

    def get_score(self, capability: str) -> float:
        return self._scores.get(capability, 0.0)

    def get_all_scores(self) -> dict[str, float]:
        return dict(self._scores)

    def get_transitions(self) -> list[GraphTransition]:
        return list(self._transitions)

    async def on_outcome(self, outcome) -> None:
        """Keep the listener graph-centric; derive topology from outcome graphs."""
        graph_id = getattr(outcome, "workflow_id", "") or getattr(outcome, "goal", "")
        graph_delta = GraphDelta(
            graph_id=graph_id,
            predicted_state=getattr(outcome, "state_before", {}) or {},
            observed_state=getattr(outcome, "state_after", {}) or {},
            execution_metrics={
                "latency_ms": float(getattr(outcome, "latency_ms", 0.0)),
                "reward": float(getattr(outcome, "reward", 0.5)),
            },
        )
        graph_delta.topological_loss = 1.0 - float(getattr(outcome, "reward", 0.5))
        self.update(
            graph_id=graph_id,
            graph_delta=graph_delta,
            capability=getattr(outcome, "capability_name", ""),
        )

    def summary(self) -> dict:
        return {
            "total_transitions": len(self._transitions),
            "capabilities_learned": len(self._scores),
            "scores": dict(self._scores),
            "counts": dict(self._counts),
        }
