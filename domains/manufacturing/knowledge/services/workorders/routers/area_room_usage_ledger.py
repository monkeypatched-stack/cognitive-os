from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.workorders.helpers import area_room_usage_ledger as crud
from services.workorders.models.area_room_usage_ledger import (
    AreaRoomUsageLedgerCreate,
    AreaRoomUsageLedgerResponse,
    AreaRoomUsageLedgerUpdate,
    PaginatedAreaRoomUsageLedgerResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedAreaRoomUsageLedgerResponse)
async def list_area_room_usage_ledger(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    batch_execution_record_id: str | None = Query(None),
    batch_id: str | None = Query(None),
    process_step_id: str | None = Query(None),
    operator_id: str | None = Query(None),
    room_id: str | None = Query(None),
    area_id: str | None = Query(None),
    bay_id: str | None = Query(None),
    started_from: datetime | None = Query(None),
    started_to: datetime | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    records, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        batch_execution_record_id=batch_execution_record_id,
        batch_id=batch_id,
        process_step_id=process_step_id,
        operator_id=operator_id,
        room_id=room_id,
        area_id=area_id,
        bay_id=bay_id,
        started_from=started_from,
        started_to=started_to,
        status=status_filter,
    )
    return PaginatedAreaRoomUsageLedgerResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get("/{usage_id}", response_model=AreaRoomUsageLedgerResponse)
async def get_area_room_usage_ledger_entry(
    usage_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    record = await crud.get_by_id(db, usage_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Area/room usage ledger entry '{usage_id}' not found",
        )
    return record


@router.post(
    "/", response_model=AreaRoomUsageLedgerResponse, status_code=status.HTTP_201_CREATED
)
async def create_area_room_usage_ledger_entry(
    data: AreaRoomUsageLedgerCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-tasks")),
):
    if await crud.get_by_id(db, data.usage_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Area/room usage ledger entry '{data.usage_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    return await crud.create(db, data)


@router.patch("/{usage_id}", response_model=AreaRoomUsageLedgerResponse)
async def update_area_room_usage_ledger_entry(
    usage_id: str,
    data: AreaRoomUsageLedgerUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await crud.update(db, usage_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Area/room usage ledger entry '{usage_id}' not found",
        )
    return updated


@router.delete("/{usage_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_area_room_usage_ledger_entry(
    usage_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-tasks")),
):
    if not await crud.delete(db, usage_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Area/room usage ledger entry '{usage_id}' not found",
        )
