from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.workorders.helpers import executed_instruction_evidence as crud
from services.workorders.models.executed_instruction_evidence import (
    ExecutedInstructionEvidenceCreate,
    ExecutedInstructionEvidenceResponse,
    ExecutedInstructionEvidenceUpdate,
    PaginatedExecutedInstructionEvidenceResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedExecutedInstructionEvidenceResponse)
async def list_executed_instruction_evidence(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    batch_step_execution_id: str | None = Query(None),
    batch_execution_record_id: str | None = Query(None),
    batch_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    records, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        batch_step_execution_id=batch_step_execution_id,
        batch_execution_record_id=batch_execution_record_id,
        batch_id=batch_id,
        status=status_filter,
    )
    return PaginatedExecutedInstructionEvidenceResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get(
    "/{executed_instruction_evidence_id}",
    response_model=ExecutedInstructionEvidenceResponse,
)
async def get_executed_instruction_evidence(
    executed_instruction_evidence_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    record = await crud.get_by_id(db, executed_instruction_evidence_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Executed instruction evidence '{executed_instruction_evidence_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=ExecutedInstructionEvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_executed_instruction_evidence(
    data: ExecutedInstructionEvidenceCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-tasks")),
):
    if await crud.get_by_id(db, data.executed_instruction_evidence_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Executed instruction evidence '{data.executed_instruction_evidence_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    return await crud.create(db, data)


@router.patch(
    "/{executed_instruction_evidence_id}",
    response_model=ExecutedInstructionEvidenceResponse,
)
async def update_executed_instruction_evidence(
    executed_instruction_evidence_id: str,
    data: ExecutedInstructionEvidenceUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await crud.update(db, executed_instruction_evidence_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Executed instruction evidence '{executed_instruction_evidence_id}' not found",
        )
    return updated


@router.delete(
    "/{executed_instruction_evidence_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_executed_instruction_evidence(
    executed_instruction_evidence_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-tasks")),
):
    if not await crud.delete(db, executed_instruction_evidence_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Executed instruction evidence '{executed_instruction_evidence_id}' not found",
        )
