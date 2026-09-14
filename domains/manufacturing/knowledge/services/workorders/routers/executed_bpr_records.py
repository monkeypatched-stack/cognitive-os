from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.workorders.helpers import executed_bpr_records as crud
from services.workorders.models.executed_bpr_records import (
    ExecutedBprRecordCreate,
    ExecutedBprRecordResponse,
    ExecutedBprRecordUpdate,
    PaginatedExecutedBprRecordResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedExecutedBprRecordResponse)
async def list_executed_bpr_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    batch_execution_record_id: str | None = Query(None),
    batch_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    packing_instruction_id: str | None = Query(None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    records, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        batch_execution_record_id=batch_execution_record_id,
        batch_id=batch_id,
        status=status_filter,
        packing_instruction_id=packing_instruction_id,
    )
    return PaginatedExecutedBprRecordResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/{executed_bpr_record_id}", response_model=ExecutedBprRecordResponse)
async def get_executed_bpr_record(
    executed_bpr_record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    record = await crud.get_by_id(db, executed_bpr_record_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Executed BPR record '{executed_bpr_record_id}' not found",
        )
    return record


@router.post(
    "/", response_model=ExecutedBprRecordResponse, status_code=status.HTTP_201_CREATED
)
async def create_executed_bpr_record(
    data: ExecutedBprRecordCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-tasks")),
):
    if await crud.get_by_id(db, data.executed_bpr_record_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Executed BPR record '{data.executed_bpr_record_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    return await crud.create(db, data)


@router.patch("/{executed_bpr_record_id}", response_model=ExecutedBprRecordResponse)
async def update_executed_bpr_record(
    executed_bpr_record_id: str,
    data: ExecutedBprRecordUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await crud.update(db, executed_bpr_record_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Executed BPR record '{executed_bpr_record_id}' not found",
        )
    return updated


@router.delete("/{executed_bpr_record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_executed_bpr_record(
    executed_bpr_record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-tasks")),
):
    if not await crud.delete(db, executed_bpr_record_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Executed BPR record '{executed_bpr_record_id}' not found",
        )
