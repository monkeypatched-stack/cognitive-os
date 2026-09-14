"""Tests for execution-time permission enforcement (CCB-203 follow-up):
a PlanStep.required_permission the actor's resolved permissions don't
have is rejected before it ever reaches the execution engine — a real
kernel-level check, not just information available to the planner.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime
from src.monkey_brain.kernel.pipeline.belief_state import (
    BeliefState,
    Goal,
    Plan,
    PlanStep,
)
from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.actor import Actor
from src.monkey_brain.kernel.pipeline.contracts import (
    CompiledRequest,
    PipelineRequest,
    RuntimeContext,
)
from unittest.mock import MagicMock


def _state_with_plan(plan: Plan, resolved_permissions: frozenset = frozenset()) -> CognitiveState:
    belief = BeliefState(actor_id="alice", plan=plan)
    belief.metadata["_resolved_permissions"] = resolved_permissions
    compiled = CompiledRequest(
        request=PipelineRequest(question="q", actor_id="alice", tenant_id="acme"),
        intent={},
        goal={},
        intent_ir=MagicMock(),
        goal_ir=MagicMock(),
        execution_context=MagicMock(),
    )
    context = RuntimeContext(world=MagicMock(), actor=MagicMock())
    return CognitiveState(compiled=compiled, context=context, actor=Actor(actor_id="alice"), belief=belief)


@pytest.mark.asyncio
async def test_step_missing_required_permission_is_rejected_before_executor():
    plan = Plan(
        goal="spend",
        steps=(
            PlanStep(
                action="spend_wallet",
                description="spend household funds",
                required_permission="household_wallet:spend",
                confidence=0.9,
            ),
        ),
    )
    state = _state_with_plan(plan, resolved_permissions=frozenset())

    rt = CognitiveRuntime()
    result_state = await rt._execute_plan(state)

    assert len(result_state.actions) == 1
    outcome = result_state.actions[0]
    assert outcome.success is False
    assert "Permission denied" in outcome.error
    assert "household_wallet:spend" in outcome.error
    assert result_state.execution_result.goal_achieved is False
    assert result_state.execution_result.failure_count == 1


@pytest.mark.asyncio
async def test_step_with_granted_permission_executes_normally():
    plan = Plan(
        goal="spend",
        steps=(
            PlanStep(
                action="spend_wallet",
                description="spend household funds",
                required_permission="household_wallet:spend",
                confidence=0.9,
            ),
        ),
    )
    state = _state_with_plan(plan, resolved_permissions=frozenset({"household_wallet:spend"}))

    rt = CognitiveRuntime()
    result_state = await rt._execute_plan(state)

    assert len(result_state.actions) == 1
    # Reached the real (simulated, no capability bus) executor rather than
    # being denied outright — simulated actions succeed by default.
    assert result_state.actions[0].error != "Permission denied: missing household_wallet:spend"


@pytest.mark.asyncio
async def test_step_with_no_required_permission_is_unaffected():
    plan = Plan(
        goal="look",
        steps=(PlanStep(action="check_pantry", description="just looking", confidence=0.9),),
    )
    state = _state_with_plan(plan, resolved_permissions=frozenset())

    rt = CognitiveRuntime()
    result_state = await rt._execute_plan(state)

    assert len(result_state.actions) == 1
    assert "Permission denied" not in result_state.actions[0].error


@pytest.mark.asyncio
async def test_mixed_plan_denies_only_the_unpermitted_step_preserving_order():
    plan = Plan(
        goal="mixed",
        steps=(
            PlanStep(action="check_pantry", description="look", confidence=0.9),
            PlanStep(
                action="spend_wallet",
                description="spend household funds",
                required_permission="household_wallet:spend",
                confidence=0.9,
            ),
            PlanStep(action="report_back", description="report", confidence=0.9),
        ),
    )
    state = _state_with_plan(plan, resolved_permissions=frozenset())

    rt = CognitiveRuntime()
    result_state = await rt._execute_plan(state)

    assert len(result_state.actions) == 3
    assert "Permission denied" not in result_state.actions[0].error
    assert "Permission denied" in result_state.actions[1].error
    assert "Permission denied" not in result_state.actions[2].error
    assert result_state.execution_result.failure_count == 1
    assert result_state.execution_result.success_count == 2
