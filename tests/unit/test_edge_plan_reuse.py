"""classify_reasoning_need (kernel/edge/plan_reuse.py) -- proves the edge
hot path never reaches for the LLM merely because a tick occurred."""
from __future__ import annotations

from src.monkey_brain.kernel.edge.plan_reuse import (
    CommittedPlanRecord,
    ReasoningNeed,
    classify_reasoning_need,
)


def _plan(goal_hash="g1", world="w1", policy="p1"):
    return CommittedPlanRecord(
        plan=object(), goal_hash=goal_hash, world_state_version=world,
        policy_version=policy, committed_at=0.0,
    )


class TestNoReasoningRequired:
    def test_no_goal(self):
        d = classify_reasoning_need(
            goal=None, goal_achieved=False, goal_hash="", world_state_version="w1",
            policy_version="p1", committed_plan=None,
        )
        assert d.need is ReasoningNeed.NO_REASONING_REQUIRED

    def test_goal_already_achieved(self):
        d = classify_reasoning_need(
            goal="buy milk", goal_achieved=True, goal_hash="g1", world_state_version="w1",
            policy_version="p1", committed_plan=_plan(),
        )
        assert d.need is ReasoningNeed.NO_REASONING_REQUIRED


class TestLocalRule:
    def test_local_rule_short_circuits_before_plan_reuse_is_even_considered(self):
        d = classify_reasoning_need(
            goal="buy milk", goal_achieved=False, goal_hash="g1", world_state_version="w1",
            policy_version="p1", committed_plan=_plan(goal_hash="different"),
            local_rule_available=True,
        )
        assert d.need is ReasoningNeed.LOCAL_RULE


class TestReuseExistingPlan:
    def test_unchanged_goal_world_and_policy_reuses_the_plan(self):
        d = classify_reasoning_need(
            goal="buy milk", goal_achieved=False, goal_hash="g1", world_state_version="w1",
            policy_version="p1", committed_plan=_plan(goal_hash="g1", world="w1", policy="p1"),
        )
        assert d.need is ReasoningNeed.REUSE_EXISTING_PLAN


class TestReplanRequired:
    def test_world_state_drift_forces_replan(self):
        d = classify_reasoning_need(
            goal="buy milk", goal_achieved=False, goal_hash="g1", world_state_version="w2",
            policy_version="p1", committed_plan=_plan(goal_hash="g1", world="w1", policy="p1"),
        )
        assert d.need is ReasoningNeed.REPLAN_REQUIRED

    def test_policy_drift_forces_replan(self):
        d = classify_reasoning_need(
            goal="buy milk", goal_achieved=False, goal_hash="g1", world_state_version="w1",
            policy_version="p2", committed_plan=_plan(goal_hash="g1", world="w1", policy="p1"),
        )
        assert d.need is ReasoningNeed.REPLAN_REQUIRED

    def test_different_goal_forces_replan_not_llm_required(self):
        d = classify_reasoning_need(
            goal="buy bread", goal_achieved=False, goal_hash="g2", world_state_version="w1",
            policy_version="p1", committed_plan=_plan(goal_hash="g1", world="w1", policy="p1"),
        )
        assert d.need is ReasoningNeed.REPLAN_REQUIRED

    def test_observation_invalidation_forces_replan_even_if_versions_match(self):
        d = classify_reasoning_need(
            goal="buy milk", goal_achieved=False, goal_hash="g1", world_state_version="w1",
            policy_version="p1", committed_plan=_plan(goal_hash="g1", world="w1", policy="p1"),
            plan_invalidated_by_observation=True,
        )
        assert d.need is ReasoningNeed.REPLAN_REQUIRED


class TestLlmRequired:
    def test_no_committed_plan_and_no_local_rule_requires_the_llm(self):
        d = classify_reasoning_need(
            goal="buy milk", goal_achieved=False, goal_hash="g1", world_state_version="w1",
            policy_version="p1", committed_plan=None,
        )
        assert d.need is ReasoningNeed.LLM_REQUIRED
