"""Tests for Belief Learning (Step 10.4).

derive_belief_updates() is a pure function over (observations, reward) — no
CognitiveState, no BeliefState, no mocking. BeliefLearner.learn() wraps it
against a full LearningExperience, folding "belief" LearningSignals back in
without mutating the original (frozen dataclass replace).
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningExperience,
    LearningObservation,
    LearningOutcome,
)
from src.monkey_brain.kernel.pipeline.learning.reward import ExperienceRewardEngine
from src.monkey_brain.kernel.pipeline.learning.belief_learning import (
    BeliefLearner,
    BeliefUpdate,
    apply_belief_learning,
    derive_belief_updates,
)


def _store_a_observation(confidence: float = 0.9) -> LearningObservation:
    return LearningObservation(
        entity="Store A",
        attribute="stocks_whole_milk",
        value=True,
        confidence=confidence,
        source="execution_outcome",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Acceptance-criteria scenario
# ═══════════════════════════════════════════════════════════════════════════


class TestAcceptanceScenario:
    def test_successful_purchase_increases_confidence_store_a_stocks_milk(self):
        """'Belief Update: Increase confidence that Store A stocks whole
        milk.' from Step 10's acceptance criteria, chained off the real
        reward-evaluation result (+0.92) from Step 10.3."""
        obs = _store_a_observation(confidence=0.9)
        outcome = LearningOutcome(goal_achieved=True, cost=0.0, duration_seconds=360.0)
        experience = LearningExperience(outcome=outcome, observations=(obs,))
        evaluated = ExperienceRewardEngine(duration_budget_seconds=900.0).evaluate(experience)
        assert evaluated.reward == pytest.approx(0.92)

        updates = BeliefLearner().derive_updates(evaluated)

        assert len(updates) == 1
        assert updates[0].entity == "Store A"
        assert updates[0].attribute == "stocks_whole_milk"
        assert updates[0].new_confidence > updates[0].previous_confidence

    def test_learn_produces_a_belief_signal(self):
        obs = _store_a_observation(confidence=0.9)
        experience = LearningExperience(observations=(obs,), reward=0.92)

        learned = BeliefLearner().learn(experience)

        assert len(learned.signals) == 1
        assert learned.signals[0].kind == "belief"
        assert learned.signals[0].subject == "Store A.stocks_whole_milk"
        assert learned.signals[0].direction > 0


# ═══════════════════════════════════════════════════════════════════════════
# derive_belief_updates — directionality
# ═══════════════════════════════════════════════════════════════════════════


class TestDeriveBeliefUpdatesDirectionality:
    def test_high_reward_reinforces_toward_one(self):
        obs = _store_a_observation(confidence=0.5)
        updates = derive_belief_updates((obs,), reward=0.9)
        assert updates[0].new_confidence > 0.5
        assert updates[0].delta > 0

    def test_low_reward_weakens_toward_zero(self):
        obs = _store_a_observation(confidence=0.5)
        updates = derive_belief_updates((obs,), reward=0.1)
        assert updates[0].new_confidence < 0.5
        assert updates[0].delta < 0

    def test_neutral_reward_produces_no_updates(self):
        """reward == 0.5 is the exact midpoint — no evidence, no change."""
        obs = _store_a_observation(confidence=0.5)
        updates = derive_belief_updates((obs,), reward=0.5)
        assert updates == ()

    def test_no_observations_produces_no_updates(self):
        updates = derive_belief_updates((), reward=0.9)
        assert updates == ()

    def test_confidence_already_at_ceiling_has_no_headroom(self):
        obs = LearningObservation(entity="X", attribute="y", confidence=1.0)
        updates = derive_belief_updates((obs,), reward=0.9)
        assert updates == ()

    def test_confidence_already_at_floor_has_no_headroom_on_failure(self):
        obs = LearningObservation(entity="X", attribute="y", confidence=0.0)
        updates = derive_belief_updates((obs,), reward=0.1)
        assert updates == ()

    def test_new_confidence_stays_within_bounds(self):
        obs = LearningObservation(entity="X", attribute="y", confidence=0.95)
        updates = derive_belief_updates((obs,), reward=1.0, learning_rate=5.0)
        assert 0.0 <= updates[0].new_confidence <= 1.0

    def test_multiple_observations_each_get_an_update(self):
        obs1 = LearningObservation(entity="Store A", attribute="stocks_whole_milk", confidence=0.6)
        obs2 = LearningObservation(entity="Store A", attribute="open_late", confidence=0.4)
        updates = derive_belief_updates((obs1, obs2), reward=0.9)
        assert len(updates) == 2
        entities_attrs = {(u.entity, u.attribute) for u in updates}
        assert entities_attrs == {
            ("Store A", "stocks_whole_milk"),
            ("Store A", "open_late"),
        }

    def test_higher_learning_rate_produces_bigger_delta(self):
        obs = _store_a_observation(confidence=0.5)
        slow = derive_belief_updates((obs,), reward=0.9, learning_rate=0.1)
        fast = derive_belief_updates((obs,), reward=0.9, learning_rate=0.9)
        assert fast[0].delta > slow[0].delta


# ═══════════════════════════════════════════════════════════════════════════
# BeliefLearner — operates on LearningExperience, preserves immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestBeliefLearner:
    def test_original_experience_is_untouched(self):
        obs = _store_a_observation()
        experience = LearningExperience(observations=(obs,), reward=0.9)

        BeliefLearner().learn(experience)

        assert experience.signals == ()

    def test_signals_are_appended_not_replaced(self):
        from src.monkey_brain.kernel.pipeline.learning.domain import LearningSignal

        existing = LearningSignal(kind="reward", subject="goal")
        obs = _store_a_observation()
        experience = LearningExperience(observations=(obs,), reward=0.9, signals=(existing,))

        learned = BeliefLearner().learn(experience)

        assert existing in learned.signals
        assert len(learned.signals) == 2

    def test_belief_updates_recorded_in_metadata(self):
        obs = _store_a_observation()
        experience = LearningExperience(observations=(obs,), reward=0.9)

        learned = BeliefLearner().learn(experience)

        assert "belief_updates" in learned.metadata
        assert learned.metadata["belief_updates"][0]["entity"] == "Store A"

    def test_preserves_caller_supplied_metadata(self):
        experience = LearningExperience(metadata={"actor_id": "alice"}, reward=0.9)
        learned = BeliefLearner().learn(experience)
        assert learned.metadata["actor_id"] == "alice"

    def test_no_observations_yields_no_signals(self):
        experience = LearningExperience(observations=(), reward=0.9)
        learned = BeliefLearner().learn(experience)
        assert learned.signals == ()

    def test_experience_identity_preserved(self):
        experience = LearningExperience(goal="acquire_milk", observations=(_store_a_observation(),), reward=0.9)
        learned = BeliefLearner().learn(experience)
        assert learned.experience_id == experience.experience_id
        assert learned.goal == "acquire_milk"

    def test_module_level_function_matches_class(self):
        obs = _store_a_observation()
        experience = LearningExperience(observations=(obs,), reward=0.9)

        via_fn = apply_belief_learning(experience)
        via_class = BeliefLearner().learn(experience)

        assert via_fn.signals[0].direction == via_class.signals[0].direction


# ═══════════════════════════════════════════════════════════════════════════
# BeliefUpdate — immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestBeliefUpdateImmutability:
    def test_frozen(self):
        from dataclasses import FrozenInstanceError

        update = BeliefUpdate()
        with pytest.raises(FrozenInstanceError):
            update.new_confidence = 1.0


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
    def test_no_belief_state_or_runtime_coupling(self):
        """Belief Learning computes what the update *would be*; applying it
        to the real BeliefState is Step 10.7's job, not this one's."""
        import src.monkey_brain.kernel.pipeline.learning.belief_learning as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "belief_state",
            "execution_state",
            "action_executor",
        ):
            assert forbidden not in imports, f"belief_learning.py must not import: {forbidden}"

    def test_depends_only_on_learning_domain(self):
        import src.monkey_brain.kernel.pipeline.learning.belief_learning as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        for module_name in project_imports:
            assert module_name.startswith("src.monkey_brain.kernel.pipeline.learning"), module_name
