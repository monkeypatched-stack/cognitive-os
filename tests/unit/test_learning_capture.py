"""Tests for experience capture (Step 10.2).

Validates ExperienceBuilder against real CognitiveState objects produced by
actually running the cognitive lifecycle (not hand-built mocks standing in
for the whole pipeline) — both the default LLMPlanner/
ActionExecutor path and the rich IntegratedPlanningEngine/
IntegratedExecutionEngine path, confirming "every cognitive cycle" really
does mean every cycle, not just ones using the new pipelines. Also
validates experience_to_dict()'s serialization of arbitrary goal/plan/
execution values.
"""

from __future__ import annotations

import json

import pytest

from src.monkey_brain.kernel.pipeline.contracts import (
    PipelineRequest,
    CompiledRequest,
    RuntimeContext,
)
from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime
from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.belief_state import BeliefState
from src.monkey_brain.kernel.pipeline.actor import Actor
from src.monkey_brain.kernel.pipeline.planning import IntegratedPlanningEngine
from src.monkey_brain.kernel.pipeline.execution_runtime import IntegratedExecutionEngine
from src.monkey_brain.kernel.pipeline.learning.domain import LearningExperience
from src.monkey_brain.kernel.pipeline.learning.capture import (
    ExperienceBuilder,
    capture_experience,
    experience_to_dict,
)


def _make_compiled(question: str = "get 2 l of milk", goal_name: str = "acquire_milk") -> CompiledRequest:
    import unittest.mock as mock

    request = PipelineRequest(question=question, actor_id="user-1", tenant_id="acme")
    ctx = mock.MagicMock()
    ctx.run_id = "run-001"
    return CompiledRequest(
        request=request,
        intent={"intent": "shopping", "confidence": 0.9},
        goal={"name": goal_name, "description": "Get milk"},
        intent_ir=mock.MagicMock(),
        goal_ir=mock.MagicMock(goal=question),
        execution_context=ctx,
    )


def _make_context() -> RuntimeContext:
    import unittest.mock as mock

    world = mock.MagicMock()
    world.states.return_value = ["start", "middle", "end"]
    world.domains.return_value = ["task"]
    world.nnz.return_value = 5
    return RuntimeContext(world=world, actor=mock.MagicMock())


async def _run_cycle(runtime: CognitiveRuntime, goal_name: str = "acquire_milk") -> CognitiveState:
    compiled = _make_compiled(goal_name=goal_name)
    context = _make_context()
    actor = Actor(actor_id="user-1", tenant_id="acme")
    state = CognitiveState(
        compiled=compiled,
        context=context,
        actor=actor,
        belief=BeliefState(actor_id="user-1", tenant_id="acme"),
    )
    actor.start_reasoning()
    return await runtime._policy.execute(state)


# ═══════════════════════════════════════════════════════════════════════════
# Capture against the default (untouched) pipeline
# ═══════════════════════════════════════════════════════════════════════════


class TestCaptureDefaultPipeline:
    @pytest.mark.asyncio
    async def test_captures_a_real_experience(self):
        rt = CognitiveRuntime()
        state = await _run_cycle(rt)

        experience = capture_experience(state)

        assert isinstance(experience, LearningExperience)
        assert experience.goal.name == "acquire_milk"
        assert experience.provenance.actor_id == "user-1"
        assert experience.provenance.tenant_id == "acme"
        assert experience.provenance.run_id == "run-001"

    @pytest.mark.asyncio
    async def test_reward_and_signals_stay_unset(self):
        """No learning algorithm yet — only capture."""
        rt = CognitiveRuntime()
        state = await _run_cycle(rt)

        experience = capture_experience(state)

        assert experience.reward == 0.0
        assert experience.signals == ()

    @pytest.mark.asyncio
    async def test_outcome_reflects_state_outcome(self):
        rt = CognitiveRuntime()
        state = await _run_cycle(rt)

        experience = capture_experience(state)

        assert experience.outcome.goal_achieved == state.outcome["goal_achieved"]

    @pytest.mark.asyncio
    async def test_confidence_matches_belief_confidence(self):
        rt = CognitiveRuntime()
        state = await _run_cycle(rt)

        experience = capture_experience(state)

        assert experience.confidence == state.belief.confidence()

    @pytest.mark.asyncio
    async def test_observations_extracted_from_belief_facts(self):
        rt = CognitiveRuntime()
        state = await _run_cycle(rt)

        experience = capture_experience(state)

        assert len(experience.observations) == len(state.belief.facts)
        if state.belief.facts:
            assert experience.observations[0].entity == state.belief.facts[0].entity

    @pytest.mark.asyncio
    async def test_events_extracted_from_execution_trace(self):
        rt = CognitiveRuntime()
        state = await _run_cycle(rt)

        experience = capture_experience(state)

        assert len(experience.events) == len(state.execution_trace)


# ═══════════════════════════════════════════════════════════════════════════
# Capture against the rich pipeline — "every cognitive cycle," not just default
# ═══════════════════════════════════════════════════════════════════════════


class TestCaptureRichPipeline:
    @pytest.mark.asyncio
    async def test_captures_planning_domain_plan_object(self):
        """goal/plan/execution must accept whatever the actual cycle
        produced — here, real planning.domain.Plan / execution.py
        ExecutionResult objects, not just belief_state's simpler shapes."""
        rt = CognitiveRuntime(
            planning_engine=IntegratedPlanningEngine(),
            execution_engine=IntegratedExecutionEngine(),
        )
        state = await _run_cycle(rt)

        experience = capture_experience(state)

        assert experience.plan is state.belief.plan
        assert experience.execution is state.execution_result

    @pytest.mark.asyncio
    async def test_captures_known_execution_limitation_faithfully(self):
        """The Step 9.7 parameter-loss limitation (Navigate missing
        'destination') must show up transcribed in the captured outcome,
        not hidden or smoothed over — capture is pure transcription."""
        rt = CognitiveRuntime(
            planning_engine=IntegratedPlanningEngine(),
            execution_engine=IntegratedExecutionEngine(),
        )
        state = await _run_cycle(rt)

        experience = capture_experience(state)

        assert experience.outcome.goal_achieved is False


# ═══════════════════════════════════════════════════════════════════════════
# ExperienceBuilder — configurability
# ═══════════════════════════════════════════════════════════════════════════


class TestExperienceBuilderConfiguration:
    @pytest.mark.asyncio
    async def test_custom_default_source(self):
        rt = CognitiveRuntime()
        state = await _run_cycle(rt)

        builder = ExperienceBuilder(default_source="user_feedback")
        experience = builder.capture(state)

        assert experience.provenance.source == "user_feedback"

    @pytest.mark.asyncio
    async def test_module_level_function_matches_default_builder(self):
        rt = CognitiveRuntime()
        state = await _run_cycle(rt)

        via_function = capture_experience(state)
        via_builder = ExperienceBuilder().capture(state)

        assert via_function.provenance.source == via_builder.provenance.source
        assert via_function.outcome == via_builder.outcome


# ═══════════════════════════════════════════════════════════════════════════
# Serialization
# ═══════════════════════════════════════════════════════════════════════════


class TestExperienceSerialization:
    @pytest.mark.asyncio
    async def test_json_serializable_with_rich_dataclass_fields(self):
        """The hard case: goal/plan/execution hold real nested dataclasses
        (planning.domain.Plan containing PlanStep containing PlanningOperator,
        etc.) — must serialize without raising."""
        rt = CognitiveRuntime(
            planning_engine=IntegratedPlanningEngine(),
            execution_engine=IntegratedExecutionEngine(),
        )
        state = await _run_cycle(rt)
        experience = capture_experience(state)

        d = experience_to_dict(experience)
        json.dumps(d)  # must not raise

    def test_arbitrary_non_dataclass_values_fall_back_to_str(self):
        experience = LearningExperience(goal=object(), plan="a plain string plan", execution=42)
        d = experience_to_dict(experience)

        json.dumps(d)  # must not raise
        assert isinstance(d["goal"], str)
        assert d["plan"] == "a plain string plan"
        assert d["execution"] == 42

    def test_none_values_serialize_to_none(self):
        experience = LearningExperience(goal=None, plan=None, execution=None)
        d = experience_to_dict(experience)
        assert d["goal"] is None
        assert d["plan"] is None
        assert d["execution"] is None

    def test_dict_typed_goal_serializes_recursively(self):
        experience = LearningExperience(goal={"name": "acquire_milk", "nested": {"x": 1}})
        d = experience_to_dict(experience)
        assert d["goal"] == {"name": "acquire_milk", "nested": {"x": 1}}

    def test_all_top_level_fields_present(self):
        experience = LearningExperience()
        d = experience_to_dict(experience)
        for key in (
            "experience_id",
            "goal",
            "plan",
            "execution",
            "observations",
            "outcome",
            "reward",
            "confidence",
            "timestamp",
            "provenance",
            "events",
            "signals",
            "metadata",
        ):
            assert key in d


# ═══════════════════════════════════════════════════════════════════════════
# Immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestImmutability:
    @pytest.mark.asyncio
    async def test_captured_experience_is_frozen(self):
        from dataclasses import FrozenInstanceError

        rt = CognitiveRuntime()
        state = await _run_cycle(rt)
        experience = capture_experience(state)

        with pytest.raises(FrozenInstanceError):
            experience.reward = 1.0
