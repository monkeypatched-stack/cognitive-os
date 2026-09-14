"""Predict-phase and Learn-phase artifacts, and the RepairRequest combinator.

Ownership:

    PredictionReport  — produced by PREDICT; knows nothing about execution
        solver_results      solver outputs keyed by solver name
        predicted_state     PredictedEPAState from SolverMesh + JEPAPredictor
        graph_analysis      structural analysis of the ExecutionGraph
        predicted_constraints  constraint satisfaction prediction
        issues              supplementary Issue list (backward compat)
        recommended_repairs repair candidates based on predictions

    LearningReport    — produced by LEARN; knows nothing about graph expansion
        observed_state      ObservedEPAState from epa_transition / graph states
        loss_vector         EPALossVector: per-component prediction error
        composite_loss      scalar adaptation signal
        knowledge_updates   capability success rates updated this cycle
        q_updates           node_id → reward for each node in this execution
        experience          JEPATransitions pushed to training buffer

    RepairRequest     — consumed by FIX; combines both artifacts
        prediction          PredictionReport
        learning            LearningReport

Separation matters:
    Predict knows nothing about execution.
    Learn knows nothing about graph expansion.
    Fix consumes both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GraphAnalysis:
    failed_nodes: list[str] = field(default_factory=list)
    unreachable_nodes: list[str] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    critical_path: list[str] = field(default_factory=list)

    @property
    def has_cycle(self) -> bool:
        return bool(self.cycles)


# ── PREDICT artifact ──────────────────────────────────────────────────────────


@dataclass
class PredictionReport:
    """Produced by PREDICT.  Contains only forward-looking information.
    No execution results.  No loss.  No observed state.
    """

    predicted_state: Any = None  # PredictedEPAState
    graph_analysis: GraphAnalysis | None = None
    solver_results: dict[str, Any] = field(default_factory=dict)  # solver_name → SolverResult
    predicted_constraints: dict[str, Any] = field(default_factory=dict)
    issues: list[Any] = field(default_factory=list)  # list[Issue] — backward compat
    recommended_repairs: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)


# ── LEARN artifact ────────────────────────────────────────────────────────────


@dataclass
class LearningReport:
    """Produced by LEARN.  Contains only observation and error information.
    No graph topology.  No repair candidates.
    """

    observed_state: Any = None  # ObservedEPAState
    loss_vector: Any = None  # EPALossVector
    composite_loss: float = 0.0
    knowledge_updates: dict[str, float] = field(default_factory=dict)  # cap_name → reward
    q_updates: dict[str, float] = field(default_factory=dict)  # node_id → reward
    experience: list[Any] = field(default_factory=list)  # JEPATransition list

    @property
    def has_loss(self) -> bool:
        return self.loss_vector is not None


# ── FIX input — combines both ─────────────────────────────────────────────────


@dataclass
class RepairRequest:
    """Consumed by FIX.  Joins PredictionReport + LearningReport so Fix has
    everything it needs without either upstream phase knowing about the other.
    """

    prediction: PredictionReport
    learning: LearningReport

    # ── Convenience accessors — Fix code reads these instead of digging in ───

    @property
    def issues(self) -> list[Any]:
        return self.prediction.issues

    @property
    def composite_loss(self) -> float:
        return self.learning.composite_loss

    @property
    def loss_vector(self) -> Any:
        return self.learning.loss_vector

    @property
    def graph_analysis(self) -> GraphAnalysis | None:
        return self.prediction.graph_analysis

    @property
    def recommended_repairs(self) -> list[str]:
        return self.prediction.recommended_repairs

    @property
    def has_issues(self) -> bool:
        return bool(self.prediction.issues)
