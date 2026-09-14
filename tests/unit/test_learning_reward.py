"""Tests for the Reward & Evaluation Engine (Step 10.3).

compute_reward() is a pure function over LearningOutcome — no CognitiveState,
no mocking, no async. ExperienceRewardEngine.evaluate() wraps it against a full
LearningExperience, returning a new (not mutated) experience since the model
is frozen.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningExperience,
    LearningOutcome,
)
from src.monkey_brain.kernel.pipeline.learning.reward import (
    RewardBreakdown,
    ExperienceRewardEngine,
    RewardWeights,
    compute_reward,
    evaluate_experience,
)

# ═══════════════════════════════════════════════════════════════════════════
# compute_reward — the acceptance-criteria scenario
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeRewardAcceptanceScenario:
    def test_milk_in_six_minutes_within_budget_scores_point_nine_two(self):
        """'Successfully purchased milk from Store A in 6 minutes' against a
        15-minute duration budget, zero cost -> the exact +0.92 from Step
        10's acceptance criteria."""
        outcome = LearningOutcome(goal_achieved=True, cost=0.0, duration_seconds=360.0)

        breakdown = compute_reward(outcome, duration_budget_seconds=900.0)

        assert breakdown.total == pytest.approx(0.92)

    def test_rationale_explains_quickly_and_within_budget(self):
        outcome = LearningOutcome(goal_achieved=True, cost=0.0, duration_seconds=360.0)
        breakdown = compute_reward(outcome, duration_budget_seconds=900.0)

        assert "quickly" in breakdown.rationale
        assert "within budget" in breakdown.rationale


# ═══════════════════════════════════════════════════════════════════════════
# compute_reward — component behavior
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeRewardComponents:
    def test_goal_not_achieved_scores_zero_by_default(self):
        outcome = LearningOutcome(goal_achieved=False, partial=False)
        assert compute_reward(outcome).total == 0.0

    def test_partial_achievement_earns_partial_credit(self):
        outcome = LearningOutcome(goal_achieved=False, partial=True)
        breakdown = compute_reward(outcome)
        assert breakdown.total == pytest.approx(0.3)
        assert breakdown.base == pytest.approx(0.3)

    def test_speed_and_budget_bonuses_require_goal_achieved(self):
        """A fast, cheap failure is still worth nothing — bonuses only
        apply on top of success, not instead of it."""
        outcome = LearningOutcome(goal_achieved=False, cost=0.0, duration_seconds=1.0)
        breakdown = compute_reward(outcome, duration_budget_seconds=900.0, cost_budget=10.0)

        assert breakdown.speed_bonus == 0.0
        assert breakdown.budget_bonus == 0.0
        assert breakdown.total == 0.0

    def test_errors_penalize_a_successful_outcome(self):
        outcome = LearningOutcome(goal_achieved=True, errors=("timeout", "retry_exhausted"))
        breakdown = compute_reward(outcome)

        weights = RewardWeights()
        expected = weights.goal_achieved + weights.budget_bonus - (2 * weights.error_penalty)
        assert breakdown.total == pytest.approx(expected)
        assert breakdown.error_penalty == pytest.approx(2 * weights.error_penalty)

    def test_reward_never_goes_negative(self):
        outcome = LearningOutcome(goal_achieved=True, errors=("e1", "e2", "e3", "e4", "e5", "e6"))
        breakdown = compute_reward(outcome)
        assert breakdown.total == 0.0

    def test_reward_never_exceeds_one(self):
        outcome = LearningOutcome(goal_achieved=True, cost=0.0, duration_seconds=0.0)
        breakdown = compute_reward(outcome, duration_budget_seconds=900.0, cost_budget=100.0)
        assert breakdown.total <= 1.0

    def test_zero_cost_earns_budget_bonus_even_without_a_stated_budget(self):
        outcome = LearningOutcome(goal_achieved=True, cost=0.0)
        breakdown = compute_reward(outcome, cost_budget=None)
        assert breakdown.budget_bonus == pytest.approx(RewardWeights().budget_bonus)

    def test_nonzero_cost_without_a_budget_earns_no_budget_bonus(self):
        outcome = LearningOutcome(goal_achieved=True, cost=5.0)
        breakdown = compute_reward(outcome, cost_budget=None)
        assert breakdown.budget_bonus == 0.0

    def test_exceeding_duration_budget_earns_no_speed_bonus(self):
        outcome = LearningOutcome(goal_achieved=True, duration_seconds=1800.0)
        breakdown = compute_reward(outcome, duration_budget_seconds=900.0)
        assert breakdown.speed_bonus == 0.0

    def test_custom_weights_are_honored(self):
        outcome = LearningOutcome(goal_achieved=True, cost=0.0, duration_seconds=360.0)
        weights = RewardWeights(goal_achieved=1.0, speed_bonus=0.0, budget_bonus=0.0, error_penalty=0.0)

        breakdown = compute_reward(outcome, weights, duration_budget_seconds=900.0)

        assert breakdown.total == 1.0


# ═══════════════════════════════════════════════════════════════════════════
# ExperienceRewardEngine — operates on LearningExperience, preserves immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestExperienceRewardEngine:
    def test_evaluate_returns_a_new_experience_with_reward_set(self):
        outcome = LearningOutcome(goal_achieved=True, cost=0.0, duration_seconds=360.0)
        experience = LearningExperience(outcome=outcome, reward=0.0)

        evaluated = ExperienceRewardEngine(duration_budget_seconds=900.0).evaluate(experience)

        assert evaluated.reward == pytest.approx(0.92)

    def test_original_experience_is_untouched(self):
        outcome = LearningOutcome(goal_achieved=True, cost=0.0, duration_seconds=360.0)
        experience = LearningExperience(outcome=outcome, reward=0.0)

        ExperienceRewardEngine(duration_budget_seconds=900.0).evaluate(experience)

        assert experience.reward == 0.0

    def test_evaluated_experience_preserves_identity_and_other_fields(self):
        outcome = LearningOutcome(goal_achieved=True)
        experience = LearningExperience(goal="acquire_milk", outcome=outcome)

        evaluated = ExperienceRewardEngine().evaluate(experience)

        assert evaluated.experience_id == experience.experience_id
        assert evaluated.goal == "acquire_milk"

    def test_breakdown_attached_to_metadata(self):
        outcome = LearningOutcome(goal_achieved=True, cost=0.0, duration_seconds=360.0)
        experience = LearningExperience(outcome=outcome)

        evaluated = ExperienceRewardEngine(duration_budget_seconds=900.0).evaluate(experience)

        assert "reward_breakdown" in evaluated.metadata
        assert evaluated.metadata["reward_breakdown"]["total"] == pytest.approx(0.92)

    def test_preserves_caller_supplied_metadata(self):
        experience = LearningExperience(metadata={"actor_id": "alice"})
        evaluated = ExperienceRewardEngine().evaluate(experience)
        assert evaluated.metadata["actor_id"] == "alice"

    def test_module_level_function_matches_engine(self):
        outcome = LearningOutcome(goal_achieved=True, cost=0.0, duration_seconds=360.0)
        experience = LearningExperience(outcome=outcome)

        via_fn = evaluate_experience(experience, duration_budget_seconds=900.0)
        via_engine = ExperienceRewardEngine(duration_budget_seconds=900.0).evaluate(experience)

        assert via_fn.reward == pytest.approx(via_engine.reward)


# ═══════════════════════════════════════════════════════════════════════════
# RewardBreakdown — immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestRewardBreakdownImmutability:
    def test_frozen(self):
        from dataclasses import FrozenInstanceError

        breakdown = RewardBreakdown()
        with pytest.raises(FrozenInstanceError):
            breakdown.total = 1.0

    def test_weights_frozen(self):
        from dataclasses import FrozenInstanceError

        weights = RewardWeights()
        with pytest.raises(FrozenInstanceError):
            weights.goal_achieved = 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary — reward evaluation depends only on the domain model
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
    def test_no_execution_engine_or_cognitive_runtime_coupling(self):
        import src.monkey_brain.kernel.pipeline.learning.reward as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "execution_state",
            "kernel.pipeline.execution",
            "action_executor",
        ):
            assert forbidden not in imports, f"reward.py must not import: {forbidden}"

    def test_depends_only_on_learning_domain(self):
        """Reward evaluation is a pure function of LearningOutcome — it has
        no reason to import CognitiveState at all, unlike capture.py."""
        import src.monkey_brain.kernel.pipeline.learning.reward as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        for module_name in project_imports:
            assert module_name.startswith("src.monkey_brain.kernel.pipeline.learning"), module_name
