"""Tests for Learning Policies (Step 10.6).

Resolves the tension Step 10.1's domain-model docstring deliberately left
open: domain.LearningPolicy stays pure data; LearningPolicyEngine (this
module) is the actual behavioral contract, and resolve_policy() bridges
between them. Three concrete strategies (reinforcement/passive/conservative)
prove policies are genuinely pluggable, not one hardcoded pipeline.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningExperience,
    LearningObservation,
    LearningOutcome,
    LearningPolicy,
    LearningResult,
)
from src.monkey_brain.kernel.pipeline.learning.reward import ExperienceRewardEngine
from src.monkey_brain.kernel.pipeline.learning.policies import (
    ConservativeLearningPolicy,
    LearningPolicyEngine,
    PassiveLearningPolicy,
    ReinforcementLearningPolicy,
    apply_learning_policy,
    resolve_policy,
)


def _milk_experience(duration_seconds: float = 360.0, confidence: float = 0.9) -> LearningExperience:
    obs = LearningObservation(
        entity="Store A",
        attribute="stocks_whole_milk",
        value=True,
        confidence=confidence,
    )
    outcome = LearningOutcome(goal_achieved=True, cost=0.0, duration_seconds=duration_seconds)
    return LearningExperience(outcome=outcome, observations=(obs,))


# ═══════════════════════════════════════════════════════════════════════════
# Protocol conformance
# ═══════════════════════════════════════════════════════════════════════════


class TestProtocolConformance:
    def test_all_three_policies_satisfy_learning_policy_engine(self):
        assert isinstance(ReinforcementLearningPolicy(), LearningPolicyEngine)
        assert isinstance(PassiveLearningPolicy(), LearningPolicyEngine)
        assert isinstance(ConservativeLearningPolicy(), LearningPolicyEngine)

    def test_learn_returns_a_learning_result(self):
        result = ReinforcementLearningPolicy().learn(_milk_experience())
        assert isinstance(result, LearningResult)


# ═══════════════════════════════════════════════════════════════════════════
# ReinforcementLearningPolicy — full pipeline every time
# ═══════════════════════════════════════════════════════════════════════════


class TestReinforcementLearningPolicy:
    def test_runs_the_full_reward_belief_world_pipeline(self):
        experience = _milk_experience()
        engine = ReinforcementLearningPolicy(reward_engine=ExperienceRewardEngine(duration_budget_seconds=900.0))

        result = engine.learn(experience)

        assert result.reward == pytest.approx(0.92)
        assert result.belief_updated is True
        assert result.world_updated is True
        assert len(result.signals_applied) == 2
        signal_kinds = {s.kind for s in result.signals_applied}
        assert signal_kinds == {"belief", "world"}

    def test_experience_id_preserved(self):
        experience = _milk_experience()
        result = ReinforcementLearningPolicy().learn(experience)
        assert result.experience_id == experience.experience_id

    def test_original_experience_never_mutated(self):
        experience = _milk_experience()
        ReinforcementLearningPolicy().learn(experience)
        assert experience.signals == ()
        assert experience.reward == 0.0

    def test_outright_failure_decreases_confidence_rather_than_skipping_updates(self):
        """reward=0.0 for a fully failed goal is decisive NEGATIVE evidence,
        not 'no signal' (that's the neutral reward=0.5 case) -- Reinforcement
        always acts on non-neutral reward, so a total failure correctly
        weakens the observation's confidence."""
        obs = LearningObservation(entity="Store A", attribute="stocks_whole_milk", confidence=0.9)
        experience = LearningExperience(outcome=LearningOutcome(goal_achieved=False), observations=(obs,))

        result = ReinforcementLearningPolicy().learn(experience)

        assert result.reward == 0.0
        assert result.belief_updated is True
        assert result.signals_applied[0].direction < 0


# ═══════════════════════════════════════════════════════════════════════════
# PassiveLearningPolicy — reward only, no belief/world influence
# ═══════════════════════════════════════════════════════════════════════════


class TestPassiveLearningPolicy:
    def test_records_reward_without_updating_beliefs_or_world(self):
        experience = _milk_experience()
        engine = PassiveLearningPolicy(reward_engine=ExperienceRewardEngine(duration_budget_seconds=900.0))

        result = engine.learn(experience)

        assert result.reward == pytest.approx(0.92)
        assert result.belief_updated is False
        assert result.world_updated is False
        assert result.signals_applied == ()

    def test_rationale_explains_passivity(self):
        result = PassiveLearningPolicy().learn(_milk_experience())
        assert "passive" in result.rationale.lower()


# ═══════════════════════════════════════════════════════════════════════════
# ConservativeLearningPolicy — threshold gating
# ═══════════════════════════════════════════════════════════════════════════


class TestConservativeLearningPolicy:
    def test_decisive_reward_triggers_updates(self):
        experience = _milk_experience()
        engine = ConservativeLearningPolicy(reward_engine=ExperienceRewardEngine(duration_budget_seconds=900.0))

        result = engine.learn(experience)

        assert result.reward == pytest.approx(0.92)
        assert result.belief_updated is True
        assert result.world_updated is True

    def test_ambiguous_reward_skips_updates(self):
        """Partial credit (base 0.3, |0.3-0.5|=0.2) is well under the
        default 0.3 threshold -- clearly ambiguous, not a boundary case."""
        obs = LearningObservation(entity="Store A", attribute="stocks_whole_milk", confidence=0.9)
        experience = LearningExperience(
            outcome=LearningOutcome(goal_achieved=False, partial=True),
            observations=(obs,),
        )

        result = ConservativeLearningPolicy().learn(experience)

        assert result.reward == pytest.approx(0.3)
        assert result.belief_updated is False
        assert result.world_updated is False
        assert "ambiguous" in result.rationale

    def test_custom_threshold_changes_the_gate(self):
        obs = LearningObservation(entity="Store A", attribute="stocks_whole_milk", confidence=0.9)
        experience = LearningExperience(
            outcome=LearningOutcome(goal_achieved=False, partial=True),
            observations=(obs,),
        )

        lenient = ConservativeLearningPolicy(confidence_threshold=0.1)
        result = lenient.learn(experience)

        assert result.belief_updated is True

    def test_ambiguous_reward_still_recorded(self):
        """Reward is recorded even when no belief/world action is taken --
        conservatism withholds updates, not the historical record."""
        obs = LearningObservation(entity="Store A", attribute="stocks_whole_milk", confidence=0.9)
        experience = LearningExperience(
            outcome=LearningOutcome(goal_achieved=False, partial=True),
            observations=(obs,),
        )

        result = ConservativeLearningPolicy().learn(experience)

        assert result.reward != 0.0


# ═══════════════════════════════════════════════════════════════════════════
# resolve_policy — the LearningPolicy (data) -> LearningPolicyEngine bridge
# ═══════════════════════════════════════════════════════════════════════════


class TestResolvePolicy:
    def test_resolves_reinforcement_by_name(self):
        engine = resolve_policy(LearningPolicy(strategy="reinforcement"))
        assert isinstance(engine, ReinforcementLearningPolicy)

    def test_resolves_passive_by_name(self):
        engine = resolve_policy(LearningPolicy(strategy="passive"))
        assert isinstance(engine, PassiveLearningPolicy)

    def test_resolves_conservative_by_name(self):
        engine = resolve_policy(LearningPolicy(strategy="conservative"))
        assert isinstance(engine, ConservativeLearningPolicy)

    def test_unknown_strategy_falls_back_to_reinforcement(self):
        """Matches the graceful-default precedent IntegratedExecutionEngine
        set in Step 9.7 for unrecognized capabilities -- data that predates
        a newer strategy name shouldn't crash the runtime."""
        engine = resolve_policy(LearningPolicy(strategy="not_a_real_strategy"))
        assert isinstance(engine, ReinforcementLearningPolicy)

    def test_default_policy_resolves_to_reinforcement(self):
        assert isinstance(resolve_policy(), ReinforcementLearningPolicy)

    def test_conservative_parameters_configure_the_threshold(self):
        policy = LearningPolicy(strategy="conservative", parameters={"confidence_threshold": 0.05})
        engine = resolve_policy(policy)
        assert engine.confidence_threshold == 0.05

    def test_domain_learning_policy_stays_pure_data(self):
        """LearningPolicy itself never grows a .learn() method -- the
        behavioral contract lives entirely in LearningPolicyEngine."""
        policy = LearningPolicy()
        assert not hasattr(policy, "learn")


# ═══════════════════════════════════════════════════════════════════════════
# apply_learning_policy — module-level convenience
# ═══════════════════════════════════════════════════════════════════════════


class TestApplyLearningPolicy:
    def test_matches_resolved_engine(self):
        experience = _milk_experience()
        policy = LearningPolicy(strategy="passive")

        via_fn = apply_learning_policy(experience, policy)
        via_engine = resolve_policy(policy).learn(experience)

        assert via_fn.belief_updated == via_engine.belief_updated
        assert via_fn.signals_applied == via_engine.signals_applied

    def test_default_policy_argument_is_reinforcement(self):
        experience = _milk_experience()
        result = apply_learning_policy(experience)
        assert result.belief_updated is True


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
    def test_no_runtime_coupling(self):
        import src.monkey_brain.kernel.pipeline.learning.policies as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "belief_state",
            "execution_state",
            "action_executor",
        ):
            assert forbidden not in imports, f"policies.py must not import: {forbidden}"

    def test_depends_only_on_learning_subpackage(self):
        import src.monkey_brain.kernel.pipeline.learning.policies as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        for module_name in project_imports:
            assert module_name.startswith("src.monkey_brain.kernel.pipeline.learning"), module_name
