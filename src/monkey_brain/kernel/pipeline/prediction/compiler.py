"""Prediction Compiler (Step 11.8) — compiles prediction pipeline outputs
into deterministic, serializable artifacts.

Inputs: candidate plans, SimulationTrajectory results, RiskAssessment
analysis, ScenarioEvaluator rankings.

Outputs: PredictionSummary, ScenarioSet, ConfidenceReport,
DecisionRecommendation.

The compiler is a pure-function aggregator: it reads pieces the pipeline
already computed (ScenarioEvaluator.evaluate_and_recommend() produces a
PredictionResult containing ranked PredictionCandidates, each carrying a
Prediction with confidence and predicted outcomes, and a RiskAssessment's
risk_factors are derivable from the trajectory). It derives no new
predictions of its own -- it structures and serializes existing outputs
the same way learning/phi.py (PhiCompiler) structured learning results
and planning/trace.py structured planning results.

All output types are frozen dataclasses with one Any-typed field
(PredictionSummary.plan_summary, mirroring the deliberate Any-typing
domain.py uses for plan/belief_state at module boundaries) -- serialized
via the same graceful-degradation _safe_serialize() convention Step
10.2's learning/capture.py (experience_to_dict) and Step 10.7's
learning/integration.py (prediction_result_to_dict) already established,
so a real Plan object threaded through as `plan=...` falls back to str()
instead of breaking json.dumps() on the result.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline.prediction.domain import (
    PredictionCandidate,
    PredictionConfidence,
    PredictionResult,
)
from src.monkey_brain.kernel.pipeline.prediction.risk import RiskAssessment, RiskFactor
from src.monkey_brain.kernel.pipeline.prediction.simulation import SimulationTrajectory


@dataclass(frozen=True)
class ScenarioSummary:
    """One scenario's serializable summary: label, probability, outcome
    description, assumptions, and whether it was rejected."""

    label: str = ""
    probability: float = 0.0
    predicted_outcome: str = ""
    assumptions: tuple[str, ...] = ()
    rejected: bool = False
    rejection_reason: str = ""
    risk_factors: tuple[str, ...] = ()
    trajectory_length: int = 0


@dataclass(frozen=True)
class ScenarioSet:
    """All scenarios considered during one prediction cycle, in ranked
    order. Deterministic: same inputs always produce the same ScenarioSet."""

    set_id: str = field(default_factory=lambda: uuid4().hex)
    scenarios: tuple[ScenarioSummary, ...] = ()
    total_count: int = 0
    viable_count: int = 0
    rejected_count: int = 0


@dataclass(frozen=True)
class ConfidenceReport:
    """Structured confidence analysis across all evaluated scenarios."""

    report_id: str = field(default_factory=lambda: uuid4().hex)
    overall_confidence: PredictionConfidence = field(default_factory=PredictionConfidence)
    best_case_confidence: PredictionConfidence = field(default_factory=PredictionConfidence)
    worst_case_confidence: PredictionConfidence = field(default_factory=PredictionConfidence)
    risk_factor_count: int = 0
    missing_knowledge_count: int = 0
    uncertainty_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionRecommendation:
    """The final, fully-serializable decision output: what to do, why, and
    what was considered."""

    recommendation_id: str = field(default_factory=lambda: uuid4().hex)
    selected_scenario: str = ""
    recommendation: str = ""
    rationale: str = ""
    confidence_level: str = ""
    """'high' | 'medium' | 'low' | 'none' -- derived from the selected
    candidate's confidence point_estimate."""
    alternatives_count: int = 0


@dataclass(frozen=True)
class PredictionSummary:
    """The compiler's top-level artifact: one compact, fully-serializable
    record of an entire prediction cycle, suitable for persistence,
    sharing, or governance audit."""

    summary_id: str = field(default_factory=lambda: uuid4().hex)
    plan_summary: Any = None
    scenario_set: ScenarioSet = field(default_factory=ScenarioSet)
    confidence_report: ConfidenceReport = field(default_factory=ConfidenceReport)
    decision: DecisionRecommendation = field(default_factory=DecisionRecommendation)
    compiled_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class PredictionCompiler:
    """Compiles prediction pipeline outputs into deterministic, reusable
    artifacts. Stateless -- no instance state beyond construction defaults."""

    def compile(
        self,
        result: PredictionResult,
        *,
        trajectories: tuple[SimulationTrajectory, ...] = (),
        assessments: tuple[RiskAssessment, ...] = (),
        plan: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> PredictionSummary:
        scenario_set = self._compile_scenario_set(result, trajectories, assessments)
        confidence_report = self._compile_confidence_report(result, assessments)
        decision = self._compile_decision(result)

        return PredictionSummary(
            plan_summary=plan,
            scenario_set=scenario_set,
            confidence_report=confidence_report,
            decision=decision,
            metadata=dict(metadata) if metadata else {},
        )

    # ── Internals ────────────────────────────────────────────────────────

    def _compile_scenario_set(
        self,
        result: PredictionResult,
        trajectories: tuple[SimulationTrajectory, ...],
        assessments: tuple[RiskAssessment, ...],
    ) -> ScenarioSet:
        summaries: list[ScenarioSummary] = []
        for i, candidate in enumerate(result.candidates):
            risk_factors: tuple[str, ...] = ()
            trajectory_length = 0
            if i < len(assessments):
                risk_factors = tuple(f.name for f in assessments[i].risk_factors)
            if i < len(trajectories):
                trajectory_length = len(trajectories[i].states)
            prediction = candidate.prediction
            predicted_outcome = ""
            if prediction.predicted_outcomes:
                predicted_outcome = prediction.predicted_outcomes[0].description
            summaries.append(
                ScenarioSummary(
                    label=candidate.scenario_label,
                    probability=candidate.probability,
                    predicted_outcome=predicted_outcome,
                    assumptions=prediction.assumptions,
                    rejected=candidate.rejected,
                    rejection_reason=candidate.rejection_reason,
                    risk_factors=risk_factors,
                    trajectory_length=trajectory_length,
                )
            )

        viable = sum(1 for s in summaries if not s.rejected)
        return ScenarioSet(
            scenarios=tuple(summaries),
            total_count=len(summaries),
            viable_count=viable,
            rejected_count=len(summaries) - viable,
        )

    def _compile_confidence_report(
        self,
        result: PredictionResult,
        assessments: tuple[RiskAssessment, ...],
    ) -> ConfidenceReport:
        candidates = [c for c in result.candidates if not c.rejected]
        if not candidates:
            return ConfidenceReport()

        all_confidences = [c.prediction.confidence for c in candidates]
        overall = PredictionConfidence(
            point_estimate=(min(c.point_estimate for c in all_confidences) if all_confidences else 0.0),
            lower_bound=(min(c.lower_bound for c in all_confidences) if all_confidences else 0.0),
            upper_bound=(max(c.upper_bound for c in all_confidences) if all_confidences else 1.0),
            rationale=f"aggregated across {len(all_confidences)} viable scenario(s)",
            uncertainty_sources=tuple(src for c in all_confidences for src in c.uncertainty_sources),
        )

        best = max(candidates, key=lambda c: c.probability)
        worst = min(candidates, key=lambda c: c.probability)

        all_risk_factors: list[RiskFactor] = []
        for a in assessments:
            all_risk_factors.extend(a.risk_factors)
        missing_knowledge = sum(1 for f in all_risk_factors if f.category == "missing_knowledge")
        uncertainty_sources = tuple(dict.fromkeys(f.name for f in all_risk_factors))

        return ConfidenceReport(
            overall_confidence=overall,
            best_case_confidence=best.prediction.confidence,
            worst_case_confidence=worst.prediction.confidence,
            risk_factor_count=len(all_risk_factors),
            missing_knowledge_count=missing_knowledge,
            uncertainty_sources=uncertainty_sources,
        )

    def _compile_decision(self, result: PredictionResult) -> DecisionRecommendation:
        selected_label = result.selected.scenario_label if result.selected else ""
        confidence_level = _confidence_level(result.selected) if result.selected else "none"
        alternatives = len(result.candidates) - (1 if result.selected else 0)

        return DecisionRecommendation(
            selected_scenario=selected_label,
            recommendation=result.recommendation,
            rationale=result.rationale,
            confidence_level=confidence_level,
            alternatives_count=alternatives,
        )


def _confidence_level(candidate: PredictionCandidate | None) -> str:
    if candidate is None:
        return "none"
    p = candidate.probability
    if p >= 0.9:
        return "high"
    if p >= 0.7:
        return "medium"
    if p >= 0.3:
        return "low"
    return "none"


def _safe_serialize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _safe_serialize(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {k: _safe_serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_serialize(v) for v in value]
    return str(value)


def prediction_summary_to_dict(summary: PredictionSummary) -> dict[str, Any]:
    """Genuinely JSON-safe serialization -- PredictionSummary.plan_summary
    is Any-typed and commonly holds a real Plan object (see
    test_prediction_e2e.py's usage), so a bare dataclasses.asdict() would
    embed that raw object in the result and fail json.dumps() downstream.
    Falls back to str() for anything not already a primitive/dataclass/
    dict/list, the same convention every other *_to_dict() in this
    codebase uses."""
    return _safe_serialize(summary)


def compile_prediction(
    result: PredictionResult,
    *,
    trajectories: tuple[SimulationTrajectory, ...] = (),
    assessments: tuple[RiskAssessment, ...] = (),
    plan: Any = None,
    metadata: dict[str, Any] | None = None,
) -> PredictionSummary:
    """Module-level convenience wrapper around PredictionCompiler().compile()."""
    return PredictionCompiler().compile(
        result,
        trajectories=trajectories,
        assessments=assessments,
        plan=plan,
        metadata=metadata,
    )
