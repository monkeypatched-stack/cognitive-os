"""Tests for Risk & Uncertainty Analysis (Step 11.5).

RiskEngine.assess() is a pure function over a SimulationTrajectory -- no
CognitiveState, no execution. Exercises both a confident, low-risk
scenario and a genuinely risky one (low probability + uncertain +
missing-knowledge steps together) to prove every metric actually responds
to the underlying transitions, not just returns plausible-looking defaults.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.prediction.domain import (
    Prediction,
    PredictionConfidence,
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
from src.monkey_brain.kernel.pipeline.prediction.risk import (
    RiskAssessment,
    RiskEngine,
    RiskFactor,
    enrich_prediction,
)


class _Step:
    def __init__(self, description: str) -> None:
        self.description = description


class _Plan:
    def __init__(self, steps) -> None:
        self.steps = steps


def _confident_trajectory():
    model = TransitionModel(
        known_transitions={
            ("", "drive_to_store_a"): (
                WorldTransition(
                    description="Arrived, store open",
                    probability=0.96,
                    confidence=0.9,
                    resulting_world_delta={"actor.location": "store_a"},
                ),
            ),
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
    plan = _Plan(steps=(_Step("drive_to_store_a"), _Step("purchase_milk")))
    return SimulationEngine(TransitionPredictionEngine(model)).simulate_plan(None, None, plan)


def _risky_trajectory():
    model = TransitionModel(
        known_transitions={
            ("", "drive_to_store_a"): (
                WorldTransition(
                    description="Maybe arrives",
                    probability=0.5,
                    confidence=0.5,
                    resulting_world_delta={},
                ),
            ),
            ("", "ask_directions"): (
                WorldTransition(
                    description="Might get lost",
                    probability=1.0,
                    confidence=0.1,
                    resulting_world_delta={},
                ),
            ),
        }
    )
    plan = _Plan(
        steps=(
            _Step("drive_to_store_a"),
            _Step("ask_directions"),
            _Step("unregistered_step"),
        )
    )
    return SimulationEngine(TransitionPredictionEngine(model)).simulate_plan(None, None, plan)


# ═══════════════════════════════════════════════════════════════════════════
# Confident scenario — the Store A "get 2L of milk" running example
# ═══════════════════════════════════════════════════════════════════════════


class TestConfidentScenario:
    def test_probability_of_success_is_the_path_probability_product(self):
        assessment = RiskEngine().assess(_confident_trajectory())
        assert assessment.probability_of_success == pytest.approx(0.96 * 0.96)

    def test_execution_risk_is_the_complement_of_success_probability(self):
        assessment = RiskEngine().assess(_confident_trajectory())
        assert assessment.execution_risk == pytest.approx(1.0 - assessment.probability_of_success)

    def test_no_risk_factors_when_every_step_is_confident_and_likely(self):
        assessment = RiskEngine().assess(_confident_trajectory())
        assert assessment.risk_factors == ()

    def test_confidence_interval_is_tight_with_no_risk_factors(self):
        assessment = RiskEngine().assess(_confident_trajectory())
        assert assessment.confidence.lower_bound == assessment.confidence.upper_bound

    def test_expected_reward_mostly_positive(self):
        assessment = RiskEngine().assess(_confident_trajectory())
        assert assessment.expected_reward > 0.5


# ═══════════════════════════════════════════════════════════════════════════
# Risky scenario — low probability + uncertain + missing knowledge together
# ═══════════════════════════════════════════════════════════════════════════


class TestRiskyScenario:
    def test_identifies_a_risk_factor_per_problematic_step(self):
        assessment = RiskEngine().assess(_risky_trajectory())
        categories = {f.category for f in assessment.risk_factors}
        assert categories == {
            "low_probability",
            "uncertain_transition",
            "missing_knowledge",
        }

    def test_missing_knowledge_factor_has_maximum_severity(self):
        assessment = RiskEngine().assess(_risky_trajectory())
        missing = next(f for f in assessment.risk_factors if f.category == "missing_knowledge")
        assert missing.severity == 1.0

    def test_uncertain_transition_severity_derived_from_confidence(self):
        assessment = RiskEngine().assess(_risky_trajectory())
        uncertain = next(f for f in assessment.risk_factors if f.category == "uncertain_transition")
        assert uncertain.severity == pytest.approx(1.0 - 0.1)  # confidence was 0.1

    def test_confidence_interval_wider_than_confident_scenario(self):
        confident = RiskEngine().assess(_confident_trajectory())
        risky = RiskEngine().assess(_risky_trajectory())

        confident_width = confident.confidence.upper_bound - confident.confidence.lower_bound
        risky_width = risky.confidence.upper_bound - risky.confidence.lower_bound
        assert risky_width > confident_width

    def test_uncertainty_sources_named_in_confidence(self):
        assessment = RiskEngine().assess(_risky_trajectory())
        assert len(assessment.confidence.uncertainty_sources) == 3

    def test_expected_reward_lower_than_confident_scenario(self):
        confident = RiskEngine().assess(_confident_trajectory())
        risky = RiskEngine().assess(_risky_trajectory())
        assert risky.expected_reward < confident.expected_reward

    def test_low_probability_threshold_is_configurable(self):
        lenient = RiskEngine(low_probability_threshold=0.3)
        assessment = lenient.assess(_risky_trajectory())
        categories = {f.category for f in assessment.risk_factors}
        assert "low_probability" not in categories  # 0.5 >= 0.3 now


# ═══════════════════════════════════════════════════════════════════════════
# Empty plan
# ═══════════════════════════════════════════════════════════════════════════


class TestEmptyPlan:
    def test_vacuously_certain(self):
        empty_trajectory = SimulationEngine().simulate(None, None, ())
        assessment = RiskEngine().assess(empty_trajectory)

        assert assessment.probability_of_success == 1.0
        assert assessment.execution_risk == 0.0
        assert assessment.risk_factors == ()


# ═══════════════════════════════════════════════════════════════════════════
# Configurable utility scale
# ═══════════════════════════════════════════════════════════════════════════


class TestUtilityScale:
    def test_custom_utility_values_change_expected_reward(self):
        engine = RiskEngine(utility_if_success=10.0, utility_if_failure=-5.0)
        assessment = engine.assess(_confident_trajectory())

        expected = assessment.probability_of_success * 10.0 + (1 - assessment.probability_of_success) * (-5.0)
        assert assessment.expected_reward == pytest.approx(expected)


# ═══════════════════════════════════════════════════════════════════════════
# assess_branch — composes with Step 11.4's CounterfactualBranch
# ═══════════════════════════════════════════════════════════════════════════


class TestAssessBranch:
    def test_assesses_a_counterfactual_branchs_own_trajectory(self):
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
        assumption = CounterfactualAssumption(
            description="Store closed",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        probability=0.95,
                        confidence=0.9,
                        resulting_world_delta={"actor.has_milk": False},
                    ),
                )
            },
        )
        branch = cf_engine.branch(None, None, plan, assumption)

        assessment = RiskEngine().assess_branch(branch)

        assert isinstance(assessment, RiskAssessment)
        assert assessment.probability_of_success == pytest.approx(
            RiskEngine().assess(branch.trajectory).probability_of_success
        )


# ═══════════════════════════════════════════════════════════════════════════
# enrich_prediction — "expose uncertainty throughout the prediction pipeline"
# ═══════════════════════════════════════════════════════════════════════════


class TestEnrichPrediction:
    def test_threads_confidence_and_expected_reward_into_prediction(self):
        assessment = RiskEngine().assess(_confident_trajectory())
        prediction = Prediction(time_horizon=600.0)

        enriched = enrich_prediction(prediction, assessment)

        assert enriched.confidence == assessment.confidence
        assert enriched.expected_utility == assessment.expected_reward

    def test_preserves_other_prediction_fields(self):
        assessment = RiskEngine().assess(_confident_trajectory())
        prediction = Prediction(time_horizon=600.0, assumptions=("Store A is open",))

        enriched = enrich_prediction(prediction, assessment)

        assert enriched.time_horizon == 600.0
        assert enriched.assumptions == ("Store A is open",)

    def test_original_prediction_is_not_mutated(self):
        assessment = RiskEngine().assess(_confident_trajectory())
        prediction = Prediction()

        enrich_prediction(prediction, assessment)

        assert prediction.expected_utility == 0.0
        assert prediction.confidence == PredictionConfidence()


# ═══════════════════════════════════════════════════════════════════════════
# Immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_risk_factor_frozen(self):
        from dataclasses import FrozenInstanceError

        factor = RiskFactor()
        with pytest.raises(FrozenInstanceError):
            factor.severity = 1.0

    def test_risk_assessment_frozen(self):
        from dataclasses import FrozenInstanceError

        assessment = RiskAssessment()
        with pytest.raises(FrozenInstanceError):
            assessment.execution_risk = 1.0


# ═══════════════════════════════════════════════════════════════════════════
# PRODUCTION HARDENING — Actor lockout / prediction gate recovery (Phase 1D)
#
# Regression target: _path_probability's dependency-chain gate used to
# return a hard 0.0 for the WHOLE downstream chain the instant ANY
# dependency's own probability dropped below 0.5 -- even a single real
# observed failure (learned via TransitionModel.learn_from_execution's
# EMA, never an absolute 0.0 itself) cascaded into an absolute-zero
# prediction for every later plan sharing that dependency chain. A 0%
# prediction is rejected at Decide time and therefore never re-executed
# to gather better evidence -- a real actor got permanently stuck this
# way. Fixed: a failed dependency's downstream step is excluded from the
# product (same "no signal" treatment as UNKNOWN), not a hard zero.
# ═══════════════════════════════════════════════════════════════════════════

from src.monkey_brain.kernel.pipeline.belief_state import Plan, PlanStep


def _sequential_plan(*actions: str) -> Plan:
    """depends_on=[i-1] for every step -- the real shape LLMPlanner
    produces for a standard multi-step purchase (ProductSelection ->
    OrderCreation -> ... -> Delivery), the exact shape that triggered
    the catastrophic cascade in production."""
    steps = []
    for i, action in enumerate(actions):
        steps.append(PlanStep(action=action, description=action, depends_on=(i - 1,) if i > 0 else ()))
    return Plan(goal="", cost=0.0, confidence=0.8, risk=0.0, planner="llm", steps=tuple(steps))


class TestActorLockoutRecovery:
    def test_one_real_observed_failure_does_not_permanently_zero_a_multistep_plan(self):
        """The exact live repro in miniature: a 6-step purchase plan
        where ONE middle step ("OrderConfirmation") has genuinely
        learned negative evidence (one real observed failure, EMA-
        blended, never an absolute 0). Every later prediction for this
        SAME goal_key must NOT be an absolute 0% -- the actor must still
        be able to attempt (and potentially succeed at) the plan."""
        model = TransitionModel()
        # One real observed failure, exactly as learn_from_execution
        # would blend it (confidence=0.85, learning_rate=0.15 -- the
        # real values comparison/integration.py::_apply_transition_
        # learning uses).
        model = model.learn_from_execution(
            "OrderConfirmation",
            success=False,
            confidence=0.85,
            learning_rate=0.15,
            goal_key="buy groceries",
        )
        learned = model.known_transitions[("buy groceries", "OrderConfirmation")][0]
        assert 0.0 < learned.probability < 0.5, "one real failure must degrade, not zero out, the learned probability"

        plan = _sequential_plan(
            "ProductSelection",
            "OrderCreation",
            "PaymentConfirmation",
            "Payment",
            "OrderConfirmation",
            "Delivery",
        )
        trajectory = SimulationEngine(TransitionPredictionEngine(model)).simulate_plan(None, None, plan)
        assessment = RiskEngine().assess(trajectory)

        assert assessment.probability_of_success > 0.0, (
            "a single real observed failure on one step permanently zeroed the "
            "ENTIRE plan's probability -- the actor can never be predicted to "
            "succeed at this goal again, a real permanent lockout."
        )

    def test_probability_recovers_after_a_later_success_on_the_same_action(self):
        """Not just non-zero -- genuinely bounded and RECOVERABLE. The
        same action's next real observation (a success) must be able to
        raise its learned probability back up via the existing EMA, not
        be stuck at whatever the first failure set it to."""
        model = TransitionModel()
        model = model.learn_from_execution(
            "OrderConfirmation",
            success=False,
            confidence=0.85,
            learning_rate=0.15,
            goal_key="buy groceries",
        )
        after_failure = model.known_transitions[("buy groceries", "OrderConfirmation")][0].probability

        model = model.learn_from_execution(
            "OrderConfirmation",
            success=True,
            confidence=0.85,
            learning_rate=0.15,
            goal_key="buy groceries",
        )
        after_recovery = model.known_transitions[("buy groceries", "OrderConfirmation")][0].probability

        assert after_recovery > after_failure, "a later real success must raise the learned probability back up"

    def test_repeated_failures_degrade_boundedly_not_catastrophically(self):
        """Several real observed failures in a row must degrade the
        learned probability gradually (bounded by the EMA's own
        learning_rate), never jump straight to an unrecoverable
        absolute 0 in one step."""
        model = TransitionModel()
        probabilities = []
        for _ in range(5):
            model = model.learn_from_execution(
                "Payment",
                success=False,
                confidence=0.85,
                learning_rate=0.15,
                goal_key="buy groceries",
            )
            probabilities.append(model.known_transitions[("buy groceries", "Payment")][0].probability)

        # Monotonically non-increasing (each failure can only push it
        # down or hold, via the EMA), but every single value stays
        # strictly above 0 -- the EMA's own floor (observed_prob is
        # clamped to max(0.05, 1 - confidence) for a failure, never 0).
        assert all(p > 0.0 for p in probabilities), "repeated failures must never reach an absolute, unrecoverable 0"
        assert probabilities == sorted(probabilities, reverse=True), "each failure should degrade, not oscillate"


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
        import src.monkey_brain.kernel.pipeline.prediction.risk as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "kernel.pipeline.execution",
            "action_executor",
            "learning.",
        ):
            assert forbidden not in imports, f"risk.py must not import: {forbidden}"

    def test_depends_only_on_prediction_subpackage(self):
        import src.monkey_brain.kernel.pipeline.prediction.risk as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        for module_name in project_imports:
            assert module_name.startswith("src.monkey_brain.kernel.pipeline.prediction"), module_name
