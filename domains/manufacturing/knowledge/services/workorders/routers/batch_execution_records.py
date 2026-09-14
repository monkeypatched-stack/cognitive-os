from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.workorders.helpers import batch_execution_records as crud
from services.workorders.models.batch_execution_records import (
    BatchProductionExecutionRecordCreate,
    BatchProductionExecutionRecordResponse,
    BatchProductionExecutionRecordUpdate,
    PaginatedBatchProductionExecutionRecordResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedBatchProductionExecutionRecordResponse)
async def list_batch_execution_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    batch_id: str | None = Query(None),
    process_definition_id: str | None = Query(None),
    work_order_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    records, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        batch_id=batch_id,
        process_definition_id=process_definition_id,
        work_order_id=work_order_id,
        status=status_filter,
    )
    return PaginatedBatchProductionExecutionRecordResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get(
    "/{batch_execution_record_id}",
    response_model=BatchProductionExecutionRecordResponse,
)
async def get_batch_execution_record(
    batch_execution_record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    record = await crud.get_by_id(db, batch_execution_record_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Batch execution record '{batch_execution_record_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=BatchProductionExecutionRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_batch_execution_record(
    data: BatchProductionExecutionRecordCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-tasks")),
):
    if await crud.get_by_id(db, data.batch_execution_record_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Batch execution record '{data.batch_execution_record_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    return await crud.create(db, data)


@router.patch(
    "/{batch_execution_record_id}",
    response_model=BatchProductionExecutionRecordResponse,
)
async def update_batch_execution_record(
    batch_execution_record_id: str,
    data: BatchProductionExecutionRecordUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await crud.update(db, batch_execution_record_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Batch execution record '{batch_execution_record_id}' not found",
        )
    return updated


@router.delete("/{batch_execution_record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_batch_execution_record(
    batch_execution_record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-tasks")),
):
    if not await crud.delete(db, batch_execution_record_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Batch execution record '{batch_execution_record_id}' not found",
        )
