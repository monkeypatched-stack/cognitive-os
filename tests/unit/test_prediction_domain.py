"""Tests for the prediction domain model (Step 11.1).

Validates construction, defaults, and immutability for all 8 named
prediction domain types. Pure data — no algorithm, no mocking needed,
matching the style of test_planning_domain.py / test_execution_domain.py /
test_learning_domain.py.
"""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from src.monkey_brain.kernel.pipeline.prediction import (
    PredictionConfidence,
    PredictionOutcome,
    Prediction,
    PredictionRequest,
    PredictionContext,
    PredictionCandidate,
    PredictionTrace,
    PredictionResult,
)
from src.monkey_brain.kernel.pipeline.learning.domain import Provenance

# ═══════════════════════════════════════════════════════════════════════════
# PredictionConfidence
# ═══════════════════════════════════════════════════════════════════════════


class TestPredictionConfidence:
    def test_construction(self):
        conf = PredictionConfidence(
            point_estimate=0.96,
            lower_bound=0.9,
            upper_bound=0.99,
            rationale="Historical success rate at Store A",
            uncertainty_sources=("stock_data_age",),
        )
        assert conf.point_estimate == 0.96
        assert conf.uncertainty_sources == ("stock_data_age",)

    def test_defaults_are_neutral(self):
        conf = PredictionConfidence()
        assert conf.point_estimate == 0.5
        assert conf.lower_bound == 0.0
        assert conf.upper_bound == 1.0

    def test_frozen(self):
        conf = PredictionConfidence()
        with pytest.raises(FrozenInstanceError):
            conf.point_estimate = 1.0


# ═══════════════════════════════════════════════════════════════════════════
# PredictionOutcome
# ═══════════════════════════════════════════════════════════════════════════


class TestPredictionOutcome:
    def test_construction(self):
        outcome = PredictionOutcome(
            description="Store A has milk in stock",
            success=True,
            probability=0.96,
            utility=0.9,
        )
        assert outcome.success is True
        assert outcome.probability == 0.96

    def test_defaults(self):
        outcome = PredictionOutcome()
        assert outcome.success is False
        assert outcome.probability == 0.0

    def test_frozen(self):
        outcome = PredictionOutcome()
        with pytest.raises(FrozenInstanceError):
            outcome.success = True


# ═══════════════════════════════════════════════════════════════════════════
# Prediction — the central type; matches "each prediction should contain"
# ═══════════════════════════════════════════════════════════════════════════


class TestPrediction:
    def test_construction_minimal(self):
        pred = Prediction()
        assert isinstance(pred.confidence, PredictionConfidence)
        assert isinstance(pred.provenance, Provenance)
        assert pred.predicted_outcomes == ()
        assert pred.assumptions == ()
        assert pred.prediction_id  # auto-generated

    def test_prediction_ids_are_unique(self):
        assert Prediction().prediction_id != Prediction().prediction_id

    def test_contains_all_eight_required_fields(self):
        """'Each prediction should contain: Current belief state, World
        snapshot, Assumptions, Predicted outcomes, Confidence, Expected
        utility, Time horizon, Provenance.' -- Step 11.1's own spec."""
        outcome = PredictionOutcome(description="milk purchased", success=True, probability=0.96)
        pred = Prediction(
            belief_state={"goal": "acquire_milk"},
            world_snapshot={"stores": ["Store A"]},
            assumptions=("Store A is open",),
            predicted_outcomes=(outcome,),
            confidence=PredictionConfidence(point_estimate=0.96),
            expected_utility=0.88,
            time_horizon=600.0,
            provenance=Provenance(actor_id="alice"),
        )
        assert pred.belief_state == {"goal": "acquire_milk"}
        assert pred.world_snapshot == {"stores": ["Store A"]}
        assert pred.assumptions == ("Store A is open",)
        assert pred.predicted_outcomes[0].description == "milk purchased"
        assert pred.confidence.point_estimate == 0.96
        assert pred.expected_utility == 0.88
        assert pred.time_horizon == 600.0
        assert pred.provenance.actor_id == "alice"

    def test_belief_state_and_world_snapshot_accept_arbitrary_types(self):
        """Deliberately Any-typed -- must work for both belief_state.BeliefState-
        shaped data and any other representation, without importing that
        protected type here."""
        pred = Prediction(belief_state="raw_belief_repr", world_snapshot=42)
        assert pred.belief_state == "raw_belief_repr"
        assert pred.world_snapshot == 42

    def test_frozen(self):
        pred = Prediction()
        with pytest.raises(FrozenInstanceError):
            pred.expected_utility = 1.0


# ═══════════════════════════════════════════════════════════════════════════
# PredictionRequest
# ═══════════════════════════════════════════════════════════════════════════


class TestPredictionRequest:
    def test_construction(self):
        req = PredictionRequest(
            goal="acquire_milk",
            plan="drive_to_store_a",
            time_horizon=600.0,
            actor_id="alice",
        )
        assert req.goal == "acquire_milk"
        assert req.time_horizon == 600.0
        assert req.actor_id == "alice"

    def test_defaults(self):
        req = PredictionRequest()
        assert req.tenant_id == "default"
        assert req.time_horizon == 0.0
        assert req.request_id

    def test_request_ids_are_unique(self):
        assert PredictionRequest().request_id != PredictionRequest().request_id

    def test_frozen(self):
        req = PredictionRequest()
        with pytest.raises(FrozenInstanceError):
            req.goal = "x"


# ═══════════════════════════════════════════════════════════════════════════
# PredictionContext
# ═══════════════════════════════════════════════════════════════════════════


class TestPredictionContext:
    def test_composes_request_and_candidates(self):
        """Also exercises the forward reference to PredictionCandidate,
        defined later in domain.py -- must resolve correctly under
        `from __future__ import annotations`."""
        req = PredictionRequest(goal="acquire_milk")
        candidate = PredictionCandidate(scenario_label="Store open", probability=0.96)
        ctx = PredictionContext(request=req, assumptions=("Store A is open",), candidates=(candidate,))

        assert ctx.request.goal == "acquire_milk"
        assert ctx.assumptions == ("Store A is open",)
        assert ctx.candidates[0].scenario_label == "Store open"

    def test_defaults(self):
        ctx = PredictionContext()
        assert isinstance(ctx.request, PredictionRequest)
        assert ctx.candidates == ()

    def test_frozen(self):
        ctx = PredictionContext()
        with pytest.raises(FrozenInstanceError):
            ctx.assumptions = ("x",)


# ═══════════════════════════════════════════════════════════════════════════
# PredictionCandidate — matches the acceptance criteria's Scenario A/B/C shape
# ═══════════════════════════════════════════════════════════════════════════


class TestPredictionCandidate:
    def test_construction_matches_acceptance_criteria_scenario_shape(self):
        """'Scenario A: Store open, Success 96%' from Step 11's acceptance
        criteria."""
        outcome = PredictionOutcome(description="Store open, milk purchased", success=True, probability=0.96)
        candidate = PredictionCandidate(
            prediction=Prediction(predicted_outcomes=(outcome,), expected_utility=0.9),
            scenario_label="Store open",
            probability=0.96,
        )
        assert candidate.scenario_label == "Store open"
        assert candidate.probability == 0.96
        assert candidate.prediction.predicted_outcomes[0].success is True

    def test_defaults(self):
        candidate = PredictionCandidate()
        assert isinstance(candidate.prediction, Prediction)
        assert candidate.rejected is False
        assert candidate.candidate_id

    def test_candidate_ids_are_unique(self):
        assert PredictionCandidate().candidate_id != PredictionCandidate().candidate_id

    def test_rejected_candidate_carries_a_reason(self):
        candidate = PredictionCandidate(
            scenario_label="Store closed",
            rejected=True,
            rejection_reason="Success probability 12% below threshold",
        )
        assert candidate.rejected is True
        assert "12%" in candidate.rejection_reason

    def test_frozen(self):
        candidate = PredictionCandidate()
        with pytest.raises(FrozenInstanceError):
            candidate.probability = 1.0


# ═══════════════════════════════════════════════════════════════════════════
# PredictionTrace — data-only placeholder, matches Plan.trace precedent
# ═══════════════════════════════════════════════════════════════════════════


class TestPredictionTrace:
    def test_construction(self):
        trace = PredictionTrace(entries=("Simulated 3 scenarios", "Selected Scenario A"))
        assert len(trace.entries) == 2

    def test_defaults(self):
        trace = PredictionTrace()
        assert trace.entries == ()
        assert trace.trace_id

    def test_frozen(self):
        trace = PredictionTrace()
        with pytest.raises(FrozenInstanceError):
            trace.entries = ("x",)


# ═══════════════════════════════════════════════════════════════════════════
# PredictionResult — matches the acceptance criteria's decision recommendation
# ═══════════════════════════════════════════════════════════════════════════


class TestPredictionResult:
    def test_construction_matches_acceptance_criteria(self):
        """'Recommendation: Execute Scenario A' from Step 11's acceptance
        criteria."""
        scenario_a = PredictionCandidate(scenario_label="Store open", probability=0.96)
        scenario_b = PredictionCandidate(scenario_label="Store closed", probability=0.12, rejected=True)
        scenario_c = PredictionCandidate(scenario_label="Traffic delay", probability=0.81)

        result = PredictionResult(
            candidates=(scenario_a, scenario_b, scenario_c),
            selected=scenario_a,
            recommendation="Execute Scenario A",
            rationale="Highest success probability (96%) with acceptable risk.",
        )

        assert len(result.candidates) == 3
        assert result.selected.scenario_label == "Store open"
        assert result.recommendation == "Execute Scenario A"

    def test_defaults(self):
        result = PredictionResult()
        assert result.candidates == ()
        assert result.selected is None
        assert result.recommendation == ""

    def test_frozen(self):
        result = PredictionResult()
        with pytest.raises(FrozenInstanceError):
            result.recommendation = "x"


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary — model-only, no coupling to runtime/execution/learning engines
# ═══════════════════════════════════════════════════════════════════════════


def _imported_modules(mod) -> list[str]:
    """AST-based import extraction -- not a substring scan, so an
    explanatory docstring mentioning a forbidden filename by name isn't
    mistaken for a real dependency. Same fix Step 10.1 applied after a
    false positive on this exact pattern."""
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
    def test_no_learning_engine_or_execution_engine_coupling(self):
        """Step 11.1 must not import LearningEngine (belief_runtime.py's
        Learn stage, learning/integration.py) or ExecutionEngine
        (execution.py, action_executor.py) -- those stay untouched until
        Step 11.7."""
        import src.monkey_brain.kernel.pipeline.prediction.domain as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "kernel.pipeline.execution",
            "action_executor",
            "learning.integration",
            "learning.capture",
            "learning.policies",
        ):
            assert forbidden not in imports, f"prediction/domain.py must not import: {forbidden}"

    def test_only_project_dependency_is_learning_domain_provenance(self):
        """The one deliberate cross-subsystem reuse (Provenance, a pure
        data type Step 10.1 already proved has zero runtime coupling) --
        everything else must be stdlib."""
        import src.monkey_brain.kernel.pipeline.prediction.domain as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        assert project_imports == ["src.monkey_brain.kernel.pipeline.learning.domain"]

    def test_no_belief_state_or_execution_state_coupling(self):
        """belief_state/world_snapshot are deliberately Any-typed -- no
        reason to import belief_state.py or execution_state.py's real
        types at all."""
        import src.monkey_brain.kernel.pipeline.prediction.domain as mod

        imports = _imported_modules(mod)
        for module_name in imports:
            assert "belief_state" not in module_name
            assert "execution_state" not in module_name
