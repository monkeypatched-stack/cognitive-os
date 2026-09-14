"""Tests for Predictive Policy Framework (Step 11.10).

Validates that PredictionPolicy interface is pluggable,
DeterministicPredictionPolicy produces correct results, and PredictionPolicyRegistry
works for named policy lookup. Follows the same testing patterns as the
other prediction test files.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.prediction.policies import (
    PredictionPolicy,
    PredictionPolicyInput,
    PredictionPolicyResult,
    DeterministicPredictionPolicy,
    PredictionPolicyRegistry,
)
from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionModel,
    WorldTransition,
)


class _Step:
    def __init__(self, description: str) -> None:
        self.description = description


class _Plan:
    def __init__(self, steps) -> None:
        self.steps = steps


def _milk_plan():
    return _Plan(
        steps=(
            _Step("drive_to_store"),
            _Step("purchase_milk"),
        )
    )


def _milk_model(open_probability: float = 0.96) -> TransitionModel:
    return TransitionModel(
        known_transitions={
            ("", "drive_to_store"): (
                WorldTransition(
                    description="Arrived at store",
                    probability=1.0,
                    confidence=0.95,
                    resulting_world_delta={"at_store": True},
                ),
            ),
            ("", "purchase_milk"): (
                WorldTransition(
                    description="Milk purchased",
                    probability=open_probability,
                    confidence=0.9,
                    resulting_world_delta={"has_milk": True},
                ),
            ),
        }
    )


# ═══════════════════════════════════════════════════════════════════════════
# PredictionPolicy protocol
# ═══════════════════════════════════════════════════════════════════════════


class TestPredictionPolicyProtocol:
    def test_deterministic_policy_satisfies_protocol(self):
        policy = DeterministicPredictionPolicy()
        assert isinstance(policy, PredictionPolicy)

    def test_custom_policy_satisfies_protocol(self):
        class MockPolicy:
            def predict(self, input: PredictionPolicyInput) -> PredictionPolicyResult:
                return PredictionPolicyResult()

        assert isinstance(MockPolicy(), PredictionPolicy)


# ═══════════════════════════════════════════════════════════════════════════
# PredictionPolicyInput / PredictionPolicyResult
# ═══════════════════════════════════════════════════════════════════════════


class TestPolicyTypes:
    def test_input_defaults(self):
        inp = PredictionPolicyInput()
        assert inp.world_snapshot is None
        assert inp.plan is None
        assert inp.time_horizon == 0.0

    def test_result_defaults(self):
        result = PredictionPolicyResult()
        assert result.trajectories == ()
        assert result.metadata == {}


# ═══════════════════════════════════════════════════════════════════════════
# DeterministicPredictionPolicy
# ═══════════════════════════════════════════════════════════════════════════


class TestDeterministicPredictionPolicy:
    def test_produces_prediction_result(self):
        policy = DeterministicPredictionPolicy(transition_model=_milk_model())
        inp = PredictionPolicyInput(plan=_milk_plan())
        result = policy.predict(inp)
        assert isinstance(result, PredictionPolicyResult)
        assert result.result.selected is not None

    def test_baseline_scenario_selected(self):
        policy = DeterministicPredictionPolicy(transition_model=_milk_model())
        inp = PredictionPolicyInput(plan=_milk_plan())
        result = policy.predict(inp)
        assert result.result.selected.scenario_label == "Baseline"

    def test_includes_trajectories(self):
        policy = DeterministicPredictionPolicy(transition_model=_milk_model())
        inp = PredictionPolicyInput(plan=_milk_plan())
        result = policy.predict(inp)
        assert len(result.trajectories) >= 1

    def test_metadata_contains_policy_name(self):
        policy = DeterministicPredictionPolicy(transition_model=_milk_model())
        inp = PredictionPolicyInput(plan=_milk_plan())
        result = policy.predict(inp)
        assert result.metadata["policy"] == "deterministic"

    def test_with_counterfactual_assumptions(self):
        from src.monkey_brain.kernel.pipeline.prediction.counterfactuals import (
            CounterfactualAssumption,
        )

        assumption = CounterfactualAssumption(
            description="Store closed",
            category="availability",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        description="Store closed",
                        probability=0.12,
                        confidence=0.7,
                        resulting_world_delta={"has_milk": False},
                    ),
                ),
            },
        )
        policy = DeterministicPredictionPolicy(
            transition_model=_milk_model(),
            counterfactual_assumptions=(assumption,),
        )
        inp = PredictionPolicyInput(plan=_milk_plan())
        result = policy.predict(inp)
        assert len(result.trajectories) == 2
        labels = [c.scenario_label for c in result.result.candidates]
        assert "Baseline" in labels
        assert "Store closed" in labels

    def test_recommends_highest_probability_scenario(self):
        policy = DeterministicPredictionPolicy(transition_model=_milk_model(open_probability=0.96))
        inp = PredictionPolicyInput(plan=_milk_plan())
        result = policy.predict(inp)
        assert result.result.recommendation == "Execute Baseline"

    def test_rejection_threshold_affects_result(self):
        from src.monkey_brain.kernel.pipeline.prediction.counterfactuals import (
            CounterfactualAssumption,
        )

        assumption = CounterfactualAssumption(
            description="Store closed",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        probability=0.12,
                        confidence=0.7,
                        resulting_world_delta={"has_milk": False},
                    ),
                ),
            },
        )
        strict = DeterministicPredictionPolicy(
            transition_model=_milk_model(),
            counterfactual_assumptions=(assumption,),
            rejection_threshold=0.5,
        )
        inp = PredictionPolicyInput(plan=_milk_plan())
        result = strict.predict(inp)
        store_closed = next(c for c in result.result.candidates if c.scenario_label == "Store closed")
        assert store_closed.rejected is True

    def test_side_effect_free(self):
        policy = DeterministicPredictionPolicy(transition_model=_milk_model())
        inp = PredictionPolicyInput(plan=_milk_plan())
        before = id(inp.plan)
        policy.predict(inp)
        assert id(inp.plan) == before


# ═══════════════════════════════════════════════════════════════════════════
# PredictionPolicyRegistry
# ═══════════════════════════════════════════════════════════════════════════


class TestPredictionPolicyRegistry:
    def test_register_and_get(self):
        registry = PredictionPolicyRegistry()
        policy = DeterministicPredictionPolicy()
        registry.register("det", policy)
        assert registry.get("det") is policy

    def test_get_unknown_raises(self):
        registry = PredictionPolicyRegistry()
        with pytest.raises(KeyError, match="Unknown prediction policy"):
            registry.get("nonexistent")

    def test_list_policies(self):
        registry = PredictionPolicyRegistry()
        registry.register("b", DeterministicPredictionPolicy())
        registry.register("a", DeterministicPredictionPolicy())
        assert registry.list_policies() == ("a", "b")

    def test_with_defaults_has_deterministic(self):
        registry = PredictionPolicyRegistry.with_defaults()
        assert "deterministic" in registry.list_policies()
        assert isinstance(registry.get("deterministic"), DeterministicPredictionPolicy)


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
        import src.monkey_brain.kernel.pipeline.prediction.policies as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "kernel.pipeline.execution",
            "action_executor",
            "learning.",
        ):
            assert forbidden not in imports, f"policies.py must not import: {forbidden}"

    def test_depends_only_on_prediction_subpackage(self):
        import src.monkey_brain.kernel.pipeline.prediction.policies as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        for module_name in project_imports:
            assert module_name.startswith("src.monkey_brain.kernel.pipeline.prediction"), module_name
