"""Plan-reuse / LLM-call-avoidance classification for the edge hot path.

The cognitive loop must not call an LLM merely because a new tick
occurred. This module classifies, PURELY from already-available local
state (never by calling the LLM itself to find out), what level of fresh
reasoning a tick actually needs:

    NO_REASONING_REQUIRED  -- no goal, or goal already satisfied; nothing
                              to plan for at all.
    LOCAL_RULE             -- a deterministic policy/rule already
                              determines the next action (no planning
                              needed at all, LLM or otherwise).
    REUSE_EXISTING_PLAN    -- a previously committed plan remains valid:
                              same goal, compatible world state, valid
                              policy, no invalidating observation.
    REPLAN_REQUIRED        -- something changed enough that the existing
                              plan can no longer be trusted, but this is
                              a known, bounded re-planning situation.
    LLM_REQUIRED           -- genuinely novel reasoning is needed.

This module NEVER calls an LLM, NEVER calls TransitionModel/Comparator
itself -- it is a pure decision function over inputs the caller already
has (kernel/pipeline/belief_runtime.py's own TransitionModel gate remains
the authority on "does this observation invalidate the plan"; this module
only decides whether that check is even worth doing, i.e. whether
reasoning is needed at all before reaching for the expensive machinery).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ReasoningNeed(Enum):
    NO_REASONING_REQUIRED = "NO_REASONING_REQUIRED"
    LOCAL_RULE = "LOCAL_RULE"
    REUSE_EXISTING_PLAN = "REUSE_EXISTING_PLAN"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    LLM_REQUIRED = "LLM_REQUIRED"


@dataclass(frozen=True)
class ReasoningDecision:
    need: ReasoningNeed
    reason: str


@dataclass(frozen=True)
class CommittedPlanRecord:
    """What the caller retains about its last committed plan -- enough
    to judge reusability without re-deriving it. `plan` itself
    (kernel/pipeline/belief_state.py::Plan) is already an immutable,
    frozen dataclass -- this only adds the version stamps needed to
    decide reuse, it does not wrap or replace Plan."""
    plan: Any
    goal_hash: str
    world_state_version: str
    policy_version: str
    committed_at: float


def classify_reasoning_need(
    *, goal: Any, goal_achieved: bool, goal_hash: str,
    world_state_version: str, policy_version: str,
    committed_plan: CommittedPlanRecord | None,
    plan_invalidated_by_observation: bool = False,
    local_rule_available: bool = False,
) -> ReasoningDecision:
    """Pure classification. `plan_invalidated_by_observation` is the
    ONE input this function trusts from the caller's own
    TransitionModel/Comparator check (belief_runtime.py) -- it never
    re-derives that judgment itself, only acts on it.
    """
    if goal is None or goal_achieved:
        return ReasoningDecision(ReasoningNeed.NO_REASONING_REQUIRED, "no active, unsatisfied goal")

    if local_rule_available:
        return ReasoningDecision(ReasoningNeed.LOCAL_RULE, "a deterministic local rule already covers this goal")

    if committed_plan is not None and not plan_invalidated_by_observation:
        same_goal = committed_plan.goal_hash == goal_hash
        same_world = committed_plan.world_state_version == world_state_version
        same_policy = committed_plan.policy_version == policy_version
        if same_goal and same_world and same_policy:
            return ReasoningDecision(
                ReasoningNeed.REUSE_EXISTING_PLAN,
                "goal, world-state version, and policy version are all unchanged since the last commit",
            )
        return ReasoningDecision(
            ReasoningNeed.REPLAN_REQUIRED,
            f"one or more inputs changed since last commit (goal={same_goal} world={same_world} policy={same_policy})",
        )

    if committed_plan is not None and plan_invalidated_by_observation:
        return ReasoningDecision(
            ReasoningNeed.REPLAN_REQUIRED,
            "a new observation invalidated the committed plan (TransitionModel/Comparator)",
        )

    return ReasoningDecision(ReasoningNeed.LLM_REQUIRED, "no committed plan exists for this goal")
