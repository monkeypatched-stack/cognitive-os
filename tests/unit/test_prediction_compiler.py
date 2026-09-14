"""Tests for Prediction Compiler (Step 11.8).

Validates that PredictionCompiler produces deterministic, serializable
artifacts from prediction pipeline outputs. Follows the same testing
pattern as test_prediction_domain.py / test_prediction_scenarios.py.
"""

from __future__ import annotations

import dataclasses
import pytest

from src.monkey_brain.kernel.pipeline.prediction.compiler import (
    PredictionCompiler,
    PredictionSummary,
    ScenarioSet,
    ScenarioSummary,
    ConfidenceReport,
    DecisionRecommendation,
    prediction_summary_to_dict,
    compile_prediction,
)
from src.monkey_brain.kernel.pipeline.prediction.domain import (
    Prediction,
    PredictionCandidate,
    PredictionConfidence,
    PredictionOutcome,
    PredictionResult,
)
from src.monkey_brain.kernel.pipeline.prediction.risk import RiskAssessment, RiskFactor
from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionModel,
    TransitionPredictionEngine,
    WorldTransition,
)
from src.monkey_brain.kernel.pipeline.prediction.simulation import SimulationEngine


class _Step:
    def __init__(self, description: str) -> None:
        self.description = description


class _Plan:
    def __init__(self, steps) -> None:
        self.steps = steps


def _trajectory_at_probability(probability: float):
    model = TransitionModel(
        known_transitions={
            ("", "purchase_milk"): (
                WorldTransition(
                    description="result",
                    probability=probability,
                    confidence=0.9,
                    resulting_world_delta={"x": 1},
                ),
            ),
        }
    )
    plan = _Plan(steps=(_Step("purchase_milk"),))
    return SimulationEngine(TransitionPredictionEngine(model)).simulate_plan(None, None, plan)


def _acceptance_result() -> PredictionResult:
    """Reproduces the acceptance criteria: Scenario A (96%), B (12%), C (81%)."""
    a = PredictionCandidate(
        prediction=Prediction(
            predicted_outcomes=(
                PredictionOutcome(
                    description="Store open, milk purchased",
                    success=True,
                    probability=0.96,
                ),
            ),
            confidence=PredictionConfidence(point_estimate=0.96, rationale="high confidence"),
            assumptions=("Store open",),
        ),
        scenario_label="Scenario A",
        probability=0.96,
    )
    b = PredictionCandidate(
        prediction=Prediction(
            predicted_outcomes=(PredictionOutcome(description="Store closed", success=False, probability=0.12),),
            confidence=PredictionConfidence(point_estimate=0.3, rationale="low confidence"),
            assumptions=("Store closed",),
        ),
        scenario_label="Scenario B",
        probability=0.12,
        rejected=True,
        rejection_reason="Success probability 12% below acceptance threshold 30%",
    )
    c = PredictionCandidate(
        prediction=Prediction(
            predicted_outcomes=(PredictionOutcome(description="Traffic delay", success=True, probability=0.81),),
            confidence=PredictionConfidence(point_estimate=0.81, rationale="medium confidence"),
            assumptions=("Traffic delay",),
        ),
        scenario_label="Scenario C",
        probability=0.81,
    )
    return PredictionResult(
        candidates=(a, b, c),
        selected=a,
        recommendation="Execute Scenario A",
        rationale="Scenario A has the highest viable success probability (96%).",
    )


# ═══════════════════════════════════════════════════════════════════════════
# PredictionCompiler.compile
# ═══════════════════════════════════════════════════════════════════════════


class TestCompile:
    def test_produces_prediction_summary(self):
        result = _acceptance_result()
        summary = PredictionCompiler().compile(result)
        assert isinstance(summary, PredictionSummary)

    def test_scenario_set_has_correct_counts(self):
        result = _acceptance_result()
        summary = PredictionCompiler().compile(result)
        assert summary.scenario_set.total_count == 3
        assert summary.scenario_set.viable_count == 2
        assert summary.scenario_set.rejected_count == 1

    def test_scenario_set_scenarios_are_ranked(self):
        result = _acceptance_result()
        summary = PredictionCompiler().compile(result)
        labels = [s.label for s in summary.scenario_set.scenarios]
        assert labels == ["Scenario A", "Scenario B", "Scenario C"]

    def test_rejected_scenario_flagged(self):
        result = _acceptance_result()
        summary = PredictionCompiler().compile(result)
        scenario_b = next(s for s in summary.scenario_set.scenarios if s.label == "Scenario B")
        assert scenario_b.rejected is True
        assert "12%" in scenario_b.rejection_reason

    def test_confidence_report_populated(self):
        result = _acceptance_result()
        summary = PredictionCompiler().compile(result)
        assert summary.confidence_report.overall_confidence.point_estimate > 0
        assert summary.confidence_report.missing_knowledge_count == 0

    def test_decision_recommendation_matches_input(self):
        result = _acceptance_result()
        summary = PredictionCompiler().compile(result)
        assert summary.decision.selected_scenario == "Scenario A"
        assert summary.decision.recommendation == "Execute Scenario A"
        assert summary.decision.confidence_level == "high"
        assert summary.decision.alternatives_count == 2

    def test_plan_summary_threaded_through(self):
        result = _acceptance_result()
        plan = _Plan(steps=(_Step("purchase_milk"),))
        summary = PredictionCompiler().compile(result, plan=plan)
        assert summary.plan_summary is plan

    def test_metadata_threaded_through(self):
        result = _acceptance_result()
        summary = PredictionCompiler().compile(result, metadata={"run": 42})
        assert summary.metadata == {"run": 42}

    def test_with_risk_assessments(self):
        result = _acceptance_result()
        assessments = (
            RiskAssessment(probability_of_success=0.96, risk_factors=()),
            RiskAssessment(
                probability_of_success=0.12,
                risk_factors=(RiskFactor(name="store_closed", category="missing_knowledge"),),
            ),
            RiskAssessment(probability_of_success=0.81, risk_factors=()),
        )
        summary = PredictionCompiler().compile(result, assessments=assessments)
        assert summary.confidence_report.risk_factor_count == 1
        assert summary.confidence_report.missing_knowledge_count == 1
        scenario_b = next(s for s in summary.scenario_set.scenarios if s.label == "Scenario B")
        assert "store_closed" in scenario_b.risk_factors

    def test_empty_result(self):
        result = PredictionResult()
        summary = PredictionCompiler().compile(result)
        assert summary.scenario_set.total_count == 0
        assert summary.decision.selected_scenario == ""
        assert summary.decision.confidence_level == "none"

    def test_trajectory_length_reflects_real_simulated_states(self):
        """trajectories[i] pairs with candidates[i] by position -- each
        ScenarioSummary.trajectory_length must reflect that trajectory's
        actual state count, not stay at 0 regardless of input."""
        result = _acceptance_result()
        trajectory = _trajectory_at_probability(0.96)
        trajectories = (trajectory, trajectory, trajectory)

        summary = PredictionCompiler().compile(result, trajectories=trajectories)

        assert all(s.trajectory_length == len(trajectory.states) for s in summary.scenario_set.scenarios)
        assert summary.scenario_set.scenarios[0].trajectory_length > 0

    def test_trajectory_length_stays_zero_when_no_trajectories_supplied(self):
        result = _acceptance_result()
        summary = PredictionCompiler().compile(result)
        assert all(s.trajectory_length == 0 for s in summary.scenario_set.scenarios)


# ═══════════════════════════════════════════════════════════════════════════
# Determinism
# ═══════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def test_same_inputs_produce_same_output(self):
        result = _acceptance_result()
        s1 = PredictionCompiler().compile(result)
        s2 = PredictionCompiler().compile(result)
        assert s1.scenario_set.scenarios == s2.scenario_set.scenarios
        assert s1.decision.recommendation == s2.decision.recommendation
        assert s1.confidence_report.overall_confidence == s2.confidence_report.overall_confidence


# ═══════════════════════════════════════════════════════════════════════════
# Serialization
# ═══════════════════════════════════════════════════════════════════════════


class TestSerialization:
    def test_prediction_summary_to_dict_is_json_safe(self):
        result = _acceptance_result()
        summary = PredictionCompiler().compile(result)
        d = prediction_summary_to_dict(summary)
        assert isinstance(d, dict)
        assert d["decision"]["selected_scenario"] == "Scenario A"
        assert d["scenario_set"]["total_count"] == 3

    def test_json_safe_even_with_a_real_plan_object_threaded_through(self):
        """plan_summary is Any-typed and commonly holds a real Plan object
        (see test_prediction_e2e.py) -- prediction_summary_to_dict() must
        produce output json.dumps() actually accepts, not just a dict
        that happens to embed a raw non-serializable object."""
        import json

        result = _acceptance_result()
        plan = _Plan(steps=(_Step("purchase_milk"),))
        summary = PredictionCompiler().compile(result, plan=plan)

        d = prediction_summary_to_dict(summary)

        json.dumps(d)  # must not raise
        assert isinstance(d["plan_summary"], str)

    def test_to_dict_is_deterministic(self):
        result = _acceptance_result()
        d1 = prediction_summary_to_dict(PredictionCompiler().compile(result))
        d2 = prediction_summary_to_dict(PredictionCompiler().compile(result))
        assert d1["scenario_set"]["scenarios"] == d2["scenario_set"]["scenarios"]
        assert d1["decision"]["recommendation"] == d2["decision"]["recommendation"]

    def test_frozen_dataclasses(self):
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            PredictionSummary().scenario_set = ScenarioSet()
        with pytest.raises(FrozenInstanceError):
            ScenarioSummary().label = "x"
        with pytest.raises(FrozenInstanceError):
            ConfidenceReport().overall_confidence = PredictionConfidence()
        with pytest.raises(FrozenInstanceError):
            DecisionRecommendation().recommendation = "x"


# ═══════════════════════════════════════════════════════════════════════════
# Module-level convenience
# ═══════════════════════════════════════════════════════════════════════════


class TestCompilePrediction:
    def test_module_level_wrapper(self):
        result = _acceptance_result()
        summary = compile_prediction(result)
        assert isinstance(summary, PredictionSummary)
        assert summary.decision.selected_scenario == "Scenario A"


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary
# ═══════════════════════════════════════════════════════════════════════════


def _imported_modules(mod) -> list[str]:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(mod))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


class TestOwnershipBoundary:
    def test_no_runtime_or_execution_coupling(self):
        import src.monkey_brain.kernel.pipeline.prediction.compiler as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "kernel.pipeline.execution",
            "action_executor",
            "learning.",
        ):
            assert forbidden not in imports, f"compiler.py must not import: {forbidden}"

    def test_depends_only_on_prediction_subpackage(self):
        import src.monkey_brain.kernel.pipeline.prediction.compiler as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        for module_name in project_imports:
            assert module_name.startswith("src.monkey_brain.kernel.pipeline.prediction"), module_name
