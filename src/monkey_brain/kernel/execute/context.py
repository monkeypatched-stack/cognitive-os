"""ExecutionContext — the single, immutable source of runtime state.

Replaces loose parameter lists (goal, run_id, trace_id, mode, intent, ...)
threaded individually through the runtime. Every runtime component that
needs execution state receives one ExecutionContext instead of reassembling
it from scratch, which is what caused goal re-derivation drift in the first
place (see IntentIR / GoalExecutor.execute).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from src.monkey_brain.kernel.execute.models import ExecutionMode
from src.monkey_brain.kernel.plan.goals.intent_ir import IntentIR


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str
    trace_id: str
    execution_mode: ExecutionMode
    intent_ir: IntentIR | None
    user_id: str
    request_metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    created_at: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        execution_mode: ExecutionMode,
        intent_ir: IntentIR | None = None,
        user_id: str = "",
        trace_id: str = "",
        request_metadata: Mapping[str, Any] | None = None,
    ) -> "ExecutionContext":
        return cls(
            run_id=run_id,
            trace_id=trace_id or run_id,
            execution_mode=execution_mode,
            intent_ir=intent_ir,
            user_id=user_id,
            request_metadata=MappingProxyType(dict(request_metadata or {})),
        )

    def log_extra(self) -> dict[str, Any]:
        """Structured fields every execution log line should carry.

        Callers should use this instead of passing run_id/trace_id/mode
        manually into each log call.
        """
        ir = self.intent_ir
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "execution_mode": getattr(self.execution_mode, "value", self.execution_mode),
            "goal_id": (ir.goal.get("goal_id", "") if ir is not None else ""),
            "intent_id": (ir.intent_ir_id if ir is not None else ""),
            "schema_version": (ir.schema_version if ir is not None else ""),
        }
