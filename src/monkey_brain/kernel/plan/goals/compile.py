"""Shared intent compilation — classify + resolve goal + build signed IntentIR.

This is the SAME operation regardless of which runtime the result is destined
for (CognitiveRuntime/SimulationRuntime/ComparatorRuntime) — only `target`
differs, tagging which runtime's Run stage should consume the compiled
IntentIR and which one /replay/{run_id} must dispatch back to. Extracted out
of any single runtime so the three runtimes can share one compiler without
depending on each other (they must run completely independently).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.execute.orchestration.routing import create_goal
from src.monkey_brain.kernel.plan.goals.intent_ir import IntentIR, build_intent_ir
from src.monkey_brain.kernel.plan.goals.run_store import get_run_store

logger = logging.getLogger("agentos.compile_intent")


async def compile_intent(
    question: str,
    *,
    target: str = "execute",
    run_id: str | None = None,
    lemon: Any = None,
    store: bool = True,
) -> IntentIR | None:
    """Classify the question, resolve its goal, compile into a signed IntentIR.

    Returns None when nothing resolves (nothing to compile — like a source
    file that fails to compile). Never executes/simulates/compares anything.
    store=True (default) records the IntentIR in the shared run store tagged
    with `target`, so /replay dispatches back to the right runtime.
    """
    run_id = run_id or uuid4().hex
    normalized, intent, goal = create_goal(question, lemon=lemon, trace_id=run_id)
    if not intent or goal is None:
        logger.info(
            "run=%r compile_intent: no intent resolved for question (target=%s)",
            run_id,
            target,
        )
        return None

    ir = build_intent_ir(intent=intent, goal=goal, run_id=run_id, question=normalized)
    if store:
        get_run_store().store(run_id, ir, target=target)
    return ir
