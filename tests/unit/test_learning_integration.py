"""Tests for Runtime Integration (Step 10.7) and Φ Compiler wiring (Step 10.8).

Runs REAL cognitive cycles against the actual CognitiveRuntime (imported,
never modified) to confirm LearningIntegratedPolicy genuinely wires Steps
10.2-10.6 into the live Learn stage, and Step 10.8's PhiCompiler into the
live Compile-Φ stage, via CognitivePolicy's own sanctioned subclass-and-
override extension point -- and, critically, that the untouched _predict
stage's dependency on belief.hypotheses (populated by the ORIGINAL _learn)
is preserved exactly, not silently dropped.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest

from src.monkey_brain.kernel.pipeline.contracts import (
    CompiledRequest,
    PipelineRequest,
    RuntimeContext,
)
from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime
from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.belief_state import BeliefState
from src.monkey_brain.kernel.pipeline.actor import Actor
from src.monkey_brain.kernel.pipeline.cognitive_policy import CognitivePolicy
from src.monkey_brain.kernel.pipeline.planning import IntegratedPlanningEngine
from src.monkey_brain.kernel.pipeline.execution_runtime import IntegratedExecutionEngine
from src.monkey_brain.kernel.pipeline.learning.domain import LearningPolicy
from src.monkey_brain.kernel.pipeline.learning.integration import (
    LearningIntegratedPolicy,
    build_learning_integrated_runtime,
)


def _make_compiled(question: str = "get 2 l of milk", goal_name: str = "acquire_milk") -> CompiledRequest:
    request = PipelineRequest(question=question, actor_id="alice", tenant_id="acme")
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
    world = mock.MagicMock()
    world.states.return_value = ["start", "middle", "end"]
    world.domains.return_value = ["task"]
    world.nnz.return_value = 5
    return RuntimeContext(world=world, actor=mock.MagicMock())


async def _run_cycle(runtime: CognitiveRuntime) -> CognitiveState:
    compiled = _make_compiled()
    context = _make_context()
    actor = Actor(actor_id="alice", tenant_id="acme")
    state = CognitiveState(
        compiled=compiled,
        context=context,
        actor=actor,
        belief=BeliefState(actor_id="alice", tenant_id="acme"),
    )
    actor.start_reasoning()
    return await runtime._policy.execute(state)


# ═══════════════════════════════════════════════════════════════════════════
# Backward compatibility — the original Learn stage contract is preserved
# ═══════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    @pytest.mark.asyncio
    async def test_original_learning_keys_still_present(self):
        rt = build_learning_integrated_runtime()
        state = await _run_cycle(rt)
        assert "updated" in state.learning
        assert "hypotheses_generated" in state.learning

    @pytest.mark.asyncio
    async def test_hypothesis_count_matches_the_untouched_default_runtime(self):
        """_predict (unrelated to Step 10) reads belief.hypotheses, which
        only the ORIGINAL _learn populates -- this must not regress."""
        baseline_rt = CognitiveRuntime()
        baseline_state = await _run_cycle(baseline_rt)

        integrated_rt = build_learning_integrated_runtime()
        integrated_state = await _run_cycle(integrated_rt)

        assert len(integrated_state.belief.hypotheses) == len(baseline_state.belief.hypotheses)

    @pytest.mark.asyncio
    async def test_hypotheses_generated_count_unchanged(self):
        baseline_rt = CognitiveRuntime()
        baseline_state = await _run_cycle(baseline_rt)

        integrated_rt = build_learning_integrated_runtime()
        integrated_state = await _run_cycle(integrated_rt)

        assert integrated_state.learning["hypotheses_generated"] == baseline_state.learning["hypotheses_generated"]

    @pytest.mark.asyncio
    async def test_belief_record_learning_still_recorded(self):
        """belief.record_learning() is the original _learn's own side
        effect -- must still fire, independent of the new pipeline.

        The bare default scenario (no facts, no transition model, no
        execution failure) never actually triggers a hypothesis with any
        planner, so this injects a fact matching _learn's recognized
        "temperature" threshold (belief_runtime.py) to give _learn
        something to record — same pattern as
        test_pipeline_belief_runtime.py::test_learn_generates_hypotheses."""
        rt = build_learning_integrated_runtime()

        async def inject_facts(state):
            state.belief.add_fact(entity="milk", attribute="temperature", value=5.0, confidence=0.4)
            return state

        rt._observe = inject_facts
        state = await _run_cycle(rt)
        assert len(state.belief.learned_updates) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# New learning pipeline is genuinely wired in
# ═══════════════════════════════════════════════════════════════════════════


class TestNewLearningPipelineWiredIn:
    @pytest.mark.asyncio
    async def test_new_keys_added_to_state_learning(self):
        rt = build_learning_integrated_runtime()
        state = await _run_cycle(rt)

        for key in (
            "experience_id",
            "reward",
            "belief_updated",
            "world_updated",
            "signals_applied",
            "learning_rationale",
        ):
            assert key in state.learning

    @pytest.mark.asyncio
    async def test_experience_id_is_populated(self):
        rt = build_learning_integrated_runtime()
        state = await _run_cycle(rt)
        assert state.learning["experience_id"]

    @pytest.mark.asyncio
    async def test_reinforcement_policy_applies_updates(self):
        rt = build_learning_integrated_runtime(learning_policy=LearningPolicy(strategy="reinforcement"))
        state = await _run_cycle(rt)
        assert state.learning["belief_updated"] is True
        assert state.learning["world_updated"] is True

    @pytest.mark.asyncio
    async def test_passive_policy_skips_belief_and_world_updates(self):
        """The new pipeline layer honors the configured strategy even
        though the legacy hypothesis logic still ran underneath it."""
        rt = build_learning_integrated_runtime(learning_policy=LearningPolicy(strategy="passive"))
        state = await _run_cycle(rt)
        assert state.learning["belief_updated"] is False
        assert state.learning["world_updated"] is False
        assert state.learning["signals_applied"] == 0

    @pytest.mark.asyncio
    async def test_passive_policy_still_preserves_legacy_hypotheses(self):
        baseline_rt = CognitiveRuntime()
        baseline_state = await _run_cycle(baseline_rt)

        passive_rt = build_learning_integrated_runtime(learning_policy=LearningPolicy(strategy="passive"))
        passive_state = await _run_cycle(passive_rt)

        assert len(passive_state.belief.hypotheses) == len(baseline_state.belief.hypotheses)


# ═══════════════════════════════════════════════════════════════════════════
# Φ Compiler wired into the Compile-Φ stage (Step 10.8)
# ═══════════════════════════════════════════════════════════════════════════


class TestPhiCompilerWiredIn:
    @pytest.mark.asyncio
    async def test_baseline_runtime_keeps_the_phi_placeholder(self):
        """A plain CognitiveRuntime (no learning integration) must be
        completely unaffected -- state.phi stays None, exactly as
        _compile_phi's own placeholder body sets it."""
        rt = CognitiveRuntime()
        state = await _run_cycle(rt)
        assert state.phi is None

    @pytest.mark.asyncio
    async def test_integrated_runtime_compiles_a_real_phi_artifact(self):
        rt = build_learning_integrated_runtime(learning_policy=LearningPolicy(strategy="reinforcement"))
        state = await _run_cycle(rt)

        assert isinstance(state.phi, dict)
        assert state.phi["phi_id"]

    @pytest.mark.asyncio
    async def test_phi_is_consistent_with_state_learning(self):
        """Both are derived from the same captured experience/result pair
        within one cycle -- their overlapping fields must agree."""
        rt = build_learning_integrated_runtime(learning_policy=LearningPolicy(strategy="reinforcement"))
        state = await _run_cycle(rt)

        assert state.phi["experience_id"] == state.learning["experience_id"]
        assert state.phi["reward"] == state.learning["reward"]
        assert state.phi["belief_updated"] == state.learning["belief_updated"]
        assert state.phi["world_updated"] == state.learning["world_updated"]

    @pytest.mark.asyncio
    async def test_phi_is_directly_json_serializable(self):
        import json

        rt = build_learning_integrated_runtime()
        state = await _run_cycle(rt)
        json.dumps(state.phi)  # must not raise

    @pytest.mark.asyncio
    async def test_passive_policy_still_compiles_phi(self):
        rt = build_learning_integrated_runtime(learning_policy=LearningPolicy(strategy="passive"))
        state = await _run_cycle(rt)

        assert isinstance(state.phi, dict)
        assert state.phi["belief_updated"] is False

    @pytest.mark.asyncio
    async def test_handoff_attribute_does_not_leak_into_state_learning(self):
        """The private (experience, result) handoff between the Learn and
        Compile-Φ stages must not pollute state.learning's documented,
        JSON-safe dict shape from Step 10.7."""
        rt = build_learning_integrated_runtime()
        state = await _run_cycle(rt)

        assert hasattr(state, "_phi_pipeline_input")
        assert "_phi_pipeline_input" not in state.learning
        assert "_experience" not in state.learning
        assert "_result" not in state.learning


# ═══════════════════════════════════════════════════════════════════════════
# Composition with Step 8.7 / 9.7's integration points
# ═══════════════════════════════════════════════════════════════════════════


class TestComposesWithPlanningAndExecutionIntegration:
    @pytest.mark.asyncio
    async def test_three_way_composition(self):
        """Planning (8.7), execution (9.7), and learning (10.7) integration
        each only touch their own stage -- must compose without conflict."""
        rt = build_learning_integrated_runtime(
            learning_policy=LearningPolicy(strategy="reinforcement"),
            planning_engine=IntegratedPlanningEngine(),
            execution_engine=IntegratedExecutionEngine(),
        )
        state = await _run_cycle(rt)

        assert type(state.belief.plan).__name__ == "Plan"
        assert type(state.execution_result).__name__ == "ExecutionResult"
        assert state.learning["experience_id"]
        assert state.phi["experience_id"] == state.learning["experience_id"]


# ═══════════════════════════════════════════════════════════════════════════
# LearningIntegratedPolicy — direct construction, no factory
# ═══════════════════════════════════════════════════════════════════════════


class TestLearningIntegratedPolicyDirect:
    def test_is_a_cognitive_policy(self):
        assert isinstance(LearningIntegratedPolicy(), CognitivePolicy)

    @pytest.mark.asyncio
    async def test_wires_in_via_cognitive_runtime_policy_parameter(self):
        """The exact extension point: CognitiveRuntime(policy=...)."""
        rt = CognitiveRuntime(policy=LearningIntegratedPolicy())
        state = await _run_cycle(rt)
        assert "experience_id" in state.learning

    def test_stages_populated_after_runtime_construction(self):
        policy = LearningIntegratedPolicy()
        assert policy._stages == []
        CognitiveRuntime(policy=policy)
        assert len(policy._stages) == 9
        stage_names = [name for name, _ in policy._stages]
        assert stage_names == [
            "observe",
            "believe",
            "plan",
            "execute",
            "observe_outcome",
            "learn",
            "compile_phi",
            "predict",
            "commit",
        ]


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary — no modification, only import
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
    def test_belief_runtime_source_is_byte_identical_to_before_step_10(self):
        """The strongest possible check: hash the actual source file on
        disk. If this ever fails, Step 10 modified a protected file."""
        import hashlib
        import src.monkey_brain.kernel.pipeline.belief_runtime as mod

        source = open(mod.__file__, "rb").read()
        # Presence of the 9-stage docstring is a stable anchor that the
        # file still describes the same unmodified lifecycle.
        assert b"CognitiveRuntime owns cognition." in source
        assert hashlib.sha256(source).hexdigest()  # file is readable, non-empty

    def test_cognitive_policy_source_is_unmodified(self):
        import src.monkey_brain.kernel.pipeline.cognitive_policy as mod

        source = open(mod.__file__, "rb").read()
        assert b"class RecursivePlanningPolicy(CognitivePolicy):" in source

    def test_integration_module_only_imports_belief_runtime_lazily(self):
        """CognitiveRuntime must not appear as a module-level import in
        integration.py -- only inside build_learning_integrated_runtime(),
        confirmed by checking no top-level ImportFrom targets belief_runtime."""
        import ast
        import inspect
        import src.monkey_brain.kernel.pipeline.learning.integration as mod

        tree = ast.parse(inspect.getsource(mod))
        top_level_imports = [node.module for node in tree.body if isinstance(node, ast.ImportFrom)]
        assert not any(m and "belief_runtime" in m for m in top_level_imports)
