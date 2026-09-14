"""Tests for World Model Evolution (Step 10.5).

derive_world_updates() is a pure function over (observations, reward,
current_strengths) — no CognitiveState, no real world model, no mocking.
WorldEvolutionEngine.learn() wraps it against a full LearningExperience,
folding "world" LearningSignals back in without mutating the original.

Distinct from Step 10.4's belief_learning.py: this updates a *shared*
relationship strength (defaulting to a neutral prior, moving conservatively)
rather than an actor's own observation confidence (starting from that
actor's existing confidence, moving faster) — see the module docstring in
world_evolution.py for the architectural justification.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningExperience,
    LearningObservation,
    LearningOutcome,
    LearningSignal,
)
from src.monkey_brain.kernel.pipeline.learning.reward import ExperienceRewardEngine
from src.monkey_brain.kernel.pipeline.learning.belief_learning import BeliefLearner
from src.monkey_brain.kernel.pipeline.learning.world_evolution import (
    NEUTRAL_STRENGTH,
    WorldEvolutionEngine,
    WorldRelationshipUpdate,
    apply_world_evolution,
    derive_world_updates,
)


def _store_a_observation() -> LearningObservation:
    return LearningObservation(
        entity="Store A",
        attribute="stocks_whole_milk",
        value=True,
        confidence=0.9,
        source="execution_outcome",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Acceptance-criteria scenario
# ═══════════════════════════════════════════════════════════════════════════


class TestAcceptanceScenario:
    def test_successful_purchase_reinforces_store_a_inventory_relationship(self):
        """'World Update: Store A inventory relationship reinforced.' from
        Step 10's acceptance criteria, chained off the real +0.92 reward."""
        obs = _store_a_observation()
        outcome = LearningOutcome(goal_achieved=True, cost=0.0, duration_seconds=360.0)
        experience = LearningExperience(outcome=outcome, observations=(obs,))
        evaluated = ExperienceRewardEngine(duration_budget_seconds=900.0).evaluate(experience)
        assert evaluated.reward == pytest.approx(0.92)

        updates = WorldEvolutionEngine().derive_updates(evaluated)

        assert len(updates) == 1
        assert updates[0].entity == "Store A"
        assert updates[0].new_strength > updates[0].previous_strength
        assert "reinforced" in updates[0].rationale

    def test_learn_produces_a_world_signal(self):
        obs = _store_a_observation()
        experience = LearningExperience(observations=(obs,), reward=0.92)

        learned = WorldEvolutionEngine().learn(experience)

        assert len(learned.signals) == 1
        assert learned.signals[0].kind == "world"
        assert learned.signals[0].subject == "Store A.stocks_whole_milk"


# ═══════════════════════════════════════════════════════════════════════════
# Shared vs. per-actor — the reason 10.5 is a distinct step from 10.4
# ═══════════════════════════════════════════════════════════════════════════


class TestSharedVersusPerActor:
    def test_starts_from_a_neutral_prior_not_the_observation_confidence(self):
        """Unlike BeliefLearner (which starts from obs.confidence=0.9), the
        world update starts from NEUTRAL_STRENGTH when no shared prior is
        supplied — a single actor's observation confidence doesn't
        automatically become shared-world truth."""
        obs = _store_a_observation()
        updates = derive_world_updates((obs,), reward=0.92)
        assert updates[0].previous_strength == NEUTRAL_STRENGTH

    def test_moves_more_conservatively_than_belief_learning(self):
        obs = _store_a_observation()
        experience = LearningExperience(observations=(obs,), reward=0.92)

        world_updates = WorldEvolutionEngine().derive_updates(experience)
        belief_updates = BeliefLearner().derive_updates(experience)

        assert abs(world_updates[0].delta) < abs(belief_updates[0].delta)

    def test_current_strengths_supplies_the_shared_prior(self):
        obs = _store_a_observation()
        engine = WorldEvolutionEngine(current_strengths={("Store A", "stocks_whole_milk"): 0.8})

        updates = engine.derive_updates(LearningExperience(observations=(obs,), reward=0.92))

        assert updates[0].previous_strength == 0.8
        assert updates[0].new_strength > 0.8


# ═══════════════════════════════════════════════════════════════════════════
# derive_world_updates — directionality
# ═══════════════════════════════════════════════════════════════════════════


class TestDeriveWorldUpdatesDirectionality:
    def test_high_reward_reinforces_toward_one(self):
        obs = _store_a_observation()
        updates = derive_world_updates((obs,), reward=0.9)
        assert updates[0].new_strength > NEUTRAL_STRENGTH

    def test_low_reward_weakens_toward_zero(self):
        obs = _store_a_observation()
        updates = derive_world_updates((obs,), reward=0.1)
        assert updates[0].new_strength < NEUTRAL_STRENGTH
        assert "weakened" in updates[0].rationale

    def test_neutral_reward_produces_no_updates(self):
        obs = _store_a_observation()
        updates = derive_world_updates((obs,), reward=0.5)
        assert updates == ()

    def test_no_observations_produces_no_updates(self):
        assert derive_world_updates((), reward=0.9) == ()

    def test_strength_already_at_ceiling_has_no_headroom(self):
        obs = LearningObservation(entity="X", attribute="y")
        updates = derive_world_updates((obs,), reward=0.9, current_strengths={("X", "y"): 1.0})
        assert updates == ()

    def test_new_strength_stays_within_bounds(self):
        obs = LearningObservation(entity="X", attribute="y")
        updates = derive_world_updates((obs,), reward=1.0, current_strengths={("X", "y"): 0.98}, learning_rate=5.0)
        assert 0.0 <= updates[0].new_strength <= 1.0

    def test_multiple_observations_each_get_an_update(self):
        obs1 = LearningObservation(entity="Store A", attribute="stocks_whole_milk")
        obs2 = LearningObservation(entity="Store A", attribute="open_late")
        updates = derive_world_updates((obs1, obs2), reward=0.9)
        assert len(updates) == 2

    def test_unrelated_relationship_keys_do_not_affect_each_other(self):
        obs = LearningObservation(entity="Store A", attribute="stocks_whole_milk")
        updates = derive_world_updates(
            (obs,),
            reward=0.9,
            current_strengths={("Store B", "stocks_whole_milk"): 0.99},
        )
        assert updates[0].previous_strength == NEUTRAL_STRENGTH


# ═══════════════════════════════════════════════════════════════════════════
# WorldEvolutionEngine — operates on LearningExperience, preserves immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestWorldEvolutionEngine:
    def test_original_experience_is_untouched(self):
        obs = _store_a_observation()
        experience = LearningExperience(observations=(obs,), reward=0.9)

        WorldEvolutionEngine().learn(experience)

        assert experience.signals == ()

    def test_signals_are_appended_not_replaced(self):
        existing = LearningSignal(kind="belief", subject="x")
        obs = _store_a_observation()
        experience = LearningExperience(observations=(obs,), reward=0.9, signals=(existing,))

        learned = WorldEvolutionEngine().learn(experience)

        assert existing in learned.signals
        assert len(learned.signals) == 2

    def test_world_updates_recorded_in_metadata(self):
        obs = _store_a_observation()
        experience = LearningExperience(observations=(obs,), reward=0.9)

        learned = WorldEvolutionEngine().learn(experience)

        assert "world_updates" in learned.metadata
        assert learned.metadata["world_updates"][0]["entity"] == "Store A"

    def test_preserves_caller_supplied_metadata(self):
        experience = LearningExperience(metadata={"actor_id": "alice"}, reward=0.9)
        learned = WorldEvolutionEngine().learn(experience)
        assert learned.metadata["actor_id"] == "alice"

    def test_no_observations_yields_no_signals(self):
        experience = LearningExperience(observations=(), reward=0.9)
        learned = WorldEvolutionEngine().learn(experience)
        assert learned.signals == ()

    def test_experience_identity_preserved(self):
        experience = LearningExperience(goal="acquire_milk", observations=(_store_a_observation(),), reward=0.9)
        learned = WorldEvolutionEngine().learn(experience)
        assert learned.experience_id == experience.experience_id
        assert learned.goal == "acquire_milk"

    def test_module_level_function_matches_class(self):
        obs = _store_a_observation()
        experience = LearningExperience(observations=(obs,), reward=0.9)

        via_fn = apply_world_evolution(experience)
        via_class = WorldEvolutionEngine().learn(experience)

        assert via_fn.signals[0].direction == via_class.signals[0].direction


# ═══════════════════════════════════════════════════════════════════════════
# WorldRelationshipUpdate — immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestWorldRelationshipUpdateImmutability:
    def test_frozen(self):
        from dataclasses import FrozenInstanceError

        update = WorldRelationshipUpdate()
        with pytest.raises(FrozenInstanceError):
            update.new_strength = 1.0


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
    def test_no_world_runtime_or_belief_runtime_coupling(self):
        """World Model Evolution computes what the update *would be*;
        applying it to a real, persistent world model is Step 10.7's job."""
        import src.monkey_brain.kernel.pipeline.learning.world_evolution as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "belief_state",
            "execution_state",
            "graph_manager",
            "world",
        ):
            assert forbidden not in imports, f"world_evolution.py must not import: {forbidden}"

    def test_depends_only_on_learning_domain(self):
        import src.monkey_brain.kernel.pipeline.learning.world_evolution as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        for module_name in project_imports:
            assert module_name.startswith("src.monkey_brain.kernel.pipeline.learning"), module_name
