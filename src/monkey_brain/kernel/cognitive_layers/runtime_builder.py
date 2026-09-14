"""Layer 2: Runtime Building - Single Responsibility.

Responsibility: Build ExecutionContext from IntentIR.
Depends on: ExecutionContext, intent IR
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.execute.context import ExecutionContext
from src.monkey_brain.kernel.execute.models import ExecutionMode
from src.monkey_brain.kernel.compile.solid_interfaces import ExecutorInterface

if TYPE_CHECKING:
    from src.monkey_brain.kernel.plan.goals.intent_ir import IntentIR

logger = logging.getLogger("agentos.cognitive.layers.runtime_builder")


class RuntimeBuilder(ExecutorInterface):
    """Build ExecutionContext from IntentIR.

    Responsibility: Prepare execution environment (immutable process context).
    Input: IntentIR, execution mode, metadata
    Output: ExecutionContext (ready for execution)

    Note: Does NOT execute - only builds the context.
    """

    async def execute(self, work: Any) -> Any:
        """Implement ExecutorInterface - execute work."""
        if isinstance(work, dict) and "intent_ir" in work:
            return self.build_from_dict(work)
        raise TypeError(f"Unsupported work type: {type(work)}")

    def build(
        self,
        intent_ir: IntentIR,
        execution_mode: ExecutionMode,
        *,
        user_id: str = "",
        trace_id: str = "",
        request_metadata: dict[str, Any] | None = None,
    ) -> ExecutionContext:
        """Build ExecutionContext from IntentIR.

        Args:
            intent_ir: Compiled IntentIR
            execution_mode: How to execute (PLAN, EXECUTE, REPLAY, etc.)
            user_id: User identifier
            trace_id: Trace identifier (defaults to run_id)
            request_metadata: Additional metadata

        Returns:
            ExecutionContext (immutable, ready for execution)
        """
        context = ExecutionContext.create(
            run_id=intent_ir.run_id,
            execution_mode=execution_mode,
            intent_ir=intent_ir,
            user_id=user_id,
            trace_id=trace_id or intent_ir.run_id,
            request_metadata=request_metadata,
        )

        logger.info(
            "[builder] Built execution context: run_id=%s, mode=%s, user=%s",
            intent_ir.run_id,
            (execution_mode.name if hasattr(execution_mode, "name") else str(execution_mode)),
            user_id or "unknown",
        )

        return context

    def build_from_dict(self, source: dict) -> ExecutionContext:
        """Build from dict (supports ExecutorInterface)."""
        return self.build(
            intent_ir=source.get("intent_ir"),
            execution_mode=source.get("execution_mode", ExecutionMode.EXECUTE),
            user_id=source.get("user_id", ""),
            trace_id=source.get("trace_id", ""),
            request_metadata=source.get("request_metadata"),
        )
