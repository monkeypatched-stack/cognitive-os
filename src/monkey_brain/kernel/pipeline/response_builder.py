"""Pipeline response builder — transforms execution output into PipelineResponse.

Builds a PipelineResponse from execution results and compilation context.
Pure formatting — no execution, no side effects.

Implementation: Step 4 will extend this for richer artifact extraction.
"""

from __future__ import annotations

import time
from typing import Any

from src.monkey_brain.kernel.pipeline.contracts import (
    PipelineResponse,
    PipelineStatus,
    PipelineTraceEntry,
)


class ResponseBuilder:
    """Builds a PipelineResponse from execution output.

    Responsible for:
    - Formatting the answer for the caller
    - Extracting metrics from execution
    - Building the execution trace
    - Mapping errors to PipelineError objects

    Pure formatting — no execution, no world mutation.
    """

    def build(
        self,
        execution_result: Any,
        compiled: Any,
        trace: tuple[PipelineTraceEntry, ...] = (),
        start_time: float | None = None,
    ) -> PipelineResponse:
        """Build a PipelineResponse from execution output.

        Args:
            execution_result: Result from runtime execution (dict or object)
            compiled: CompiledRequest for execution_id and metadata
            trace: Execution trace entries
            start_time: Pipeline start time for total_ms calculation

        Returns:
            PipelineResponse ready for the caller
        """
        total_ms = (time.time() - start_time) * 1000 if start_time else 0.0

        answer = self._extract_answer(execution_result)
        status = self._determine_status(execution_result)
        execution_id = self._extract_execution_id(compiled)

        return PipelineResponse(
            answer=answer,
            status=status,
            execution_id=execution_id,
            trace=trace,
            metrics={
                "total_ms": round(total_ms, 2),
            },
        )

    def _extract_answer(self, result: Any) -> str:
        """Extract the textual answer from an execution result."""
        if result is None:
            return ""
        if isinstance(result, dict):
            return result.get("answer", str(result))
        if hasattr(result, "answer"):
            return result.answer
        return str(result)

    def _determine_status(self, result: Any) -> str:
        """Determine pipeline status from execution result."""
        if result is None:
            return PipelineStatus.FAILED
        if isinstance(result, dict):
            if result.get("error"):
                return PipelineStatus.FAILED
            if result.get("status") == "partial":
                return PipelineStatus.PARTIAL
        return PipelineStatus.SUCCESS

    def _extract_execution_id(self, compiled: Any) -> str:
        """Extract the execution ID from the compiled request."""
        if compiled is None:
            return ""
        if hasattr(compiled, "execution_context"):
            ctx = compiled.execution_context
            if hasattr(ctx, "run_id"):
                return ctx.run_id
        return ""
