"""Tests for candidate plan generation (Step 8.5).

Validates the CandidateGenerator: multiple alternative PlanCandidates per
goal, each built from a registered PlanStrategy's operator sequence, none
scored yet (that's Step 8.6).
"""

from __future__ import annotations

from src.monkey_brain.kernel.pipeline.planning.domain import Goal, PlanCandidate
from src.monkey_brain.kernel.pipeline.planning.operators import OPERATOR_TEMPLATES
from src.monkey_brain.kernel.pipeline.planning.candidates import (
    OperatorCall,
    PlanStrategy,
    GenerationRule,
    DEFAULT_GENERATION_RULES,
    CandidateGenerator,
)

# ═══════════════════════════════════════════════════════════════════════════
# Default "acquire_milk" scenario — matches Step 8's own acceptance example
# ═══════════════════════════════════════════════════════════════════════════


class TestAcquireMilkScenario:
    def test_produces_three_candidates(self):
        generator = CandidateGenerator()
        candidates = generator.generate(Goal(name="acquire_milk"))
        assert len(candidates) == 3

    def test_candidate_strategy_names_match_acceptance_example(self):
        generator = CandidateGenerator()
        candidates = generator.generate(Goal(name="acquire_milk"))
        names = {c.plan.metadata["strategy"] for c in candidates}
        assert names == {"walk_to_store", "drive_to_store", "delivery_service"}

    def test_all_candidates_wrap_a_real_plan(self):
        generator = CandidateGenerator()
        candidates = generator.generate(Goal(name="acquire_milk"))
        for c in candidates:
            assert isinstance(c, PlanCandidate)
            assert len(c.plan.steps) == 2
            assert c.plan.goal.name == "acquire_milk"

    def test_candidates_have_different_cost_and_duration_profiles(self):
        """The whole point of alternatives: they trade off differently."""
        generator = CandidateGenerator()
        candidates = generator.generate(Goal(name="acquire_milk"))
        costs = {c.plan.estimated_cost for c in candidates}
        durations = {c.plan.estimated_duration for c in candidates}
        assert len(costs) == 3  # all distinct
        assert len(durations) == 3

    def test_walk_is_slower_but_cheaper_than_drive(self):
        generator = CandidateGenerator()
        by_strategy = {c.plan.metadata["strategy"]: c.plan for c in generator.generate(Goal(name="acquire_milk"))}
        walk, drive = by_strategy["walk_to_store"], by_strategy["drive_to_store"]
        assert walk.estimated_cost < drive.estimated_cost
        assert walk.estimated_duration > drive.estimated_duration


# ═══════════════════════════════════════════════════════════════════════════
# "Do not score yet" — the explicit constraint for this step
# ═══════════════════════════════════════════════════════════════════════════


class TestNoScoringYet:
    def test_all_candidates_unscored(self):
        generator = CandidateGenerator()
        candidates = generator.generate(Goal(name="acquire_milk"))
        assert all(c.score is None for c in candidates)

    def test_all_candidates_not_rejected(self):
        generator = CandidateGenerator()
        candidates = generator.generate(Goal(name="acquire_milk"))
        assert all(c.rejected is False for c in candidates)

    def test_plan_confidence_stays_unset(self):
        generator = CandidateGenerator()
        candidates = generator.generate(Goal(name="acquire_milk"))
        assert all(c.plan.confidence == 0.0 for c in candidates)


# ═══════════════════════════════════════════════════════════════════════════
# Unregistered goals
# ═══════════════════════════════════════════════════════════════════════════


class TestUnregisteredGoal:
    def test_no_rule_produces_no_candidates(self):
        generator = CandidateGenerator()
        assert generator.generate(Goal(name="never_registered")) == ()


# ═══════════════════════════════════════════════════════════════════════════
# Custom rule registration
# ═══════════════════════════════════════════════════════════════════════════


class TestCustomRuleRegistration:
    def test_register_new_rule(self):
        generator = CandidateGenerator(rules={})
        generator.register_rule(
            GenerationRule(
                goal_name="get_coffee",
                strategies=(
                    PlanStrategy(
                        name="brew_at_home",
                        operator_calls=(OperatorCall(operator_name="Wait", params={"duration_seconds": 300.0}),),
                    ),
                    PlanStrategy(
                        name="buy_from_cafe",
                        operator_calls=(OperatorCall(operator_name="Navigate", params={"destination": "cafe"}),),
                    ),
                ),
            )
        )

        candidates = generator.generate(Goal(name="get_coffee"))

        assert len(candidates) == 2
        assert {c.plan.metadata["strategy"] for c in candidates} == {
            "brew_at_home",
            "buy_from_cafe",
        }

    def test_default_rules_not_mutated_by_registration(self):
        generator = CandidateGenerator()
        generator.register_rule(GenerationRule(goal_name="custom_goal", strategies=()))
        assert "custom_goal" not in DEFAULT_GENERATION_RULES

    def test_unresolvable_operator_reference_is_skipped_not_invented(self):
        generator = CandidateGenerator(rules={})
        generator.register_rule(
            GenerationRule(
                goal_name="broken",
                strategies=(
                    PlanStrategy(
                        name="uses_nonexistent_operator",
                        operator_calls=(OperatorCall(operator_name="DoesNotExist", params={}),),
                    ),
                ),
            )
        )

        candidates = generator.generate(Goal(name="broken"))

        assert len(candidates) == 1
        assert candidates[0].plan.steps == ()  # nothing resolvable — empty plan, not a fabricated step


# ═══════════════════════════════════════════════════════════════════════════
# Step ordering and operator template usage
# ═══════════════════════════════════════════════════════════════════════════


class TestStepConstruction:
    def test_steps_are_sequenced_in_operator_call_order(self):
        generator = CandidateGenerator()
        candidates = generator.generate(Goal(name="acquire_milk"))
        walk_plan = next(c.plan for c in candidates if c.plan.metadata["strategy"] == "walk_to_store")
        assert [s.sequence for s in walk_plan.steps] == [0, 1]
        assert walk_plan.steps[0].operator.name == "Navigate"
        assert walk_plan.steps[1].operator.name == "AcquireItem"

    def test_uses_real_operator_templates_from_step_8_2(self):
        """Operators built here must be indistinguishable from calling the
        template directly — no parallel/duplicate operator construction."""
        generator = CandidateGenerator()
        candidates = generator.generate(Goal(name="acquire_milk"))
        walk_plan = next(c.plan for c in candidates if c.plan.metadata["strategy"] == "walk_to_store")
        acquire_step = walk_plan.steps[1]
        expected = OPERATOR_TEMPLATES["AcquireItem"].build(item="milk", quantity=2, unit="L")
        assert acquire_step.operator == expected

    def test_description_override_is_applied(self):
        generator = CandidateGenerator()
        candidates = generator.generate(Goal(name="acquire_milk"))
        walk_plan = next(c.plan for c in candidates if c.plan.metadata["strategy"] == "walk_to_store")
        assert walk_plan.steps[0].description == "Walk to the store"


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_runtime_or_execution_imports(self):
        import inspect
        import src.monkey_brain.kernel.pipeline.planning.candidates as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "action_executor",
            "from src.monkey_brain.kernel.pipeline.execution import",
        ]
        for imp in forbidden:
            assert imp not in source, f"candidates.py must not import: {imp}"
