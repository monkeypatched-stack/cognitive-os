from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.workorders.helpers import executed_bmr_records as crud
from services.workorders.models.executed_bmr_records import (
    ExecutedBmrRecordCreate,
    ExecutedBmrRecordResponse,
    ExecutedBmrRecordUpdate,
    PaginatedExecutedBmrRecordResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedExecutedBmrRecordResponse)
async def list_executed_bmr_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    batch_execution_record_id: str | None = Query(None),
    batch_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    mbmr_template_id: str | None = Query(None),
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
        mbmr_template_id=mbmr_template_id,
    )
    return PaginatedExecutedBmrRecordResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/{executed_bmr_record_id}", response_model=ExecutedBmrRecordResponse)
async def get_executed_bmr_record(
    executed_bmr_record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    record = await crud.get_by_id(db, executed_bmr_record_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Executed BMR record '{executed_bmr_record_id}' not found",
        )
    return record


@router.post(
    "/", response_model=ExecutedBmrRecordResponse, status_code=status.HTTP_201_CREATED
)
async def create_executed_bmr_record(
    data: ExecutedBmrRecordCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-tasks")),
):
    if await crud.get_by_id(db, data.executed_bmr_record_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Executed BMR record '{data.executed_bmr_record_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    return await crud.create(db, data)


@router.patch("/{executed_bmr_record_id}", response_model=ExecutedBmrRecordResponse)
async def update_executed_bmr_record(
    executed_bmr_record_id: str,
    data: ExecutedBmrRecordUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await crud.update(db, executed_bmr_record_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Executed BMR record '{executed_bmr_record_id}' not found",
        )
    return updated


@router.delete("/{executed_bmr_record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_executed_bmr_record(
    executed_bmr_record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-tasks")),
):
    if not await crud.delete(db, executed_bmr_record_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Executed BMR record '{executed_bmr_record_id}' not found",
        )
