"""Tests for capability promotion — learning records candidates; operators activate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from src.monkey_brain.kernel.domains.commerce import CommerceCapabilityBus
from src.monkey_brain.kernel.pipeline.belief_state import PlanStep
from src.monkey_brain.kernel.pipeline.learning.capability_promotion import (
    CapabilityPromotionTracker,
    FrozenPlanStep,
    PromotedDeterministicCapability,
    VerifiedExecutionRecipe,
    activate_promoted_capability,
    deactivate_promoted_capability,
    extract_recipe_from_experience,
    load_recipe,
    promoted_capability_name,
    reset_promotion_state_for_tests,
    try_resolve_promoted_plan,
)


@dataclass
class _FakePlan:
    steps: tuple[Any, ...] = ()


@dataclass
class _FakeExperience:
    plan: _FakePlan
    metadata: dict[str, Any] = field(default_factory=dict)


class _StubCapability:
    def __init__(self, name: str, result: dict[str, Any] | None = None):
        self.name = name
        self._result = result or {"success": True}
        self.calls: list[dict[str, Any]] = []

    def handle(self, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(args)
        return dict(self._result)


@pytest.fixture(autouse=True)
def _clean_promotion_state():
    reset_promotion_state_for_tests()
    yield
    reset_promotion_state_for_tests()


class TestCapabilityPromotionTracker:
    def test_streak_produces_candidate_once_per_run(self):
        tracker = CapabilityPromotionTracker(streak_threshold=3, confidence_threshold=0.75)
        recipe = VerifiedExecutionRecipe(
            goal_signature="acquire_milk",
            steps=(FrozenPlanStep(action="ProductSelection"),),
        )
        results = []
        for _ in range(4):
            results.append(
                tracker.observe(
                    goal_signature="acquire_milk",
                    reward=1.0,
                    confidence=0.9,
                    outcome_summary="ok",
                    top_signal_summary="",
                    recipe=recipe,
                )
            )
        assert results[0] is None
        assert results[1] is None
        assert results[2] is not None
        assert results[2].candidate_id == "acquire_milk::v1"
        assert results[3] is None

        saved = load_recipe("acquire_milk::v1")
        assert saved is not None
        assert saved.steps[0].action == "ProductSelection"

    def test_broken_streak_resets_and_can_re_promote(self):
        tracker = CapabilityPromotionTracker(streak_threshold=2, confidence_threshold=0.75)
        tracker.observe(
            goal_signature="g",
            reward=1.0,
            confidence=0.9,
            outcome_summary="",
            top_signal_summary="",
        )
        first = tracker.observe(
            goal_signature="g",
            reward=1.0,
            confidence=0.9,
            outcome_summary="",
            top_signal_summary="",
        )
        assert first is not None
        assert first.version == 1

        tracker.observe(
            goal_signature="g",
            reward=0.0,
            confidence=0.9,
            outcome_summary="",
            top_signal_summary="",
        )
        tracker.observe(
            goal_signature="g",
            reward=1.0,
            confidence=0.9,
            outcome_summary="",
            top_signal_summary="",
        )
        second = tracker.observe(
            goal_signature="g",
            reward=1.0,
            confidence=0.9,
            outcome_summary="",
            top_signal_summary="",
        )
        assert second is not None
        assert second.version == 2


class TestExtractRecipe:
    def test_extracts_plan_steps_from_experience(self):
        experience = _FakeExperience(
            plan=_FakePlan(
                steps=(
                    PlanStep(action="ProductSelection", parameters={"selection": [{"id": "m1", "qty": 2}]}),
                    PlanStep(action="OrderCreation", depends_on=(0,)),
                )
            ),
            metadata={"goal_name": "acquire_milk"},
        )
        recipe = extract_recipe_from_experience(experience)
        assert recipe is not None
        assert recipe.goal_signature == "default::acquire_milk"
        assert len(recipe.steps) == 2
        assert recipe.steps[1].depends_on == (0,)


class TestPromotedDeterministicCapability:
    def test_replays_verified_steps_through_bus(self):
        bus = CommerceCapabilityBus()
        select = _StubCapability("ProductSelection", {"success": True, "selected": "milk"})
        order = _StubCapability("OrderCreation", {"success": True, "order_id": "o1"})
        bus.register(select)
        bus.register(order)

        recipe = VerifiedExecutionRecipe(
            goal_signature="acquire_milk",
            steps=(
                FrozenPlanStep(action="ProductSelection", parameters={"selection": [{"id": "m1"}]}),
                FrozenPlanStep(action="OrderCreation", depends_on=(0,)),
            ),
            source_candidate_id="acquire_milk::v1",
        )
        promoted = PromotedDeterministicCapability(recipe, bus)
        assert promoted.name == promoted_capability_name("acquire_milk")

        result = promoted.handle({"context": {"actor_id": "alice"}})
        assert result["success"] is True
        assert result["promoted_replay"] is True
        assert len(select.calls) == 1
        assert len(order.calls) == 1

    def test_fails_when_dependency_not_satisfied(self):
        bus = CommerceCapabilityBus()
        bus.register(_StubCapability("OrderCreation", {"success": True}))
        recipe = VerifiedExecutionRecipe(
            goal_signature="g",
            steps=(FrozenPlanStep(action="OrderCreation", depends_on=(0,)),),
            source_candidate_id="g::v1",
        )
        result = PromotedDeterministicCapability(recipe, bus).handle({"context": {}})
        assert result["success"] is False
        assert "blocked" in result["error"]

    def test_replay_denies_missing_permission(self):
        bus = CommerceCapabilityBus()
        bus.register(_StubCapability("OrderCreation", {"success": True}))
        recipe = VerifiedExecutionRecipe(
            goal_signature="g",
            steps=(FrozenPlanStep(action="OrderCreation", required_permission="household_wallet:spend"),),
            source_candidate_id="g::v1",
        )
        result = PromotedDeterministicCapability(recipe, bus).handle(
            {
                "context": {"_resolved_permissions": ("other:perm",)},
            }
        )
        assert result["success"] is False
        assert "permission denied" in result["error"]


class TestOperatorActivation:
    def test_activate_registers_on_bus_and_resolves_plan(self):
        tracker = CapabilityPromotionTracker(streak_threshold=1, confidence_threshold=0.75)
        recipe = VerifiedExecutionRecipe(
            goal_signature="acquire_milk",
            steps=(FrozenPlanStep(action="ProductSelection"),),
        )
        candidate = tracker.observe(
            goal_signature="acquire_milk",
            reward=1.0,
            confidence=0.9,
            outcome_summary="",
            top_signal_summary="",
            recipe=recipe,
        )
        assert candidate is not None

        bus = CommerceCapabilityBus()
        bus.register(_StubCapability("ProductSelection"))
        activated = activate_promoted_capability(candidate.candidate_id, bus)
        assert activated is not None
        assert bus.discover(promoted_capability_name("acquire_milk")) is activated

        plan = try_resolve_promoted_plan("acquire_milk")
        assert plan is not None
        assert plan.planner == "promoted_deterministic"
        assert plan.steps[0].action == "ProductSelection"

    def test_deactivate_removes_from_bus(self):
        tracker = CapabilityPromotionTracker(streak_threshold=1, confidence_threshold=0.75)
        recipe = VerifiedExecutionRecipe(
            goal_signature="g",
            steps=(FrozenPlanStep(action="A"),),
        )
        candidate = tracker.observe(
            goal_signature="g",
            reward=1.0,
            confidence=0.9,
            outcome_summary="",
            top_signal_summary="",
            recipe=recipe,
        )
        bus = CommerceCapabilityBus()
        bus.register(_StubCapability("A"))
        activate_promoted_capability(candidate.candidate_id, bus)
        assert deactivate_promoted_capability("g", bus) is True
        assert try_resolve_promoted_plan("g") is None
        assert bus.discover(promoted_capability_name("g")) is None

    def test_learning_path_does_not_activate(self):
        """integrated_compile_phi observes only — never activates."""
        import inspect

        from src.monkey_brain.kernel.pipeline.learning import integration as learning_integration

        source = inspect.getsource(learning_integration.LearningIntegratedPolicy.configure)
        assert "activate_promoted_capability" not in source
