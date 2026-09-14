"""Tests for Action Execution (Step 9).

Validates action model, execution engine, executor, and runtime integration.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock

from src.monkey_brain.kernel.pipeline.contracts import (
    PipelineRequest,
    CompiledRequest,
    RuntimeContext,
)
from src.monkey_brain.kernel.pipeline.execution import (
    Action,
    ActionOutcome,
    ExecutionResult,
    ExecutionEngine,
)
from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_compiled(question: str = "get 2 l of milk") -> CompiledRequest:
    request = PipelineRequest(question=question, actor_id="user-1", tenant_id="acme")
    ctx = MagicMock()
    ctx.run_id = "run-001"
    return CompiledRequest(
        request=request,
        intent={"intent": "shopping", "confidence": 0.9},
        goal={"name": "acquire_item", "description": "Get milk"},
        intent_ir=MagicMock(),
        goal_ir=MagicMock(goal="get 2 l of milk"),
        execution_context=ctx,
    )


def _make_context() -> RuntimeContext:
    world = MagicMock()
    world.states.return_value = ["start", "middle", "end"]
    world.domains.return_value = ["task"]
    world.nnz.return_value = 5
    return RuntimeContext(world=world, actor=MagicMock())


# ═══════════════════════════════════════════════════════════════════════════
# Action Model
# ═══════════════════════════════════════════════════════════════════════════


class TestActionModel:
    def test_action_construction(self):
        action = Action(
            action_id="a1",
            capability="find_item",
            parameters={"item": "milk"},
            confidence=0.9,
        )
        assert action.action_id == "a1"
        assert action.capability == "find_item"
        assert action.parameters == {"item": "milk"}

    def test_action_immutable(self):
        from dataclasses import FrozenInstanceError

        action = Action(action_id="a1", capability="x")
        with pytest.raises(FrozenInstanceError):
            action.action_id = "a2"

    def test_action_outcome(self):
        outcome = ActionOutcome(
            action_id="a1",
            success=True,
            result={"answer": "found milk"},
            latency_ms=42.5,
        )
        assert outcome.success
        assert outcome.latency_ms == 42.5

    def test_action_outcome_immutable(self):
        from dataclasses import FrozenInstanceError

        outcome = ActionOutcome(action_id="a1", success=True)
        with pytest.raises(FrozenInstanceError):
            outcome.success = False

    def test_execution_result(self):
        result = ExecutionResult(
            actions=(ActionOutcome(action_id="a1", success=True),),
            success_count=1,
            failure_count=0,
            goal_achieved=True,
        )
        assert result.is_success
        assert result.success_rate == 1.0

    def test_execution_result_partial(self):
        result = ExecutionResult(
            actions=(
                ActionOutcome(action_id="a1", success=True),
                ActionOutcome(action_id="a2", success=False),
            ),
            success_count=1,
            failure_count=1,
            goal_achieved=False,
        )
        assert not result.is_success
        assert result.success_rate == 0.5

    def test_execution_result_empty(self):
        result = ExecutionResult(goal_achieved=True)
        assert result.is_success
        assert result.success_rate == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# ActionExecutor
# ═══════════════════════════════════════════════════════════════════════════


class TestActionExecutor:
    """ActionExecutor.execute() is `async def` (kernel/pipeline/action_executor.py) --
    every method here must be async and await it. Previously these were
    plain sync defs calling execute() unawaited, silently asserting on a
    bare coroutine object (AttributeError on every attribute access,
    RuntimeWarning: coroutine was never awaited) -- a pre-existing
    regression, fixed here without changing what each test verifies."""

    @pytest.mark.asyncio
    async def test_execute_empty_actions(self):
        executor = ActionExecutor()
        result = await executor.execute(())
        assert result.is_success
        assert result.goal_achieved

    @pytest.mark.asyncio
    async def test_execute_simulates_without_bus(self):
        executor = ActionExecutor(capability_bus=None)
        actions = (Action(action_id="a1", capability="find_milk"),)
        result = await executor.execute(actions)
        assert result.success_count == 1
        assert result.failure_count == 0
        assert result.goal_achieved

    @pytest.mark.asyncio
    async def test_execute_with_capability_bus(self):
        bus = MagicMock()
        capability = MagicMock()
        capability.handle.return_value = {"success": True, "answer": "found milk"}
        bus.discover.return_value = capability

        executor = ActionExecutor(capability_bus=bus)
        actions = (Action(action_id="a1", capability="find_milk"),)
        result = await executor.execute(actions)

        assert result.success_count == 1
        bus.discover.assert_called_once_with("find_milk")

    @pytest.mark.asyncio
    async def test_execute_missing_capability(self):
        bus = MagicMock()
        bus.discover.return_value = None

        executor = ActionExecutor(capability_bus=bus)
        actions = (Action(action_id="a1", capability="nonexistent"),)
        result = await executor.execute(actions)

        assert result.failure_count == 1
        assert not result.goal_achieved

    @pytest.mark.asyncio
    async def test_execute_multiple_actions(self):
        executor = ActionExecutor(capability_bus=None)
        # step_index must be distinct (as the real plan_compiler.py always
        # sets it, step_index=i per compiled step) -- the executor's
        # scheduling loop tracks "already scheduled" by step_index, and
        # Action.step_index defaults to the shared sentinel -1 for every
        # action that doesn't set one, which previously made it treat a2
        # and a3 as "the same already-scheduled step" as a1 and stop after
        # just one action.
        actions = (
            Action(action_id="a1", capability="step1", step_index=0),
            Action(action_id="a2", capability="step2", step_index=1),
            Action(action_id="a3", capability="step3", step_index=2),
        )
        result = await executor.execute(actions)
        assert result.success_count == 3
        assert result.goal_achieved

    @pytest.mark.asyncio
    async def test_execute_exception_handling(self):
        bus = MagicMock()
        bus.discover.side_effect = RuntimeError("bus crashed")

        executor = ActionExecutor(capability_bus=bus)
        actions = (Action(action_id="a1", capability="fail"),)
        result = await executor.execute(actions)

        assert result.failure_count == 1
        assert "bus crashed" in result.actions[0].error


# ═══════════════════════════════════════════════════════════════════════════
# Runtime Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionIntegration:
    @pytest.mark.asyncio
    async def test_execute_plan_produces_actions(self):
        """Executing a plan should produce ActionOutcomes."""
        rt = CognitiveRuntime()
        result = await rt.run(_make_compiled(), _make_context())

        assert "actions" in result
        assert "outcome" in result
        assert isinstance(result["actions"], list)

    @pytest.mark.asyncio
    async def test_outcome_reflects_execution(self):
        """Outcome should reflect execution results."""
        rt = CognitiveRuntime()
        result = await rt.run(_make_compiled(), _make_context())

        assert "goal_achieved" in result["outcome"]
        assert "success_count" in result["outcome"]
        assert "failure_count" in result["outcome"]

    @pytest.mark.asyncio
    async def test_custom_execution_engine(self):
        """A custom ExecutionEngine can be injected."""

        class MockExecutor:
            # Must be async: _execute_plan (belief_runtime.py) always
            # does `await self._execution_engine.execute(...)`. This test
            # was previously passing for the wrong reason -- planning
            # itself crashed first (the autouse fake LLM backend's
            # complete() was synchronous too, fixed in tests/conftest.py
            # this same pass), so _execute_plan's own empty-plan
            # early-return meant this method was never actually reached.
            async def execute(self, actions, context=None, *, execution_graph=None):
                return ExecutionResult(
                    actions=tuple(
                        ActionOutcome(action_id=a.action_id, success=True, result={"mock": True}) for a in actions
                    ),
                    success_count=len(actions),
                    goal_achieved=True,
                )

        rt = CognitiveRuntime(execution_engine=MockExecutor())
        result = await rt.run(_make_compiled(), _make_context())

        assert result["outcome"]["goal_achieved"]
        assert result["outcome"]["success_count"] >= 0

    @pytest.mark.asyncio
    async def test_actions_are_typed(self):
        """Actions in the result should be typed ActionOutcome dicts."""
        rt = CognitiveRuntime()
        result = await rt.run(_make_compiled(), _make_context())

        for action in result["actions"]:
            assert "action_id" in action
            assert "success" in action
            assert "error" in action
            assert "latency_ms" in action

    @pytest.mark.asyncio
    async def test_plan_steps_become_actions(self):
        """Plan steps should be converted to Actions before execution."""
        from src.monkey_brain.kernel.pipeline.cognitive_policy import CognitivePolicy

        actions_captured = []

        async def capture_execute(s):
            actions_captured.extend(s.actions)
            return s

        async def noop(s):
            return s

        policy = CognitivePolicy()
        policy.configure(
            observe=noop,
            believe=noop,
            plan=noop,
            execute=capture_execute,
            observe_outcome=noop,
            learn=noop,
            compile_phi=noop,
            predict=noop,
            commit=noop,
        )

        rt = CognitiveRuntime(policy=policy)
        await rt.run(_make_compiled(), _make_context())

        # Actions should be ActionOutcome instances
        for a in actions_captured:
            assert isinstance(a, ActionOutcome)
