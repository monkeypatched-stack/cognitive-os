"""Prediction Trace & Explainability (Step 11.9) — makes prediction
transparent by producing a structured PredictionTrace containing:

    - scenarios explored
    - assumptions made
    - simulated transitions
    - rejected futures
    - selected prediction
    - confidence rationale

Mirrors planning/trace.py (Step 8.8), execution_runtime/trace.py (Step
9.8), and learning/trace.py (Step 10.9) exactly: a standalone, opt-in
diagnostic built from pieces the pipeline already computes. No
recomputation, no side effects, no modification to PredictionIntegratedPolicy
or any other runtime component.

Suitable for debugging, visualization, benchmarking, and governance audit.

.build_prediction_trace() assembles one from the ScenarioEvaluator's
PredictionResult plus optional SimulationTrajectories and RiskAssessments --
the same "already computed, just structured" pattern every prior trace
module established.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline.prediction.domain import (
    PredictionCandidate,
    PredictionResult,
)
from src.monkey_brain.kernel.pipeline.prediction.simulation import SimulationTrajectory
from src.monkey_brain.kernel.pipeline.prediction.risk import RiskAssessment
from src.monkey_brain.kernel.pipeline.prediction.counterfactuals import (
    CounterfactualBranch,
)


@dataclass(frozen=True)
class ScenarioTraceEntry:
    """One explored scenario's full trace: label, probability, assumptions,
    outcomes, risk factors, rejection status."""

    label: str = ""
    probability: float = 0.0
    assumptions: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = ()
    risk_factors: tuple[str, ...] = ()
    rejected: bool = False
    rejection_reason: str = ""
    transition_count: int = 0


@dataclass(frozen=True)
class PredictionTrace:
    """The complete, structured record of one prediction cycle. Designed
    for debugging, visualization, benchmarking, and governance audit."""

    trace_id: str = field(default_factory=lambda: uuid4().hex)
    scenario_entries: tuple[ScenarioTraceEntry, ...] = ()
    assumptions_explored: tuple[str, ...] = ()
    rejected_futures: tuple[str, ...] = ()
    selected_prediction: str = ""
    confidence_rationale: str = ""
    recommendation: str = ""
    rationale: str = ""
    narrative: str = ""
    created_at: float = field(default_factory=time.time)

    def explain(self) -> str:
        return self.narrative

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "scenario_entries": [dataclasses.asdict(s) for s in self.scenario_entries],
            "assumptions_explored": list(self.assumptions_explored),
            "rejected_futures": list(self.rejected_futures),
            "selected_prediction": self.selected_prediction,
            "confidence_rationale": self.confidence_rationale,
            "recommendation": self.recommendation,
            "rationale": self.rationale,
            "narrative": self.narrative,
            "created_at": self.created_at,
        }


def _scenario_entry_from_candidate(
    candidate: PredictionCandidate,
    *,
    assessment: RiskAssessment | None = None,
    trajectory: SimulationTrajectory | None = None,
) -> ScenarioTraceEntry:
    prediction = candidate.prediction
    outcomes = tuple(o.description for o in prediction.predicted_outcomes)
    risk_factors: tuple[str, ...] = ()
    transition_count = 0
    if assessment is not None:
        risk_factors = tuple(f.name for f in assessment.risk_factors)
    if trajectory is not None:
        transition_count = sum(1 for s in trajectory.states if s.applied_transition is not None)

    return ScenarioTraceEntry(
        label=candidate.scenario_label,
        probability=candidate.probability,
        assumptions=prediction.assumptions,
        outcomes=outcomes,
        risk_factors=risk_factors,
        rejected=candidate.rejected,
        rejection_reason=candidate.rejection_reason,
        transition_count=transition_count,
    )


def build_prediction_trace(
    result: PredictionResult,
    *,
    trajectories: tuple[SimulationTrajectory, ...] = (),
    assessments: tuple[RiskAssessment, ...] = (),
    branches: tuple[CounterfactualBranch, ...] = (),
) -> PredictionTrace:
    """Assembles a PredictionTrace from the pipeline's already-computed
    outputs. Pure aggregation -- no new prediction/scoring/risk logic.

    Trajectories and assessments are matched to candidates by position:
    result.candidates[i] pairs with trajectories[i] and assessments[i]
    when available. Unmatched candidates still get traced (without
    trajectory/assessment detail).
    """
    entries: list[ScenarioTraceEntry] = []
    all_assumptions: set[str] = set()
    rejected_futures: list[str] = []

    for i, candidate in enumerate(result.candidates):
        assessment = assessments[i] if i < len(assessments) else None
        trajectory = trajectories[i] if i < len(trajectories) else None
        entry = _scenario_entry_from_candidate(candidate, assessment=assessment, trajectory=trajectory)
        entries.append(entry)

        for assumption in candidate.prediction.assumptions:
            all_assumptions.add(assumption)
        if candidate.rejected:
            rejected_futures.append(f"{candidate.scenario_label}: {candidate.rejection_reason}")

    selected_label = result.selected.scenario_label if result.selected else "(none)"
    confidence_rationale = ""
    if result.selected and result.selected.prediction.confidence.rationale:
        confidence_rationale = result.selected.prediction.confidence.rationale

    narrative = _build_narrative(entries, all_assumptions, rejected_futures, result)

    return PredictionTrace(
        scenario_entries=tuple(entries),
        assumptions_explored=tuple(sorted(all_assumptions)),
        rejected_futures=tuple(rejected_futures),
        selected_prediction=selected_label,
        confidence_rationale=confidence_rationale,
        recommendation=result.recommendation,
        rationale=result.rationale,
        narrative=narrative,
    )


def _build_narrative(
    entries: list[ScenarioTraceEntry],
    assumptions: set[str],
    rejected_futures: list[str],
    result: PredictionResult,
) -> str:
    lines: list[str] = ["Prediction Trace", "    ↓"]

    lines.append(f"Scenarios Explored: {len(entries)}")
    for entry in entries:
        mark = "✗" if entry.rejected else "✓"
        assumptions_note = f" [assumptions: {', '.join(entry.assumptions)}]" if entry.assumptions else ""
        lines.append(f"  {mark} {entry.label} (probability {entry.probability:.0%}){assumptions_note}")
        if entry.outcomes:
            for outcome in entry.outcomes:
                lines.append(f"    outcome: {outcome}")
        if entry.risk_factors:
            lines.append(f"    risk factors: {', '.join(entry.risk_factors)}")
    lines.append("    ↓")

    if assumptions:
        lines.append(f"Assumptions Made: {', '.join(sorted(assumptions))}")
    else:
        lines.append("Assumptions Made: (none)")
    lines.append("    ↓")

    if rejected_futures:
        lines.append("Rejected Futures:")
        for rf in rejected_futures:
            lines.append(f"  ✗ {rf}")
    else:
        lines.append("Rejected Futures: (none)")
    lines.append("    ↓")

    lines.append(f"Selected Prediction: {result.selected.scenario_label if result.selected else '(none)'}")
    lines.append(f"Recommendation: {result.recommendation}")
    lines.append(f"Rationale: {result.rationale}")

    return "\n".join(lines)


def predict_with_trace(
    result: PredictionResult,
    *,
    trajectories: tuple[SimulationTrajectory, ...] = (),
    assessments: tuple[RiskAssessment, ...] = (),
    branches: tuple[CounterfactualBranch, ...] = (),
) -> PredictionTrace:
    """Convenience wrapper: build the trace from pipeline outputs in one
    call. Mirrors learn_with_trace() (Step 10.9) and
    IntegratedExecutionEngine.execute_with_trace() (Step 9.7/9.8)
    naming and shape."""
    return build_prediction_trace(
        result,
        trajectories=trajectories,
        assessments=assessments,
        branches=branches,
    )
