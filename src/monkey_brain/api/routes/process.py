"""Process Management routes — checkpoint, restore, suspend, resume, terminate.

Exposes the ProcessManager's lifecycle primitives as REST endpoints.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.process_api")
router = APIRouter()


def _get_process_manager(request: Request) -> Any:
    return getattr(request.app.state, "process_manager", None)


class ProcessStateResponse(BaseModel):
    run_id: str
    state: str
    checkpoints: list[str] = Field(default_factory=list)
    created_at: float = 0.0


class ProcessListResponse(BaseModel):
    processes: list[ProcessStateResponse]
    total: int = 0


class CheckpointResponse(BaseModel):
    checkpoint_id: str
    run_id: str
    status: str = "ok"


class RestoreResponse(BaseModel):
    run_id: str
    checkpoint_id: str
    state: str
    status: str = "ok"


class ActionResult(BaseModel):
    run_id: str
    status: str
    detail: str = ""


@router.get("/processes", response_model=ProcessListResponse)
async def list_processes(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-agents")),
):
    """List all active runtime processes."""
    pm = _get_process_manager(request)
    if pm is None:
        return ProcessListResponse(processes=[], total=0)

    processes = []
    for run_id, rpcb in pm._table.items():
        processes.append(
            ProcessStateResponse(
                run_id=run_id,
                state=rpcb.state.value,
                checkpoints=[c.checkpoint_id for c in rpcb.checkpoints],
                created_at=rpcb.created_at,
            )
        )
    return ProcessListResponse(processes=processes, total=len(processes))


@router.get("/processes/{run_id}", response_model=ProcessStateResponse)
async def get_process_state(
    run_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-agents")),
):
    """Get the current state of a runtime process."""
    pm = _get_process_manager(request)
    if pm is None:
        raise HTTPException(status_code=503, detail="ProcessManager not available")

    rpcb = pm._table.get(run_id)
    if rpcb is None:
        raise HTTPException(status_code=404, detail=f"Process {run_id} not found")

    return ProcessStateResponse(
        run_id=run_id,
        state=rpcb.state.value,
        checkpoints=[c.checkpoint_id for c in rpcb.checkpoints],
        created_at=rpcb.created_at,
    )


@router.post("/processes/{run_id}/checkpoint", response_model=CheckpointResponse)
@idempotent("process.checkpoint_process")
async def checkpoint_process(
    run_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-action")),
):
    """Checkpoint a suspended process. Requires SUSPENDED state."""
    pm = _get_process_manager(request)
    if pm is None:
        raise HTTPException(status_code=503, detail="ProcessManager not available")

    try:
        ref = await pm.checkpoint_process(run_id)
        return CheckpointResponse(
            checkpoint_id=ref.checkpoint_id,
            run_id=run_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/processes/restore/{checkpoint_id}", response_model=RestoreResponse)
@idempotent("process.restore_process")
async def restore_process(
    checkpoint_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-action")),
):
    """Restore a process from checkpoint. Returns the restored process in RESUMING state."""
    pm = _get_process_manager(request)
    if pm is None:
        raise HTTPException(status_code=503, detail="ProcessManager not available")

    try:
        rpcb = await pm.restore_process(checkpoint_id)
        return RestoreResponse(
            run_id=rpcb.run_id,
            checkpoint_id=checkpoint_id,
            state=rpcb.state.value,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/processes/{run_id}/suspend", response_model=ActionResult)
@idempotent("process.suspend_process")
async def suspend_process(
    run_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-action")),
):
    """Suspend a running process."""
    pm = _get_process_manager(request)
    if pm is None:
        raise HTTPException(status_code=503, detail="ProcessManager not available")

    try:
        await pm.suspend_process(run_id)
        return ActionResult(run_id=run_id, status="suspended")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/processes/{run_id}/resume", response_model=ActionResult)
@idempotent("process.resume_process")
async def resume_process(
    run_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-action")),
):
    """Resume a suspended or checkpointed process."""
    pm = _get_process_manager(request)
    if pm is None:
        raise HTTPException(status_code=503, detail="ProcessManager not available")

    try:
        await pm.resume_process(run_id)
        return ActionResult(run_id=run_id, status="resumed")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/processes/{run_id}/terminate", response_model=ActionResult)
@idempotent("process.terminate_process")
async def terminate_process(
    run_id: str,
    request: Request,
    reason: str = "operator",
    user_id: str = Depends(require_permission("perm-execute-action")),
):
    """Terminate a process."""
    pm = _get_process_manager(request)
    if pm is None:
        raise HTTPException(status_code=503, detail="ProcessManager not available")

    try:
        await pm.terminate_process(run_id, reason=reason)
        return ActionResult(run_id=run_id, status="terminated", detail=reason)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
