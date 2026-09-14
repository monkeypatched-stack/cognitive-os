from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.workorders.helpers import yield_reconciliation_records as crud
from services.workorders.models.yield_reconciliation_records import (
    PaginatedYieldReconciliationRecordResponse,
    YieldReconciliationRecordCreate,
    YieldReconciliationRecordResponse,
    YieldReconciliationRecordUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedYieldReconciliationRecordResponse)
async def list_yield_reconciliation_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    batch_execution_record_id: str | None = Query(None),
    batch_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    disposition: str | None = Query(None),
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
        disposition=disposition,
    )
    return PaginatedYieldReconciliationRecordResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get(
    "/{yield_reconciliation_id}", response_model=YieldReconciliationRecordResponse
)
async def get_yield_reconciliation_record(
    yield_reconciliation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    record = await crud.get_by_id(db, yield_reconciliation_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Yield reconciliation record '{yield_reconciliation_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=YieldReconciliationRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_yield_reconciliation_record(
    data: YieldReconciliationRecordCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-tasks")),
):
    if await crud.get_by_id(db, data.yield_reconciliation_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Yield reconciliation record '{data.yield_reconciliation_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    return await crud.create(db, data)


@router.patch(
    "/{yield_reconciliation_id}", response_model=YieldReconciliationRecordResponse
)
async def update_yield_reconciliation_record(
    yield_reconciliation_id: str,
    data: YieldReconciliationRecordUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await crud.update(db, yield_reconciliation_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Yield reconciliation record '{yield_reconciliation_id}' not found",
        )
    return updated


@router.delete("/{yield_reconciliation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_yield_reconciliation_record(
    yield_reconciliation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-tasks")),
):
    if not await crud.delete(db, yield_reconciliation_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Yield reconciliation record '{yield_reconciliation_id}' not found",
        )
