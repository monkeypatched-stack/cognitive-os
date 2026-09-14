"""Tests for RequestCompiler.

Validates the compilation pipeline: PipelineRequest → CompiledRequest.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.monkey_brain.kernel.pipeline.contracts import (
    PipelineRequest,
    CompiledRequest,
    PipelineStatus,
)
from src.monkey_brain.kernel.pipeline.compiler import (
    RequestCompiler,
    CompilationError,
)

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_request(question: str = "get 2 l of milk", **kwargs) -> PipelineRequest:
    return PipelineRequest(question=question, **kwargs)


def _mock_intent():
    return {"intent": "shopping", "confidence": 0.9, "workload_id": "shopping"}


def _mock_goal():
    class Goal:
        name = "acquire_item"
        description = "Get 2L of milk"
        required_inputs = ["item", "quantity"]
        required_outputs = ["acquired"]
        constraints = {}
        confidence = 1.0

    return Goal()


def _mock_intent_ir():
    class IntentIR:
        intent_ir_id = "ir-test-001"
        run_id = "req-test-001"
        intent_type = "shopping"
        confidence = 0.9
        goal = {"name": "acquire_item"}
        entities = ("milk",)
        parameters = {"quantity": "2L"}
        execution_metadata = {}
        created_at = 1234567890.0
        signature = "test-sig"

    return IntentIR()


# ═══════════════════════════════════════════════════════════════════════════
# Construction
# ═══════════════════════════════════════════════════════════════════════════


class TestRequestCompilerConstruction:
    def test_creates_with_default_actor_id(self):
        compiler = RequestCompiler()
        assert compiler._actor_id == "compiler"

    def test_creates_with_custom_actor_id(self):
        compiler = RequestCompiler(actor_id="test-actor")
        assert compiler._actor_id == "test-actor"


# ═══════════════════════════════════════════════════════════════════════════
# Successful Compilation
# ═══════════════════════════════════════════════════════════════════════════


class TestCompilation:
    @pytest.mark.asyncio
    async def test_successful_compilation(self):
        request = _make_request()

        with (
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._resolve_intent") as mock_intent,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._resolve_goal") as mock_goal,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._compile_intent_ir") as mock_ir,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._compile_goal_ir") as mock_gir,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._compile_execution_context") as mock_ctx,
        ):
            mock_intent.return_value = _mock_intent()
            mock_goal.return_value = _mock_goal()
            mock_ir.return_value = _mock_intent_ir()
            mock_gir.return_value = MagicMock()
            mock_ctx.return_value = MagicMock()

            compiler = RequestCompiler()
            result = await compiler.compile(request)

            assert isinstance(result, CompiledRequest)
            assert result.request is request
            assert result.intent == _mock_intent()
            assert result.execution_graph is None  # no belief provided
            assert result.metadata["has_graph"] is False

    @pytest.mark.asyncio
    async def test_deterministic_output(self):
        """Same request → same compiled output (except timestamps)."""
        request = _make_request(question="test question")

        with (
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._resolve_intent") as mock_intent,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._resolve_goal") as mock_goal,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._compile_intent_ir") as mock_ir,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._compile_goal_ir") as mock_gir,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._compile_execution_context") as mock_ctx,
        ):
            mock_intent.return_value = _mock_intent()
            mock_goal.return_value = _mock_goal()
            mock_ir.return_value = _mock_intent_ir()
            mock_gir.return_value = MagicMock()
            mock_ctx.return_value = MagicMock()

            compiler = RequestCompiler()
            r1 = await compiler.compile(request)
            r2 = await compiler.compile(request)

            assert r1.intent == r2.intent
            assert r1.goal == r2.goal
            assert r1.intent_ir == r2.intent_ir
            assert r1.request is r2.request  # same object, not copied


# ═══════════════════════════════════════════════════════════════════════════
# Error Handling
# ═══════════════════════════════════════════════════════════════════════════


class TestCompilationErrors:
    @pytest.mark.asyncio
    async def test_intent_classification_failure(self):
        request = _make_request(question="gibberish xyzzy")

        with (
            patch(
                "src.monkey_brain.kernel.execute.orchestration.routing.resolve_intent",
                return_value=None,
            ),
            patch(
                "src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._try_auto_register",
                return_value=None,
            ),
        ):
            compiler = RequestCompiler()
            with pytest.raises(CompilationError) as exc_info:
                await compiler.compile(request)
            assert exc_info.value.code == "INTENT_CLASSIFICATION_FAILED"

    @pytest.mark.asyncio
    async def test_goal_resolution_failure(self):
        request = _make_request()

        with (
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._resolve_intent") as mock_intent,
            patch(
                "src.monkey_brain.kernel.execute.orchestration.routing.resolve_goal_from_intent",
                side_effect=RuntimeError("goal broken"),
            ),
        ):
            mock_intent.return_value = _mock_intent()

            compiler = RequestCompiler()
            with pytest.raises(CompilationError) as exc_info:
                await compiler.compile(request)
            assert exc_info.value.code == "GOAL_RESOLUTION_FAILED"

    @pytest.mark.asyncio
    async def test_unseen_domain_failure(self):
        request = _make_request(question="quantum flux capacitor analysis")

        with (
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._resolve_intent") as mock_intent,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._resolve_goal") as mock_goal,
            patch(
                "src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._explore_unseen_domain",
                return_value=None,  # no domain found
            ),
        ):
            mock_intent.return_value = {
                "intent": "unknown",
                "confidence": 0.3,
                "workload_id": "none",
            }
            mock_goal.return_value = _mock_goal()

            compiler = RequestCompiler()
            with pytest.raises(CompilationError) as exc_info:
                await compiler.compile(request)
            assert exc_info.value.step == "compile_goal_ir"


# ═══════════════════════════════════════════════════════════════════════════
# CompiledRequest Contents
# ═══════════════════════════════════════════════════════════════════════════


class TestCompiledRequestContents:
    @pytest.mark.asyncio
    async def test_compiled_request_has_all_fields(self):
        request = _make_request()

        with (
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._resolve_intent") as mock_intent,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._resolve_goal") as mock_goal,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._compile_intent_ir") as mock_ir,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._compile_goal_ir") as mock_gir,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._compile_execution_context") as mock_ctx,
        ):
            mock_intent.return_value = _mock_intent()
            mock_goal.return_value = _mock_goal()
            mock_ir.return_value = _mock_intent_ir()
            mock_gir.return_value = MagicMock(domain="shopping")
            mock_ctx.return_value = MagicMock(run_id="ctx-1")

            compiler = RequestCompiler()
            result = await compiler.compile(request)

            assert result.request is request
            assert result.intent is not None
            assert result.goal is not None
            assert result.intent_ir is not None
            assert result.goal_ir is not None
            assert result.execution_context is not None
            assert result.execution_graph is None
            assert "compilation_ms" in result.metadata

    @pytest.mark.asyncio
    async def test_compiled_request_immutable(self):
        request = _make_request()

        with (
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._resolve_intent") as mock_intent,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._resolve_goal") as mock_goal,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._compile_intent_ir") as mock_ir,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._compile_goal_ir") as mock_gir,
            patch("src.monkey_brain.kernel.pipeline.compiler.RequestCompiler._compile_execution_context") as mock_ctx,
        ):
            mock_intent.return_value = _mock_intent()
            mock_goal.return_value = _mock_goal()
            mock_ir.return_value = _mock_intent_ir()
            mock_gir.return_value = MagicMock()
            mock_ctx.return_value = MagicMock()

            compiler = RequestCompiler()
            result = await compiler.compile(request)

            from dataclasses import FrozenInstanceError

            with pytest.raises(FrozenInstanceError):
                result.request = _make_request(question="hacked")


# ═══════════════════════════════════════════════════════════════════════════
# No Runtime Dependencies
# ═══════════════════════════════════════════════════════════════════════════


class TestNoRuntimeDependencies:
    def test_compiler_has_no_runtime_imports(self):
        """compiler.py must not import any runtime implementation."""
        import inspect
        import src.monkey_brain.kernel.pipeline.compiler as mod

        source = inspect.getsource(mod)
        forbidden = [
            "from src.monkey_brain.kernel.compile.society",
            "from src.monkey_brain.kernel.compile.belief_runtime",
            "from src.monkey_brain.kernel.graph_manager",
            "from src.monkey_brain.kernel.simulation_runtime",
            "from src.monkey_brain.kernel.comparator_runtime",
            "from src.monkey_brain.kernel.cognitive_runtime",
        ]
        for imp in forbidden:
            assert imp not in source, f"compiler.py must not import: {imp}"


# ═══════════════════════════════════════════════════════════════════════════
# CompilationError
# ═══════════════════════════════════════════════════════════════════════════


class TestCompilationError:
    def test_error_construction(self):
        err = CompilationError(
            code="E001",
            message="something failed",
            step="resolve_intent",
            details={"question": "test"},
        )
        assert err.code == "E001"
        assert err.step == "resolve_intent"
        assert str(err) == "something failed"

    def test_error_is_exception(self):
        """CompilationError is an Exception — can be raised and caught."""
        err = CompilationError(code="E001", message="fail")
        assert isinstance(err, Exception)
        with pytest.raises(CompilationError):
            raise err
