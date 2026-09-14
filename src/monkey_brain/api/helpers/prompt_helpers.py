"""prompt_helpers.py — Request execution helpers for the /prompt endpoint."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from src.monkey_brain.kernel.execute.runtime.executor import get_executor
from src.monkey_brain.kernel.execute.models import ExecutionMode
from src.monkey_brain.kernel.models import PromptRequest
from src.monkey_brain.kernel.models import SimulateResponse
from src.monkey_brain.kernel.society.integration import PropagationScope
from src.monkey_brain.api.helpers.healing_helpers import _MAX_HEALING_ATTEMPTS

logger = logging.getLogger("agentos.prompt")


async def run_query(question: str, mongo_client: Any) -> dict[str, Any] | None:
    """Execute a query by first planning, then executing.

    The proper flow is:
    1. /plan creates the workload (plan_steps)
    2. /execute runs the workload

    For /prompt, we need to call /plan internally first.
    However, since we don't have access to the Request object here,
    we use the executor directly which will handle the planning.
    """
    try:
        # Use the executor which handles both planning and execution
        result = await get_executor().execute(
            question,
            mongo_client,
            question_source=ExecutionMode.QUERY,
        )
        answer, semantic_hits, graph_paths, llm_answered = result
        return {
            "question": question,
            "answer": answer,
            "semantic_hits": semantic_hits,
            "graph_paths": graph_paths,
            "citations": [],
            "llm_answered": llm_answered,
        }
    except Exception as e:
        logger.error("query failed: %s", e)
        return {"error": str(e)}


async def run_simulate(
    question: str, context: dict | None, mongo_client: Any, intent: dict | None = None
) -> SimulateResponse | None:
    """Run simulation.

    Builds IntentIR from the intent and runs the simulation pipeline.
    """
    try:
        if not intent:
            logger.info("[prompt] no intent available for simulation")
            return None

        from uuid import uuid4
        from src.monkey_brain.kernel.plan.goals.intent_ir import build_intent_ir
        from src.monkey_brain.kernel.plan.goals.goal import Goal, GoalType, GoalSource

        # Build goal from intent
        goal = Goal(
            name=intent.get("intent", "world_graph_traversal"),
            description=question[:256],
            goal_type=GoalType.QUERY,
            source=GoalSource.API,
            confidence=float(intent.get("confidence", 0.5)),
        )

        # Build IntentIR
        run_id = intent.get("run_id", str(uuid4()))
        intent_ir = build_intent_ir(
            intent=intent,
            goal=goal,
            run_id=run_id,
            question=question,
        )

        # Run simulation
        from src.monkey_brain.kernel.simulation_runtime import get_simulation_runtime

        runtime = get_simulation_runtime()
        result = await runtime.run(intent_ir, mongo_client)

        return result
    except Exception as e:
        logger.error("simulation failed: %s", e)
        return None


def resolve_run_type(payload: PromptRequest) -> tuple[str, int]:
    """Resolve run_type and max_healing_attempts from payload, with meta taking priority."""
    meta = payload.meta or {}
    run_type = meta.get("run_type", payload.run_type) or "full"
    max_healing = int(meta.get("max_healing_attempts", _MAX_HEALING_ATTEMPTS))
    valid_types = {"full", "healing", "stability", "codegen"}
    if run_type not in valid_types:
        logger.warning("[prompt] Unknown run_type %r — defaulting to 'full'", run_type)
        run_type = "full"
    return run_type, max_healing


def validate_propagation_scope(payload: PromptRequest) -> tuple[str, str | None]:
    """Validate meta.propagation_scope (+ meta.propagation_target_actor_id)
    at the /prompt route boundary.

    PlanetaryRuntime._resolve_propagation_scope (kernel/society/
    integration.py) silently falls back to BROADCAST on anything
    unrecognized — the right behavior for its other, non-HTTP callers
    (events.py/orders.py call _propagate_coordination directly with no
    request object to reject). For the one HTTP-facing entry point,
    unlike run_type above, a caller that mistypes the scope or forgets
    the target actor should get an immediate 400 telling them exactly
    what's wrong, not a request that silently ran with different
    semantics than requested.
    """
    meta = payload.meta or {}
    raw_scope = meta.get("propagation_scope")
    if raw_scope is None:
        return PropagationScope.BROADCAST.value, None

    if not isinstance(raw_scope, str):
        raise HTTPException(
            status_code=400,
            detail=f"meta.propagation_scope must be a string, got {type(raw_scope).__name__}",
        )
    try:
        scope = PropagationScope(raw_scope.upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(f"meta.propagation_scope must be one of {[m.value for m in PropagationScope]}, got {raw_scope!r}"),
        )

    target_actor_id = meta.get("propagation_target_actor_id")
    if scope is PropagationScope.POINT_TO_POINT and not (isinstance(target_actor_id, str) and target_actor_id.strip()):
        raise HTTPException(
            status_code=400,
            detail=(
                "meta.propagation_target_actor_id is required (non-empty string) "
                'when meta.propagation_scope="POINT_TO_POINT"'
            ),
        )
    return scope.value, target_actor_id
