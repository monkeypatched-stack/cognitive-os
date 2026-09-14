"""Tests for Prediction Trace & Explainability (Step 11.9).

Validates that PredictionTrace captures all required information:
scenarios explored, assumptions made, simulated transitions, rejected
futures, selected prediction, confidence rationale. Follows the same
testing patterns as test_learning_trace.py and test_execution_trace.py.
"""

from __future__ import annotations

import dataclasses
import pytest

from src.monkey_brain.kernel.pipeline.prediction.trace import (
    PredictionTrace,
    ScenarioTraceEntry,
    build_prediction_trace,
    predict_with_trace,
)
from src.monkey_brain.kernel.pipeline.prediction.domain import (
    Prediction,
    PredictionCandidate,
    PredictionConfidence,
    PredictionOutcome,
    PredictionResult,
)


def _acceptance_result() -> PredictionResult:
    a = PredictionCandidate(
        prediction=Prediction(
            predicted_outcomes=(
                PredictionOutcome(
                    description="Store open, milk purchased",
                    success=True,
                    probability=0.96,
                ),
            ),
            confidence=PredictionConfidence(point_estimate=0.96, rationale="high confidence from historical data"),
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
# build_prediction_trace
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildPredictionTrace:
    def test_produces_prediction_trace(self):
        trace = build_prediction_trace(_acceptance_result())
        assert isinstance(trace, PredictionTrace)

    def test_captures_all_scenarios(self):
        trace = build_prediction_trace(_acceptance_result())
        assert len(trace.scenario_entries) == 3

    def test_selected_prediction_matches(self):
        trace = build_prediction_trace(_acceptance_result())
        assert trace.selected_prediction == "Scenario A"

    def test_rejected_futures_captured(self):
        trace = build_prediction_trace(_acceptance_result())
        assert len(trace.rejected_futures) == 1
        assert "Scenario B" in trace.rejected_futures[0]

    def test_assumptions_explored(self):
        trace = build_prediction_trace(_acceptance_result())
        assert "Store open" in trace.assumptions_explored
        assert "Store closed" in trace.assumptions_explored
        assert "Traffic delay" in trace.assumptions_explored

    def test_confidence_rationale_from_selected(self):
        trace = build_prediction_trace(_acceptance_result())
        assert trace.confidence_rationale == "high confidence from historical data"

    def test_recommendation_and_rationale(self):
        trace = build_prediction_trace(_acceptance_result())
        assert trace.recommendation == "Execute Scenario A"
        assert "96%" in trace.rationale

    def test_narrative_is_nonempty(self):
        trace = build_prediction_trace(_acceptance_result())
        assert len(trace.narrative) > 0
        assert "Prediction Trace" in trace.narrative

    def test_empty_result(self):
        trace = build_prediction_trace(PredictionResult())
        assert trace.scenario_entries == ()
        assert trace.selected_prediction == "(none)"


# ═══════════════════════════════════════════════════════════════════════════
# ScenarioTraceEntry
# ═══════════════════════════════════════════════════════════════════════════


class TestScenarioTraceEntry:
    def test_viable_scenario_not_rejected(self):
        trace = build_prediction_trace(_acceptance_result())
        entry_a = next(e for e in trace.scenario_entries if e.label == "Scenario A")
        assert entry_a.rejected is False
        assert entry_a.probability == 0.96

    def test_rejected_scenario_flagged(self):
        trace = build_prediction_trace(_acceptance_result())
        entry_b = next(e for e in trace.scenario_entries if e.label == "Scenario B")
        assert entry_b.rejected is True
        assert "12%" in entry_b.rejection_reason

    def test_outcomes_captured(self):
        trace = build_prediction_trace(_acceptance_result())
        entry_a = next(e for e in trace.scenario_entries if e.label == "Scenario A")
        assert len(entry_a.outcomes) >= 1
        assert "Store open" in entry_a.outcomes[0]


# ═══════════════════════════════════════════════════════════════════════════
# explain / to_dict
# ═══════════════════════════════════════════════════════════════════════════


class TestExplain:
    def test_explain_returns_narrative(self):
        trace = build_prediction_trace(_acceptance_result())
        assert trace.explain() == trace.narrative

    def test_narrative_contains_key_sections(self):
        trace = build_prediction_trace(_acceptance_result())
        assert "Scenarios Explored" in trace.narrative
        assert "Assumptions Made" in trace.narrative
        assert "Rejected Futures" in trace.narrative
        assert "Selected Prediction" in trace.narrative
        assert "Recommendation" in trace.narrative

    def test_to_dict_is_json_safe(self):
        trace = build_prediction_trace(_acceptance_result())
        d = trace.to_dict()
        assert isinstance(d, dict)
        assert d["selected_prediction"] == "Scenario A"
        assert len(d["scenario_entries"]) == 3
        assert isinstance(d["narrative"], str)


# ═══════════════════════════════════════════════════════════════════════════
# Immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_prediction_trace_frozen(self):
        trace = build_prediction_trace(_acceptance_result())
        with pytest.raises(dataclasses.FrozenInstanceError):
            trace.selected_prediction = "x"

    def test_scenario_trace_entry_frozen(self):
        entry = ScenarioTraceEntry(label="X")
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.label = "Y"


# ═══════════════════════════════════════════════════════════════════════════
# predict_with_trace convenience wrapper
# ═══════════════════════════════════════════════════════════════════════════


class TestPredictWithTrace:
    def test_wrapper_matches_build(self):
        result = _acceptance_result()
        t1 = build_prediction_trace(result)
        t2 = predict_with_trace(result)
        assert t1.scenario_entries == t2.scenario_entries
        assert t1.recommendation == t2.recommendation


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
        import src.monkey_brain.kernel.pipeline.prediction.trace as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "kernel.pipeline.execution",
            "action_executor",
            "learning.",
        ):
            assert forbidden not in imports, f"trace.py must not import: {forbidden}"

    def test_depends_only_on_prediction_subpackage(self):
        import src.monkey_brain.kernel.pipeline.prediction.trace as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        for module_name in project_imports:
            assert module_name.startswith("src.monkey_brain.kernel.pipeline.prediction"), module_name
