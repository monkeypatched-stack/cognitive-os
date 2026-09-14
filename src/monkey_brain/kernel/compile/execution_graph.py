"""ExecutionPlanSnapshot — first-class contract between planning and execution.

The execution plan snapshot is the primary artifact passed from CognitiveRuntime planning
to execution. It carries:
  - What to execute (plan: sequence of states)
  - Predicted outcomes (from simulation)
  - Constraints and metadata
  - Full provenance (what belief was used)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.compile.execution_graph")


@dataclass
class ExecutionPlanSnapshot:
    """First-class contract: plan + predictions + provenance.

    Passed from CognitiveRuntime.plan() → CognitiveRuntime.execute()
    """

    # REQUIRED fields (no defaults)
    graph_id: str  # unique identifier
    actor_id: str  # who planned this
    plan: list[str]  # execution sequence: [start → ... → goal]
    predicted_distribution: dict[str, float]  # predicted end-state distribution

    # OPTIONAL fields with defaults
    goal: str = ""  # target state
    timestamp: float = field(default_factory=time.time)
    predicted_confidence: float = 0.5  # confidence in prediction [0, 1]
    horizon: int = 64  # max execution steps
    belief_version: int = 0  # belief version used for planning
    trust_scores: dict[str, float] = field(default_factory=dict)  # trust snapshot
    metadata: dict[str, Any] = field(default_factory=dict)
    observed_path: list[tuple[str, str | None]] = field(default_factory=list)
    epistemic_loss: float = 0.0
    reached_goal: bool = False
    discoveries: list[tuple] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "actor_id": self.actor_id,
            "timestamp": self.timestamp,
            "plan": self.plan,
            "goal": self.goal,
            "predicted_distribution": self.predicted_distribution,
            "predicted_confidence": self.predicted_confidence,
            "horizon": self.horizon,
            "belief_version": self.belief_version,
            "trust_scores": self.trust_scores,
            "metadata": self.metadata,
            "observed_path": self.observed_path,
            "epistemic_loss": self.epistemic_loss,
            "reached_goal": self.reached_goal,
            "discoveries": self.discoveries,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionPlanSnapshot:
        assert isinstance(d, dict), "ExecutionPlanSnapshot.from_dict() requires dict input"
        graph_id = d.get("graph_id", "")
        actor_id = d.get("actor_id", "")
        plan = d.get("plan", [])
        assert graph_id, "ExecutionPlanSnapshot graph_id cannot be empty"
        assert actor_id, "ExecutionPlanSnapshot actor_id cannot be empty"
        assert isinstance(plan, list) and len(plan) > 0, "ExecutionPlanSnapshot plan must be non-empty list"

        predicted_dist = d.get("predicted_distribution", {})
        assert isinstance(predicted_dist, dict), "predicted_distribution must be dict"
        assert all(isinstance(v, (int, float)) and v >= 0 for v in predicted_dist.values()), (
            "predicted_distribution values must be non-negative numbers"
        )

        confidence = d.get("predicted_confidence", 0.5)
        assert 0.0 <= confidence <= 1.0, f"predicted_confidence must be in [0, 1], got {confidence}"

        loss = d.get("epistemic_loss", 0.0)
        assert loss >= 0.0, f"epistemic_loss must be non-negative, got {loss}"

        return cls(
            graph_id=graph_id,
            actor_id=actor_id,
            timestamp=d.get("timestamp", time.time()),
            plan=plan,
            goal=d.get("goal", ""),
            predicted_distribution=predicted_dist,
            predicted_confidence=confidence,
            horizon=d.get("horizon", 64),
            belief_version=d.get("belief_version", 0),
            trust_scores=d.get("trust_scores", {}),
            metadata=d.get("metadata", {}),
            observed_path=d.get("observed_path", []),
            epistemic_loss=loss,
            reached_goal=d.get("reached_goal", False),
            discoveries=d.get("discoveries", []),
        )
