"""Tests for Multi-Scenario Evaluation (Step 11.6).

Reproduces Step 11's acceptance criteria worked example directly: Scenario
A (Store open, 96%), Scenario B (Store closed, 12%), Scenario C (Traffic
delay, 81%) -> "Execute Scenario A". ScenarioEvaluator only consumes
SimulationTrajectory, so tests build trajectories directly via a minimal
TransitionModel rather than mocking CounterfactualBranch, proving the
evaluator is genuinely agnostic about scenario provenance.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.prediction.domain import (
    PredictionCandidate,
    PredictionResult,
)
from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionModel,
    TransitionPredictionEngine,
    WorldTransition,
)
from src.monkey_brain.kernel.pipeline.prediction.simulation import SimulationEngine
from src.monkey_brain.kernel.pipeline.prediction.counterfactuals import (
    CounterfactualAssumption,
    CounterfactualEngine,
)
from src.monkey_brain.kernel.pipeline.prediction.scenarios import (
    ScenarioEvaluator,
    ScenarioInput,
    scenarios_from_counterfactuals,
)


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


def _acceptance_scenarios() -> tuple[ScenarioInput, ...]:
    return (
        ScenarioInput(
            label="Scenario A",
            trajectory=_trajectory_at_probability(0.96),
            assumptions=("Store open",),
        ),
        ScenarioInput(
            label="Scenario B",
            trajectory=_trajectory_at_probability(0.12),
            assumptions=("Store closed",),
        ),
        ScenarioInput(
            label="Scenario C",
            trajectory=_trajectory_at_probability(0.81),
            assumptions=("Traffic delay",),
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Acceptance-criteria scenario — reproduced exactly
# ═══════════════════════════════════════════════════════════════════════════


class TestAcceptanceScenario:
    def test_ranks_scenarios_by_success_probability(self):
        result = ScenarioEvaluator().evaluate_and_recommend(_acceptance_scenarios())
        assert [c.scenario_label for c in result.candidates] == [
            "Scenario A",
            "Scenario C",
            "Scenario B",
        ]

    def test_scenario_b_is_rejected_below_threshold(self):
        result = ScenarioEvaluator().evaluate_and_recommend(_acceptance_scenarios())
        scenario_b = next(c for c in result.candidates if c.scenario_label == "Scenario B")
        assert scenario_b.rejected is True
        assert "threshold" in scenario_b.rejection_reason

    def test_recommends_execute_scenario_a(self):
        result = ScenarioEvaluator().evaluate_and_recommend(_acceptance_scenarios())
        assert result.recommendation == "Execute Scenario A"
        assert result.selected.scenario_label == "Scenario A"

    def test_each_candidate_contains_predicted_outcome_probability_utility_assumptions(
        self,
    ):
        """'Each scenario should contain: predicted outcome, probability,
        utility, assumptions.' -- Step 11.6's own spec."""
        result = ScenarioEvaluator().evaluate_and_recommend(_acceptance_scenarios())
        scenario_a = next(c for c in result.candidates if c.scenario_label == "Scenario A")

        assert len(scenario_a.prediction.predicted_outcomes) >= 1
        assert scenario_a.probability == pytest.approx(0.96)
        assert scenario_a.prediction.expected_utility != 0.0
        assert scenario_a.prediction.assumptions == ("Store open",)


# ═══════════════════════════════════════════════════════════════════════════
# evaluate — one scenario at a time
# ═══════════════════════════════════════════════════════════════════════════


class TestEvaluate:
    def test_returns_a_prediction_candidate(self):
        scenario = ScenarioInput(label="Scenario A", trajectory=_trajectory_at_probability(0.96))
        candidate = ScenarioEvaluator().evaluate(scenario)
        assert isinstance(candidate, PredictionCandidate)
        assert candidate.scenario_label == "Scenario A"

    def test_probability_matches_risk_assessed_success_probability(self):
        scenario = ScenarioInput(label="X", trajectory=_trajectory_at_probability(0.75))
        candidate = ScenarioEvaluator().evaluate(scenario)
        assert candidate.probability == pytest.approx(0.75)

    def test_time_horizon_threaded_into_prediction(self):
        scenario = ScenarioInput(label="X", trajectory=_trajectory_at_probability(0.9))
        candidate = ScenarioEvaluator().evaluate(scenario, time_horizon=600.0)
        assert candidate.prediction.time_horizon == 600.0


# ═══════════════════════════════════════════════════════════════════════════
# rank — rejected always last, then descending probability
# ═══════════════════════════════════════════════════════════════════════════


class TestRank:
    def test_rejected_candidates_sort_last_regardless_of_probability(self):
        high_but_rejected = PredictionCandidate(scenario_label="High-rejected", probability=0.99, rejected=True)
        low_but_viable = PredictionCandidate(scenario_label="Low-viable", probability=0.4, rejected=False)

        ranked = ScenarioEvaluator().rank((high_but_rejected, low_but_viable))

        assert ranked[0].scenario_label == "Low-viable"
        assert ranked[1].scenario_label == "High-rejected"

    def test_ties_preserve_relative_order_among_equal_probability(self):
        a = PredictionCandidate(scenario_label="A", probability=0.5)
        b = PredictionCandidate(scenario_label="B", probability=0.5)
        ranked = ScenarioEvaluator().rank((a, b))
        assert [c.scenario_label for c in ranked] == ["A", "B"]


# ═══════════════════════════════════════════════════════════════════════════
# recommend — selection and no-viable-scenario handling
# ═══════════════════════════════════════════════════════════════════════════


class TestRecommend:
    def test_no_viable_scenarios_yields_no_selection(self):
        all_bad = (
            ScenarioInput(label="Bad A", trajectory=_trajectory_at_probability(0.1)),
            ScenarioInput(label="Bad B", trajectory=_trajectory_at_probability(0.05)),
        )
        result = ScenarioEvaluator().evaluate_and_recommend(all_bad)

        assert result.selected is None
        assert "do not execute" in result.recommendation

    def test_empty_scenarios_yields_empty_result(self):
        result = ScenarioEvaluator().recommend(())
        assert result.candidates == ()
        assert result.selected is None

    def test_single_viable_scenario_is_selected(self):
        result = ScenarioEvaluator().evaluate_and_recommend(
            (ScenarioInput(label="Only", trajectory=_trajectory_at_probability(0.9)),)
        )
        assert result.selected.scenario_label == "Only"

    def test_rationale_names_every_scenario_considered(self):
        result = ScenarioEvaluator().evaluate_and_recommend(_acceptance_scenarios())
        assert "Scenario A" in result.rationale
        assert "Scenario B" in result.rationale
        assert "Scenario C" in result.rationale

    def test_custom_rejection_threshold(self):
        lenient = ScenarioEvaluator(rejection_threshold=0.05)
        result = lenient.evaluate_and_recommend(_acceptance_scenarios())
        scenario_b = next(c for c in result.candidates if c.scenario_label == "Scenario B")
        assert scenario_b.rejected is False


# ═══════════════════════════════════════════════════════════════════════════
# scenarios_from_counterfactuals — bridges Step 11.4 output
# ═══════════════════════════════════════════════════════════════════════════


class TestScenariosFromCounterfactuals:
    def test_baseline_plus_one_branch_per_assumption(self):
        model = TransitionModel(
            known_transitions={
                ("", "purchase_milk"): (
                    WorldTransition(
                        description="Milk purchased",
                        probability=0.96,
                        confidence=0.9,
                        resulting_world_delta={"actor.has_milk": True},
                    ),
                ),
            }
        )
        plan = _Plan(steps=(_Step("purchase_milk"),))
        cf_engine = CounterfactualEngine(model)
        baseline = cf_engine.simulate_baseline(None, None, plan)
        assumption = CounterfactualAssumption(
            description="Store closed",
            category="availability",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        probability=0.12,
                        confidence=0.7,
                        resulting_world_delta={"actor.has_milk": False},
                    ),
                )
            },
        )
        branches = cf_engine.generate_branches(None, None, plan, (assumption,))

        scenarios = scenarios_from_counterfactuals("Baseline", baseline, branches)

        assert len(scenarios) == 2
        assert scenarios[0].label == "Baseline"
        assert scenarios[1].label == "Store closed"

    def test_bridged_scenarios_evaluate_and_recommend_correctly(self):
        model = TransitionModel(
            known_transitions={
                ("", "purchase_milk"): (
                    WorldTransition(
                        description="Milk purchased",
                        probability=0.96,
                        confidence=0.9,
                        resulting_world_delta={"actor.has_milk": True},
                    ),
                ),
            }
        )
        plan = _Plan(steps=(_Step("purchase_milk"),))
        cf_engine = CounterfactualEngine(model)
        baseline = cf_engine.simulate_baseline(None, None, plan)
        assumption = CounterfactualAssumption(
            description="Store closed",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        probability=0.12,
                        confidence=0.7,
                        resulting_world_delta={"actor.has_milk": False},
                    ),
                )
            },
        )
        branches = cf_engine.generate_branches(None, None, plan, (assumption,))
        scenarios = scenarios_from_counterfactuals("Baseline", baseline, branches)

        result = ScenarioEvaluator().evaluate_and_recommend(scenarios)

        assert result.recommendation == "Execute Baseline"

    def test_assumption_with_no_description_falls_back_to_category(self):
        model = TransitionModel()
        plan = _Plan(steps=())
        cf_engine = CounterfactualEngine(model)
        baseline = cf_engine.simulate_baseline(None, None, plan)
        assumption = CounterfactualAssumption(description="", category="budget")
        branches = cf_engine.generate_branches(None, None, plan, (assumption,))

        scenarios = scenarios_from_counterfactuals("Baseline", baseline, branches)

        assert scenarios[1].label == "budget"


# ═══════════════════════════════════════════════════════════════════════════
# Immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_scenario_input_frozen(self):
        from dataclasses import FrozenInstanceError

        scenario = ScenarioInput()
        with pytest.raises(FrozenInstanceError):
            scenario.label = "x"


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
    def test_no_execution_or_runtime_coupling(self):
        import src.monkey_brain.kernel.pipeline.prediction.scenarios as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "kernel.pipeline.execution",
            "action_executor",
            "learning.",
        ):
            assert forbidden not in imports, f"scenarios.py must not import: {forbidden}"

    def test_does_not_import_counterfactuals_module(self):
        """scenarios_from_counterfactuals() duck-types CounterfactualBranch
        rather than importing counterfactuals.py -- confirmed by import
        inspection, not just by reading the code."""
        import src.monkey_brain.kernel.pipeline.prediction.scenarios as mod

        imports = _imported_modules(mod)
        assert not any("counterfactuals" in m for m in imports)

    def test_depends_only_on_prediction_subpackage(self):
        import src.monkey_brain.kernel.pipeline.prediction.scenarios as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        for module_name in project_imports:
            assert module_name.startswith("src.monkey_brain.kernel.pipeline.prediction"), module_name
