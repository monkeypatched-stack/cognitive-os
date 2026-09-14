"""Pipeline orchestrator — bridges compilation and execution.

The orchestrator calls exactly four things:

    1. RequestCompiler.compile()
    2. RuntimeContext resolution (actor, world, belief)
    3. Runtime delegation (belief.run or equivalent)
    4. ResponseBuilder.build()

Nothing else. No planner. No graph traversal. No execution.
No learning. No world initialization. No actor initialization.

This constraint keeps the orchestrator permanently thin.

The runtime receives only semantic objects (CompiledRequest, RuntimeContext).
Never transport data (question strings, HTTP requests, JSON).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.monkey_brain.kernel.pipeline.contracts import (
    PipelineRequest,
    PipelineResponse,
    RuntimeContext,
    PipelineStatus,
    PipelineError,
    PipelineTraceEntry,
)
from src.monkey_brain.kernel.pipeline.compiler import RequestCompiler, CompilationError
from src.monkey_brain.kernel.pipeline.actor import Actor
from src.monkey_brain.kernel.pipeline.response_builder import ResponseBuilder

logger = logging.getLogger("agentos.pipeline.orchestrator")


class _NoRuntime:
    """Sentinel: no runtime was available for execution."""


class PipelineOrchestrator:
    """Entry point for all request execution.

    Calls exactly four things:
        1. RequestCompiler.compile()
        2. RuntimeContext resolution
        3. Runtime delegation
        4. ResponseBuilder.build()

    The runtime receives CompiledRequest + RuntimeContext.
    Never transport-level data.

    Stateless — no instance state beyond injected dependencies.
    """

    def __init__(
        self,
        *,
        compiler: RequestCompiler | None = None,
        runtime: Any = None,
    ) -> None:
        self._compiler = compiler or RequestCompiler()
        self._runtime = runtime
        self._response_builder = ResponseBuilder()

    async def run(self, request: PipelineRequest) -> PipelineResponse:
        """Execute a pipeline request end-to-end.

        Orchestrates exactly 4 calls. Nothing else.
        """
        start = time.time()
        trace: list[PipelineTraceEntry] = []

        # 1. Compile
        compiled, compile_ms = await self._compile(request)
        trace.append(
            PipelineTraceEntry(
                step="compile",
                status="ok" if compiled is not None else "failed",
                duration_ms=compile_ms,
            )
        )
        if compiled is None:
            return self._error_response(request, trace, start, compile_ms)

        # 2. Resolve runtime context and actor
        runtime_ctx, actor = self._resolve_runtime(compiled)
        trace.append(PipelineTraceEntry(step="resolve_runtime", status="ok"))

        # 3. Delegate to runtime (compiled + context + actor, never raw question)
        exec_result, exec_ms = await self._execute(compiled, runtime_ctx, actor)
        trace.append(
            PipelineTraceEntry(
                step="execute",
                status="ok" if exec_result is not None else "failed",
                duration_ms=exec_ms,
            )
        )

        # 4. Build response
        return self._build_response(compiled, exec_result, trace, start)

    # ── 1. Compile ────────────────────────────────────────────────────────

    async def _compile(self, request: PipelineRequest) -> tuple[Any, Any]:
        """Compile request. Returns (compiled, ms) or (None, error_code)."""
        start = time.time()
        try:
            compiled = await self._compiler.compile(request)
            return compiled, (time.time() - start) * 1000
        except CompilationError as e:
            return None, e.code
        except Exception as e:
            logger.error("[orchestrator] compile failed: %s", e, exc_info=True)
            return None, "COMPILE_FAILED"

    # ── 2. Resolve runtime context ────────────────────────────────────────

    def _resolve_runtime(self, compiled: Any) -> tuple[RuntimeContext | None, Actor | None]:
        """Resolve runtime context and actor from the booted runtime.

        Returns (RuntimeContext, Actor) or (None, None) if no runtime.
        """
        if self._runtime is None:
            return None, None

        runtime_ctx = RuntimeContext(
            world=getattr(self._runtime, "_world", None),
            actor=getattr(self._runtime, "_actor_network", None),
            capabilities=getattr(self._runtime, "domain_registry", None),
            knowledge=getattr(self._runtime, "semantic_memory", None),
            context_stream=getattr(self._runtime, "_context_stream", None),
            execution=getattr(self._runtime, "_goal_executor", None),
            learning=getattr(self._runtime, "graph_manager", None),
            metrics=getattr(self._runtime, "lemon", None),
            belief=getattr(self._runtime, "_belief", None),
            trust=getattr(self._runtime, "_trust", None),
            audit=getattr(self._runtime, "_audit_service", None),
            event_bus=getattr(self._runtime, "_event_bus", None),
        )

        # Resolve actor from runtime or create from compiled request
        actor = getattr(self._runtime, "_actor", None)
        if actor is None:
            actor = Actor(
                actor_id=compiled.request.actor_id,
                tenant_id=compiled.request.tenant_id,
            )

        return runtime_ctx, actor

    # ── 3. Execute ────────────────────────────────────────────────────────

    async def _execute(
        self,
        compiled: Any,
        runtime_ctx: RuntimeContext | None,
        actor: Actor | None = None,
    ) -> tuple[Any, float]:
        """Delegate execution to the runtime.

        Passes CompiledRequest + RuntimeContext + Actor.
        Never passes raw question strings.
        """
        start = time.time()
        if runtime_ctx is None:
            return _NoRuntime(), (time.time() - start) * 1000
        try:
            result = await self._runtime.run(compiled, runtime_ctx, actor)
            return result, (time.time() - start) * 1000
        except Exception as e:
            logger.error("[orchestrator] execute failed: %s", e, exc_info=True)
            return None, (time.time() - start) * 1000

    # ── 4. Build response ─────────────────────────────────────────────────

    def _build_response(
        self,
        compiled: Any,
        exec_result: Any,
        trace: list[PipelineTraceEntry],
        start: float,
    ) -> PipelineResponse:
        """Build the final PipelineResponse."""
        total_ms = (time.time() - start) * 1000

        # No runtime available — empty success
        if isinstance(exec_result, _NoRuntime):
            return PipelineResponse(
                answer="",
                status=PipelineStatus.SUCCESS,
                execution_id=self._exec_id(compiled),
                trace=tuple(trace),
                metrics={"total_ms": round(total_ms, 2)},
            )

        if exec_result is None:
            return PipelineResponse(
                answer="",
                status=PipelineStatus.FAILED,
                execution_id=self._exec_id(compiled),
                trace=tuple(trace),
                metrics={"total_ms": round(total_ms, 2)},
                errors=(
                    PipelineError(
                        code="EXECUTION_FAILED",
                        message="Runtime failed",
                        step="execute",
                    ),
                ),
            )

        return self._response_builder.build(
            exec_result,
            compiled,
            trace=tuple(trace),
            start_time=start,
        )

    def _exec_id(self, compiled: Any) -> str:
        ctx = getattr(compiled, "execution_context", None)
        return getattr(ctx, "run_id", "") if ctx else ""

    def _error_response(
        self,
        request: PipelineRequest,
        trace: list[PipelineTraceEntry],
        start: float,
        code: Any,
    ) -> PipelineResponse:
        total_ms = (time.time() - start) * 1000
        return PipelineResponse(
            answer="",
            status=PipelineStatus.FAILED,
            trace=tuple(trace),
            metrics={"total_ms": round(total_ms, 2)},
            errors=(PipelineError(code=str(code), message=str(code), step="compile"),),
        )
