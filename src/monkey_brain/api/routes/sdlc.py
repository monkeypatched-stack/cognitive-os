"""SDLC route — trigger, poll, and approve CodeGenRuntime's SDLC pipeline runs.

POST /api/v1/agentos/sdlc/run
  Compile a question into an IntentIR and start walking the SDLC
  ExecutionGraph (Specification..Release) via CodeGenRuntime/ProcessManager.

GET /api/v1/agentos/sdlc/{run_id}
  Poll the Runtime Process's current state — RUNNING/WAITING/COMPLETED/
  FAILED/etc. — and, if WAITING on a human-approval gate, the prompt.

POST /api/v1/agentos/sdlc/{run_id}/approve
  Grant or reject a pending approval gate (e.g. between Design and
  Implementation) so the pipeline can continue.

Distinct from /api/v1/codegen/microservice (routes/codegen.py) — that route
generates a single microservice from an already-compiled prompt; this one
orchestrates the full multi-stage SDLC pipeline (requirements through
deploy) as a Runtime Process, reusing the same ProcessManager every other
runtime's Runtime Processes share.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.monkey_brain.kernel.codegen_runtime import CodeGenRuntime
from src.monkey_brain.runtime.routers import get_mongo_client
from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.helpers.run_helpers import (
    get_codegen_runtime,
    get_process_manager,
)
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.sdlc")

router = APIRouter()


async def _tick_in_background(runtime: CodeGenRuntime, run_id: str) -> None:
    """Fire-and-forget continuation of a Runtime Process — the pipeline is
    many sequential LLM calls (one per SDLC stage) and can run well past ten
    minutes, so neither sdlc_run nor sdlc_approve block waiting for it
    anymore. Poll GET /sdlc/{run_id} for progress instead."""
    try:
        await runtime.tick_until_settled(run_id)
    except Exception as e:
        logger.error("[sdlc] background ticking failed for run_id=%r: %s", run_id, e)


class SdlcRunRequest(BaseModel):
    question: str
    compliance_domains: list[str] | None = None
    execution_mode: Literal["serial", "parallel"] = "serial"


class SdlcApproveRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = ""


@router.post("/sdlc/run")
@idempotent("sdlc.sdlc_run")
async def sdlc_run(
    payload: SdlcRunRequest,
    mongo_client: Any = Depends(get_mongo_client),
    user_id: str = Depends(require_permission("perm-execute-codegen")),
    runtime: CodeGenRuntime = Depends(get_codegen_runtime),
) -> Any:
    """Start a fresh SDLC pipeline run for `question` and return its run_id
    immediately, before any stage has executed. The pipeline itself (a real
    LLM call at every stage, run serially) keeps going in the background —
    poll GET /sdlc/{run_id} for progress, or use `sdlc discovery/requirements/
    architecture/.../release <run_id>` (CLI) to inspect one stage at a time.
    """
    logger.info("[sdlc] user=%r question=%r", user_id, payload.question[:100])
    t0 = time.monotonic()

    try:
        compliance_domains = tuple(payload.compliance_domains) if payload.compliance_domains else None
        ir = await runtime.compile_intent(payload.question, store=True)
        if ir is None:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "could not compile intent for SDLC pipeline",
                    "question": payload.question,
                },
            )
        run_id = await runtime.start_run(
            ir,
            mongo_client,
            user_id=user_id,
            compliance_domains=compliance_domains,
            execution_mode=payload.execution_mode,
        )
    except Exception as e:
        logger.error("[sdlc] run failed to start for user=%r: %s", user_id, e)
        return JSONResponse(
            status_code=500,
            content={
                "error": "SDLC run failed to start",
                "detail": str(e),
                "question": payload.question,
                "user_id": user_id,
            },
        )

    asyncio.create_task(_tick_in_background(runtime, run_id))

    elapsed_ms = (time.monotonic() - t0) * 1000
    return {
        "run_id": run_id,
        "state": "running",
        "user_id": user_id,
        "elapsed_ms": round(elapsed_ms, 2),
    }


@router.get("/sdlc/{run_id}")
async def sdlc_status(
    run_id: str,
    user_id: str = Depends(require_permission("perm-execute-codegen")),
    pm: Any = Depends(get_process_manager),
) -> Any:
    """Poll a Runtime Process's current state. 404 if run_id is unknown —
    the Process Table is process-local (not durable across restarts, same
    scope cut as run_store.RunStore) unless it was checkpointed.
    """
    rpcb = pm.get_process(run_id)
    if rpcb is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": f"no SDLC run found for run_id={run_id!r}",
                "user_id": user_id,
            },
        )

    response: dict[str, Any] = {
        "run_id": run_id,
        "state": rpcb.state.value,
        "graph_summary": rpcb.graph.get_execution_summary(),
        "nodes": [
            {
                "id": node.id,
                "type": node.type,
                "state": rpcb.graph.get_state(node.id).value,
                "result": rpcb.graph.get_result(node.id),
            }
            for node in rpcb.graph.all_nodes()
        ],
        "user_id": user_id,
    }
    if rpcb.approval_gate is not None:
        response["approval_gate"] = {
            "node_id": rpcb.approval_gate.node_id,
            "prompt": rpcb.approval_gate.prompt,
            "requested_at": rpcb.approval_gate.requested_at,
        }
    if rpcb.error is not None:
        response["error"] = rpcb.error.message
    return response


@router.post("/sdlc/{run_id}/approve")
@idempotent("sdlc.sdlc_approve")
async def sdlc_approve(
    run_id: str,
    payload: SdlcApproveRequest,
    user_id: str = Depends(require_permission("perm-execute-codegen")),
    runtime: CodeGenRuntime = Depends(get_codegen_runtime),
    pm: Any = Depends(get_process_manager),
) -> Any:
    """Grant or reject the run's pending approval gate. 409 if the run isn't
    actually WAITING on one — approving a gate that doesn't exist (already
    decided, or the pipeline never reached one) is a caller error, not
    something to silently accept.
    """
    rpcb = pm.get_process(run_id)
    if rpcb is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": f"no SDLC run found for run_id={run_id!r}",
                "user_id": user_id,
            },
        )
    if rpcb.approval_gate is None:
        return JSONResponse(
            status_code=409,
            content={
                "error": f"run_id={run_id!r} has no pending approval gate (state={rpcb.state.value})",
                "user_id": user_id,
            },
        )

    try:
        await pm.approve(run_id, approver=user_id, decision=payload.decision, note=payload.note)
    except Exception as e:
        logger.error("[sdlc] approve failed for run_id=%r: %s", run_id, e)
        return JSONResponse(
            status_code=500,
            content={"error": "approval failed", "detail": str(e), "user_id": user_id},
        )

    # Resume ticking in the background rather than blocking this request —
    # the remaining stages are the same serial LLM-call chain sdlc_run
    # doesn't block on either. Poll GET /sdlc/{run_id} for the outcome.
    asyncio.create_task(_tick_in_background(runtime, run_id))

    current = pm.get_process(run_id)
    return {
        "run_id": run_id,
        "state": current.state.value,
        "graph_summary": current.graph.get_execution_summary(),
        "user_id": user_id,
    }
