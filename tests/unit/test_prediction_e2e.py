"""End-to-end acceptance criteria tests for Step 11 (11.7-11.10).

Validates the full prediction lifecycle from the spec:

Given: "Get 2 liters of milk."

Plan: Drive to Store A
    ↓
Simulation
    ↓
Scenario A: Store open, Success 96%
Scenario B: Store closed, Success 12%
Scenario C: Traffic delay, Success 81%
    ↓
Recommendation: Execute Scenario A
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.prediction.compiler import (
    PredictionCompiler,
    prediction_summary_to_dict,
    compile_prediction,
)
from src.monkey_brain.kernel.pipeline.prediction.trace import (
    build_prediction_trace,
    predict_with_trace,
)
from src.monkey_brain.kernel.pipeline.prediction.policies import (
    DeterministicPredictionPolicy,
    PredictionPolicyInput,
    PredictionPolicyRegistry,
)
from src.monkey_brain.kernel.pipeline.prediction.domain import (
    Prediction,
    PredictionCandidate,
    PredictionConfidence,
    PredictionOutcome,
    PredictionResult,
)
from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionModel,
    WorldTransition,
)
from src.monkey_brain.kernel.pipeline.prediction.counterfactuals import (
    CounterfactualAssumption,
)
from src.monkey_brain.kernel.pipeline.prediction.integration import (
    PredictionIntegratedPolicy,
    prediction_result_to_dict,
)


class _Step:
    def __init__(self, description: str) -> None:
        self.description = description


class _Plan:
    def __init__(self, steps) -> None:
        self.steps = steps


def _acceptance_plan():
    return _Plan(
        steps=(
            _Step("Drive to Store A"),
            _Step("Purchase 2 liters of milk"),
        )
    )


def _acceptance_model():
    return TransitionModel(
        known_transitions={
            ("", "Drive to Store A"): (
                WorldTransition(
                    description="Arrived at Store A",
                    probability=1.0,
                    confidence=0.95,
                    resulting_world_delta={"at_store_a": True},
                ),
            ),
            ("", "Purchase 2 liters of milk"): (
                WorldTransition(
                    description="Milk purchased successfully",
                    probability=0.96,
                    confidence=0.9,
                    resulting_world_delta={"has_milk": True, "milk_liters": 2},
                ),
            ),
        }
    )


def _acceptance_assumptions():
    return (
        CounterfactualAssumption(
            description="Store closed",
            category="availability",
            transition_overrides={
                "Purchase 2 liters of milk": (
                    WorldTransition(
                        description="Store closed, purchase failed",
                        probability=0.12,
                        confidence=0.7,
                        resulting_world_delta={"has_milk": False},
                    ),
                ),
            },
        ),
        CounterfactualAssumption(
            description="Traffic delay",
            category="transportation",
            transition_overrides={
                "Drive to Store A": (
                    WorldTransition(
                        description="Arrived at Store A (delayed)",
                        probability=0.81,
                        confidence=0.8,
                        resulting_world_delta={"at_store_a": True, "delayed": True},
                    ),
                ),
            },
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Full acceptance criteria: "Get 2 liters of milk"
# ═══════════════════════════════════════════════════════════════════════════


class TestAcceptanceCriteria:
    def test_full_lifecycle_produces_recommendation(self):
        """Plan -> Simulation -> Scenario A/B/C -> Recommendation."""
        policy = DeterministicPredictionPolicy(
            transition_model=_acceptance_model(),
            counterfactual_assumptions=_acceptance_assumptions(),
        )
        inp = PredictionPolicyInput(plan=_acceptance_plan(), time_horizon=600.0)
        result = policy.predict(inp)

        assert result.result.selected is not None
        assert result.result.selected.scenario_label == "Baseline"
        assert "Execute" in result.result.recommendation

    def test_three_scenarios_generated(self):
        """Scenario A (Baseline), Scenario B (Store closed), Scenario C (Traffic delay)."""
        policy = DeterministicPredictionPolicy(
            transition_model=_acceptance_model(),
            counterfactual_assumptions=_acceptance_assumptions(),
        )
        inp = PredictionPolicyInput(plan=_acceptance_plan())
        result = policy.predict(inp)

        labels = [c.scenario_label for c in result.result.candidates]
        assert len(labels) == 3
        assert "Baseline" in labels
        assert "Store closed" in labels
        assert "Traffic delay" in labels

    def test_highest_probability_scenario_selected(self):
        """Scenario A (96%) > Scenario C (81%) > Scenario B (12%)."""
        policy = DeterministicPredictionPolicy(
            transition_model=_acceptance_model(),
            counterfactual_assumptions=_acceptance_assumptions(),
        )
        inp = PredictionPolicyInput(plan=_acceptance_plan())
        result = policy.predict(inp)

        ranked = result.result.candidates
        assert ranked[0].probability >= ranked[1].probability
        assert ranked[1].probability >= ranked[2].probability

    def test_low_probability_scenario_rejected(self):
        """Scenario B (12%) is below the rejection threshold."""
        policy = DeterministicPredictionPolicy(
            transition_model=_acceptance_model(),
            counterfactual_assumptions=_acceptance_assumptions(),
            rejection_threshold=0.3,
        )
        inp = PredictionPolicyInput(plan=_acceptance_plan())
        result = policy.predict(inp)

        store_closed = next(c for c in result.result.candidates if c.scenario_label == "Store closed")
        assert store_closed.rejected is True

    def test_compiler_produces_full_artifact(self):
        """PredictionCompiler compiles the full acceptance criteria result."""
        policy = DeterministicPredictionPolicy(
            transition_model=_acceptance_model(),
            counterfactual_assumptions=_acceptance_assumptions(),
        )
        inp = PredictionPolicyInput(plan=_acceptance_plan())
        result = policy.predict(inp)

        summary = compile_prediction(
            result.result,
            trajectories=result.trajectories,
            plan=inp.plan,
        )
        assert summary.decision.selected_scenario == "Baseline"
        assert summary.scenario_set.total_count == 3
        assert summary.scenario_set.rejected_count >= 1
        assert summary.decision.confidence_level in ("high", "medium")

    def test_trace_captures_explainability(self):
        """PredictionTrace narrates the full prediction for debugging."""
        policy = DeterministicPredictionPolicy(
            transition_model=_acceptance_model(),
            counterfactual_assumptions=_acceptance_assumptions(),
        )
        inp = PredictionPolicyInput(plan=_acceptance_plan())
        result = policy.predict(inp)

        trace = build_prediction_trace(result.result, trajectories=result.trajectories)
        assert len(trace.scenario_entries) == 3
        assert "Store closed" in trace.assumptions_explored
        assert "Traffic delay" in trace.assumptions_explored
        assert "Prediction Trace" in trace.narrative
        assert trace.to_dict()["selected_prediction"] == "Baseline"

    def test_serializable_summary(self):
        """prediction_summary_to_dict produces JSON-safe output."""
        policy = DeterministicPredictionPolicy(
            transition_model=_acceptance_model(),
            counterfactual_assumptions=_acceptance_assumptions(),
        )
        inp = PredictionPolicyInput(plan=_acceptance_plan())
        result = policy.predict(inp)

        summary = compile_prediction(result.result, plan=inp.plan)
        d = prediction_summary_to_dict(summary)
        assert isinstance(d, dict)
        assert d["decision"]["selected_scenario"] == "Baseline"

    def test_policy_registry_end_to_end(self):
        """Registry-based policy lookup produces the same result."""
        registry = PredictionPolicyRegistry.with_defaults()
        policy = registry.get("deterministic")
        inp = PredictionPolicyInput(plan=_acceptance_plan())
        result = policy.predict(inp)
        assert result.result.selected is not None


# ═══════════════════════════════════════════════════════════════════════════
# Backward compatibility — existing PredictionIntegratedPolicy still works
# ═══════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    def test_prediction_result_to_dict_still_works(self):
        a = PredictionCandidate(scenario_label="A", probability=0.9)
        result = PredictionResult(candidates=(a,), selected=a, recommendation="Execute A")
        d = prediction_result_to_dict(result)
        assert d["recommendation"] == "Execute A"

    def test_prediction_integrated_policy_construction(self):
        policy = PredictionIntegratedPolicy()
        assert policy is not None

    def test_domain_types_still_importable(self):
        from src.monkey_brain.kernel.pipeline.prediction import (
            PredictionConfidence,
            PredictionOutcome,
            Prediction,
            PredictionRequest,
            PredictionContext,
            PredictionCandidate,
            PredictionResult,
        )

        assert PredictionConfidence().point_estimate == 0.5
