"""Layer 1: Intent Compilation - Single Responsibility.

Responsibility: Compile intent and goal into IntentIR.
Depends on: intent IR builder, run store, routing
"""

from __future__ import annotations

import logging
from uuid import uuid4
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.execute.orchestration.routing import (
    normalize_question,
    resolve_goal_from_intent,
)
from src.monkey_brain.kernel.plan.goals.intent_ir import IntentIR, build_intent_ir
from src.monkey_brain.kernel.plan.goals.run_store import get_run_store
from src.monkey_brain.kernel.compile.solid_interfaces import CompilerInterface

if TYPE_CHECKING:
    pass

logger = logging.getLogger("agentos.cognitive.layers.intent_compiler")


class _EventPublishMixin:
    """Mixin for event publishing — injected by the facade."""

    _event_bus: Any = None

    async def _publish(self, event_type: str, payload: dict) -> None:
        if self._event_bus is not None:
            await self._event_bus.publish(event_type, payload)


class IntentCompiler(CompilerInterface, _EventPublishMixin):
    """Compile intent and goal into signed IntentIR.

    Responsibility: Pure compilation (no side effects except storage).
    Input: intent dict, goal, question, run_id
    Output: IntentIR (signed and versioned)
    """

    def __init__(self) -> None:
        self._event_bus: Any = None

    async def compile_intent(
        self,
        question: str,
        *,
        run_id: str | None = None,
        lemon: Any = None,
        store: bool = True,
    ) -> IntentIR | None:
        """Compile Intent: classify the question, resolve its goal, compile
        both into a signed, versioned IntentIR.

        Returns None when the question doesn't resolve to any intent.
        """
        from src.monkey_brain.kernel.plan.goals.compile import (
            compile_intent as _compile,
        )

        ir = await _compile(question, target="execute", run_id=run_id, lemon=lemon, store=store)
        await self._publish(
            "intent.compiled" if ir is not None else "intent.compile_failed",
            {
                "run_id": run_id,
                "target": "execute",
                "intent_type": ir.intent_type if ir else None,
            },
        )
        return ir

    def compile(self, source: Any) -> Any:
        """Implement CompilerInterface - compile source to IntentIR."""
        if isinstance(source, dict):
            return self.compile_from_dict(source)
        raise TypeError(f"Unsupported source type: {type(source)}")

    def compile_from_resolved(
        self,
        *,
        intent: dict,
        goal: Any,
        question: str,
        run_id: str | None = None,
        store: bool = True,
    ) -> IntentIR:
        """Compile intent and goal into IntentIR.

        Args:
            intent: Classified intent dict (already resolved)
            goal: Goal object or None (will resolve if None)
            question: User question (natural language)
            run_id: Execution run ID (generated if None)
            store: Whether to store in run store

        Returns:
            IntentIR (signed and versioned)
        """
        run_id = run_id or uuid4().hex
        normalized = normalize_question(question)

        # Resolve goal if not provided
        if goal is None:
            goal = resolve_goal_from_intent(normalized, intent)

        # Build intent IR
        ir = build_intent_ir(
            intent=intent,
            goal=goal,
            run_id=run_id,
            question=normalized,
        )

        # Store for later retrieval
        if store:
            get_run_store().store(run_id, ir, target="execute")

        logger.info(
            "[compiler] Compiled intent: run_id=%s, intent_type=%s, goal=%s",
            run_id,
            intent.get("intent", "unknown"),
            goal.name if hasattr(goal, "name") else str(goal),
        )

        return ir

    def compile_from_dict(self, source: dict) -> IntentIR:
        """Compile from dict (supports CompilerInterface)."""
        return self.compile_from_resolved(
            intent=source.get("intent"),
            goal=source.get("goal"),
            question=source.get("question"),
            run_id=source.get("run_id"),
            store=source.get("store", True),
        )
