"""Tests for the Transition Prediction Engine (Step 11.2).

TransitionPredictionEngine is pure and read-only -- no CognitiveState, no
real world/belief objects, no execution. Fake duck-typed action/fact/belief
stand-ins exercise the Any-typed inputs the same way the engine itself
treats them (getattr-based, never assuming a real class).
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.prediction.domain import Prediction
from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionKind,
    TransitionModel,
    TransitionPredictionEngine,
    WorldTransition,
)


class _Action:
    def __init__(self, description: str) -> None:
        self.description = description


class _Fact:
    def __init__(self, entity: str, attribute: str, confidence: float) -> None:
        self.entity = entity
        self.attribute = attribute
        self.confidence = confidence


class _Belief:
    def __init__(self, facts) -> None:
        self.facts = facts


def _milk_model() -> TransitionModel:
    return TransitionModel(
        known_transitions={
            ("", "check_inventory_store_a"): (
                WorldTransition(
                    description="Inventory confirmed",
                    probability=1.0,
                    confidence=0.95,
                    resulting_world_delta={"store_a.has_milk": True},
                ),
            ),
            ("", "drive_to_store_a"): (
                WorldTransition(
                    description="Arrived on time",
                    probability=0.9,
                    confidence=0.8,
                    resulting_world_delta={"actor.location": "store_a"},
                ),
                WorldTransition(
                    description="Traffic delay",
                    probability=0.1,
                    confidence=0.8,
                    resulting_world_delta={"actor.location": "en_route"},
                ),
            ),
            ("", "ask_stranger_for_directions"): (
                WorldTransition(
                    description="Directions correct",
                    probability=1.0,
                    confidence=0.15,
                    resulting_world_delta={"actor.knows_route": True},
                ),
            ),
            ("", "purchase_milk"): (
                WorldTransition(
                    description="Milk purchased",
                    probability=1.0,
                    confidence=0.5,
                    resulting_world_delta={"actor.has_milk": True},
                    grounding_fact_key="store_a.stocks_whole_milk",
                ),
            ),
        }
    )


# ═══════════════════════════════════════════════════════════════════════════
# The four required transition kinds
# ═══════════════════════════════════════════════════════════════════════════


class TestDeterministicTransitions:
    def test_single_certain_outcome_classified_deterministic(self):
        engine = TransitionPredictionEngine(_milk_model())
        transitions = engine.predict_transitions(None, None, _Action("check_inventory_store_a"))

        assert len(transitions) == 1
        assert transitions[0].kind == TransitionKind.DETERMINISTIC
        assert transitions[0].probability == 1.0


class TestProbabilisticTransitions:
    def test_multiple_confident_outcomes_classified_probabilistic(self):
        engine = TransitionPredictionEngine(_milk_model())
        transitions = engine.predict_transitions(None, None, _Action("drive_to_store_a"))

        assert len(transitions) == 2
        assert all(t.kind == TransitionKind.PROBABILISTIC for t in transitions)

    def test_probabilities_form_a_valid_distribution(self):
        engine = TransitionPredictionEngine(_milk_model())
        transitions = engine.predict_transitions(None, None, _Action("drive_to_store_a"))
        assert sum(t.probability for t in transitions) == pytest.approx(1.0)


class TestUncertainTransitions:
    def test_low_confidence_single_outcome_classified_uncertain(self):
        """A single known outcome isn't automatically 'deterministic' --
        low confidence in that estimate makes it UNCERTAIN regardless of
        how many outcomes are registered."""
        engine = TransitionPredictionEngine(_milk_model())
        transitions = engine.predict_transitions(None, None, _Action("ask_stranger_for_directions"))

        assert transitions[0].kind == TransitionKind.UNCERTAIN

    def test_uncertainty_threshold_is_configurable(self):
        lenient_engine = TransitionPredictionEngine(_milk_model(), uncertainty_threshold=0.1)
        transitions = lenient_engine.predict_transitions(None, None, _Action("ask_stranger_for_directions"))
        assert transitions[0].kind == TransitionKind.DETERMINISTIC


class TestMissingKnowledge:
    def test_unregistered_action_returns_explicit_unknown(self):
        engine = TransitionPredictionEngine(_milk_model())
        transitions = engine.predict_transitions(None, None, _Action("fly_to_the_moon"))

        assert len(transitions) == 1
        assert transitions[0].kind == TransitionKind.UNKNOWN
        assert transitions[0].confidence == 0.0

    def test_missing_knowledge_does_not_guess_an_effect(self):
        """Missing knowledge must not silently default to success -- the
        world delta stays empty, not populated with an assumed outcome."""
        engine = TransitionPredictionEngine(_milk_model())
        transitions = engine.predict_transitions(None, None, _Action("fly_to_the_moon"))
        assert transitions[0].resulting_world_delta == {}

    def test_empty_model_treats_every_action_as_unknown(self):
        engine = TransitionPredictionEngine()
        transitions = engine.predict_transitions(None, None, _Action("anything"))
        assert transitions[0].kind == TransitionKind.UNKNOWN


# ═══════════════════════════════════════════════════════════════════════════
# Belief grounding
# ═══════════════════════════════════════════════════════════════════════════


class TestBeliefGrounding:
    def test_matching_fact_overrides_registered_confidence(self):
        engine = TransitionPredictionEngine(_milk_model())
        belief = _Belief(facts=[_Fact("store_a", "stocks_whole_milk", 0.92)])

        transitions = engine.predict_transitions(None, belief, _Action("purchase_milk"))

        assert transitions[0].confidence == 0.92

    def test_no_belief_state_keeps_registered_confidence(self):
        engine = TransitionPredictionEngine(_milk_model())
        transitions = engine.predict_transitions(None, None, _Action("purchase_milk"))
        assert transitions[0].confidence == 0.5

    def test_belief_without_matching_fact_keeps_registered_confidence(self):
        engine = TransitionPredictionEngine(_milk_model())
        belief = _Belief(facts=[_Fact("store_b", "stocks_whole_milk", 0.1)])
        transitions = engine.predict_transitions(None, belief, _Action("purchase_milk"))
        assert transitions[0].confidence == 0.5

    def test_belief_without_facts_attribute_does_not_crash(self):
        """belief_state is Any-typed -- an object with no .facts at all
        must be handled gracefully, not raise AttributeError."""
        engine = TransitionPredictionEngine(_milk_model())
        transitions = engine.predict_transitions(None, object(), _Action("purchase_milk"))
        assert transitions[0].confidence == 0.5


# ═══════════════════════════════════════════════════════════════════════════
# predict_future_world — folding a sequence of actions
# ═══════════════════════════════════════════════════════════════════════════


class TestPredictFutureWorld:
    def test_folds_most_likely_transition_per_action(self):
        engine = TransitionPredictionEngine(_milk_model())
        actions = (_Action("check_inventory_store_a"), _Action("drive_to_store_a"))

        world = engine.predict_future_world(None, None, actions)

        assert world["store_a.has_milk"] is True
        assert world["actor.location"] == "store_a"  # 0.9 probability outcome, not the 0.1 one

    def test_empty_actions_yields_empty_world(self):
        engine = TransitionPredictionEngine(_milk_model())
        assert engine.predict_future_world(None, None, ()) == {}

    def test_does_not_mutate_inputs(self):
        engine = TransitionPredictionEngine(_milk_model())
        world_snapshot = {"original": True}
        belief_state = {"original": True}
        engine.predict_future_world(world_snapshot, belief_state, (_Action("drive_to_store_a"),))
        assert world_snapshot == {"original": True}
        assert belief_state == {"original": True}


# ═══════════════════════════════════════════════════════════════════════════
# predict — the full Prediction domain object
# ═══════════════════════════════════════════════════════════════════════════


class TestPredict:
    def test_returns_a_prediction(self):
        engine = TransitionPredictionEngine(_milk_model())
        pred = engine.predict(None, None, (_Action("check_inventory_store_a"),), time_horizon=600.0)
        assert isinstance(pred, Prediction)
        assert pred.time_horizon == 600.0

    def test_world_snapshot_matches_predict_future_world(self):
        engine = TransitionPredictionEngine(_milk_model())
        actions = (_Action("check_inventory_store_a"), _Action("drive_to_store_a"))

        pred = engine.predict(None, None, actions)
        world = engine.predict_future_world(None, None, actions)

        assert pred.world_snapshot == world

    def test_confidence_is_the_minimum_across_actions(self):
        engine = TransitionPredictionEngine(_milk_model())
        actions = (
            _Action("check_inventory_store_a"),
            _Action("drive_to_store_a"),
        )  # confidences 0.95, 0.8

        pred = engine.predict(None, None, actions)

        assert pred.confidence.point_estimate == pytest.approx(0.8)

    def test_outcome_count_matches_action_count(self):
        engine = TransitionPredictionEngine(_milk_model())
        actions = (
            _Action("check_inventory_store_a"),
            _Action("drive_to_store_a"),
            _Action("fly_to_the_moon"),
        )
        pred = engine.predict(None, None, actions)
        assert len(pred.predicted_outcomes) == 3

    def test_unknown_action_outcome_marked_unsuccessful(self):
        engine = TransitionPredictionEngine(_milk_model())
        pred = engine.predict(None, None, (_Action("fly_to_the_moon"),))
        assert pred.predicted_outcomes[0].success is False

    def test_expected_utility_stays_unset(self):
        """Deferred to Step 11.5/11.6 -- this step only predicts world
        state and confidence, not utility."""
        engine = TransitionPredictionEngine(_milk_model())
        pred = engine.predict(None, None, (_Action("check_inventory_store_a"),))
        assert pred.expected_utility == 0.0

    def test_no_actions_yields_zero_confidence(self):
        engine = TransitionPredictionEngine(_milk_model())
        pred = engine.predict(None, None, ())
        assert pred.confidence.point_estimate == 0.0
        assert pred.predicted_outcomes == ()


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary — read-only, no execution coupling
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
        import src.monkey_brain.kernel.pipeline.prediction.transitions as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "kernel.pipeline.execution",
            "action_executor",
            "learning.",
        ):
            assert forbidden not in imports, f"transitions.py must not import: {forbidden}"

    def test_depends_only_on_prediction_domain(self):
        import src.monkey_brain.kernel.pipeline.prediction.transitions as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        assert project_imports == ["src.monkey_brain.kernel.pipeline.prediction.domain"]
