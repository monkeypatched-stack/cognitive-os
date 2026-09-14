"""Tests for PipelineOrchestrator and ResponseBuilder.

Validates orchestration flow, delegation, error handling, and statelessness.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.monkey_brain.kernel.pipeline.contracts import (
    PipelineRequest,
    CompiledRequest,
    RuntimeContext,
    PipelineResponse,
    PipelineStatus,
    PipelineTraceEntry,
)
from src.monkey_brain.kernel.pipeline.compiler import RequestCompiler, CompilationError
from src.monkey_brain.kernel.pipeline.orchestrator import PipelineOrchestrator
from src.monkey_brain.kernel.pipeline.response_builder import ResponseBuilder

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_request(question: str = "get 2 l of milk") -> PipelineRequest:
    return PipelineRequest(question=question)


def _make_compiled(request: PipelineRequest | None = None) -> CompiledRequest:
    request = request or _make_request()
    ctx = MagicMock()
    ctx.run_id = "test-run-001"
    return CompiledRequest(
        request=request,
        intent={"intent": "shopping", "confidence": 0.9},
        goal={"name": "acquire_item"},
        intent_ir=MagicMock(),
        goal_ir=MagicMock(),
        execution_context=ctx,
    )


def _mock_compiler() -> RequestCompiler:
    compiler = MagicMock(spec=RequestCompiler)
    compiler.compile = AsyncMock(return_value=_make_compiled())
    return compiler


def _mock_runtime(answer: str = "Got 2L of milk") -> Any:
    runtime = MagicMock()
    runtime.run = AsyncMock(return_value={"answer": answer, "status": "success"})
    return runtime


# ═══════════════════════════════════════════════════════════════════════════
# PipelineOrchestrator
# ═══════════════════════════════════════════════════════════════════════════


class TestOrchestratorConstruction:
    def test_creates_with_defaults(self):
        orch = PipelineOrchestrator()
        assert orch._compiler is not None
        assert orch._runtime is None

    def test_creates_with_injected_deps(self):
        compiler = _mock_compiler()
        runtime = _mock_runtime()
        orch = PipelineOrchestrator(compiler=compiler, runtime=runtime)
        assert orch._compiler is compiler
        assert orch._runtime is runtime


class TestOrchestratorRun:
    @pytest.mark.asyncio
    async def test_successful_orchestration(self):
        compiler = _mock_compiler()
        runtime = _mock_runtime(answer="Got milk")
        orch = PipelineOrchestrator(compiler=compiler, runtime=runtime)

        response = await orch.run(_make_request())

        assert isinstance(response, PipelineResponse)
        assert response.answer == "Got milk"
        assert response.status == PipelineStatus.SUCCESS
        assert len(response.trace) == 3  # compile + resolve_runtime + execute
        assert response.trace[0].step == "compile"
        assert response.trace[1].step == "resolve_runtime"
        assert response.trace[2].step == "execute"

    @pytest.mark.asyncio
    async def test_compiler_called_with_request(self):
        compiler = _mock_compiler()
        runtime = _mock_runtime()
        orch = PipelineOrchestrator(compiler=compiler, runtime=runtime)

        request = _make_request(question="test question")
        await orch.run(request)

        compiler.compile.assert_awaited_once_with(request)

    @pytest.mark.asyncio
    async def test_runtime_called_with_compiled_and_context(self):
        compiler = _mock_compiler()
        runtime = _mock_runtime()
        orch = PipelineOrchestrator(compiler=compiler, runtime=runtime)

        request = _make_request(question="hello")
        compiled = _make_compiled(request)
        compiler.compile = AsyncMock(return_value=compiled)

        await orch.run(request)

        # Runtime receives CompiledRequest + RuntimeContext, never raw question
        args, kwargs = runtime.run.call_args
        assert isinstance(args[0], CompiledRequest)
        assert args[0].request.question == "hello"
        assert isinstance(args[1], RuntimeContext)

    @pytest.mark.asyncio
    async def test_stateless_across_requests(self):
        compiler = _mock_compiler()
        runtime = _mock_runtime()
        orch = PipelineOrchestrator(compiler=compiler, runtime=runtime)

        r1 = await orch.run(_make_request("question 1"))
        r2 = await orch.run(_make_request("question 2"))

        assert r1.answer != r2.answer or r1 is not r2
        assert compiler.compile.await_count == 2
        assert runtime.run.await_count == 2


class TestOrchestratorErrors:
    @pytest.mark.asyncio
    async def test_compilation_failure(self):
        compiler = MagicMock(spec=RequestCompiler)
        compiler.compile = AsyncMock(side_effect=CompilationError(code="BAD_INTENT", message="cannot classify"))
        orch = PipelineOrchestrator(compiler=compiler, runtime=_mock_runtime())

        response = await orch.run(_make_request())

        assert response.status == PipelineStatus.FAILED
        assert len(response.errors) == 1
        assert response.errors[0].code == "BAD_INTENT"
        assert len(response.trace) == 1
        assert response.trace[0].status == "failed"

    @pytest.mark.asyncio
    async def test_runtime_failure(self):
        compiler = _mock_compiler()
        runtime = MagicMock()
        runtime.run = AsyncMock(side_effect=RuntimeError("runtime exploded"))
        orch = PipelineOrchestrator(compiler=compiler, runtime=runtime)

        response = await orch.run(_make_request())

        assert response.status == PipelineStatus.FAILED
        assert len(response.errors) == 1
        assert response.errors[0].code == "EXECUTION_FAILED"
        assert len(response.trace) == 3
        assert response.trace[0].status == "ok"  # compile succeeded
        assert response.trace[1].status == "ok"  # resolve_runtime
        assert response.trace[2].status == "failed"  # execute failed

    @pytest.mark.asyncio
    async def test_no_runtime_returns_empty(self):
        compiler = _mock_compiler()
        orch = PipelineOrchestrator(compiler=compiler, runtime=None)

        response = await orch.run(_make_request())

        assert response.status == PipelineStatus.SUCCESS
        assert response.answer == "" or response.answer == "{}"


class TestOrchestratorStatelessness:
    @pytest.mark.asyncio
    async def test_no_instance_state_after_run(self):
        compiler = _mock_compiler()
        runtime = _mock_runtime()
        orch = PipelineOrchestrator(compiler=compiler, runtime=runtime)

        await orch.run(_make_request())

        # Orchestrator should not accumulate state
        assert not hasattr(orch, "_last_request")
        assert not hasattr(orch, "_last_result")
        assert not hasattr(orch, "_cached_compiled")

    @pytest.mark.asyncio
    async def test_runtime_receives_compiled_not_question(self):
        """Runtime must never receive raw question strings."""
        compiler = _mock_compiler()
        runtime = _mock_runtime()
        orch = PipelineOrchestrator(compiler=compiler, runtime=runtime)

        await orch.run(_make_request())

        args, _ = runtime.run.call_args
        # First arg is CompiledRequest, not str
        assert not isinstance(args[0], str)
        assert isinstance(args[0], CompiledRequest)


# ═══════════════════════════════════════════════════════════════════════════
# ResponseBuilder
# ═══════════════════════════════════════════════════════════════════════════


class TestResponseBuilder:
    def test_build_from_dict_result(self):
        builder = ResponseBuilder()
        result = {"answer": "Got milk", "status": "success"}
        compiled = _make_compiled()

        response = builder.build(result, compiled, start_time=0.0)

        assert isinstance(response, PipelineResponse)
        assert response.answer == "Got milk"
        assert response.status == PipelineStatus.SUCCESS

    def test_build_from_none_result(self):
        builder = ResponseBuilder()
        compiled = _make_compiled()

        response = builder.build(None, compiled)

        assert response.status == PipelineStatus.FAILED

    def test_build_with_error_in_result(self):
        builder = ResponseBuilder()
        result = {"answer": "", "error": "something broke"}
        compiled = _make_compiled()

        response = builder.build(result, compiled)

        assert response.status == PipelineStatus.FAILED

    def test_build_extracts_execution_id(self):
        builder = ResponseBuilder()
        compiled = _make_compiled()

        response = builder.build({"answer": "ok"}, compiled)

        assert response.execution_id == "test-run-001"

    def test_build_with_trace(self):
        builder = ResponseBuilder()
        trace = (PipelineTraceEntry(step="planning", duration_ms=50.0),)

        response = builder.build({"answer": "ok"}, _make_compiled(), trace=trace)

        assert len(response.trace) == 1
        assert response.trace[0].step == "planning"

    def test_build_metrics_include_total_ms(self):
        builder = ResponseBuilder()

        response = builder.build({"answer": "ok"}, _make_compiled(), start_time=0.0)

        assert "total_ms" in response.metrics
        assert response.metrics["total_ms"] >= 0


# ═══════════════════════════════════════════════════════════════════════════
# No Runtime Dependencies
# ═══════════════════════════════════════════════════════════════════════════


class TestNoRuntimeDependencies:
    def test_orchestrator_has_no_runtime_imports(self):
        """orchestrator.py must not import any runtime implementation."""
        import inspect
        import src.monkey_brain.kernel.pipeline.orchestrator as mod

        source = inspect.getsource(mod)
        forbidden = [
            "from src.monkey_brain.kernel.compile.society",
            "from src.monkey_brain.kernel.compile.belief_runtime",
            "from src.monkey_brain.kernel.graph_manager",
            "from src.monkey_brain.kernel.simulation_runtime",
            "from src.monkey_brain.kernel.cognitive_runtime",
        ]
        for imp in forbidden:
            assert imp not in source, f"orchestrator.py must not import: {imp}"

    def test_response_builder_has_no_runtime_imports(self):
        """response_builder.py must not import any runtime implementation."""
        import inspect
        import src.monkey_brain.kernel.pipeline.response_builder as mod

        source = inspect.getsource(mod)
        forbidden = [
            "from src.monkey_brain.kernel.compile.",
            "from src.monkey_brain.kernel.execute.",
            "from src.monkey_brain.kernel.graph_manager",
        ]
        for imp in forbidden:
            assert imp not in source, f"response_builder.py must not import: {imp}"
