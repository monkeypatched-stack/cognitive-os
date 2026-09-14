from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.db import get_database
from services.common.auth import require_permission
from services.pm.models.downtime import (
    DowntimeLogCreate,
    DowntimeLogUpdate,
    DowntimeLogResponse,
    PaginatedDowntimeResponse,
)
from services.pm.helpers import downtime as crud

router = APIRouter()


# ── Summary: per-equipment total downtime + MTTR ──────────────────────────────


@router.get("/summary")
async def downtime_summary(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-breakdown")),
):
    return await crud.get_equipment_downtime_summary()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedDowntimeResponse)
async def list_downtime_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-breakdown")),
):
    logs, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedDowntimeResponse(
        total=total, page=page, page_size=page_size, results=logs
    )


# ── Get by machine ────────────────────────────────────────────────────────────


@router.get("/by-machine/{machine_id}", response_model=list[DowntimeLogResponse])
async def list_downtime_by_machine(
    machine_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-breakdown")),
):
    return await crud.get_by_machine(db, machine_id)


@router.get("/by-equipment/{equipment_id}", response_model=list[DowntimeLogResponse])
async def list_downtime_by_equipment(
    equipment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-breakdown")),
):
    return await crud.get_by_equipment_id(db, equipment_id)


# ── Get by line ───────────────────────────────────────────────────────────────


@router.get("/by-line/{line_id}", response_model=list[DowntimeLogResponse])
async def list_downtime_by_line(
    line_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-breakdown")),
):
    return await crud.get_by_line(db, line_id)


# ── Get by status ─────────────────────────────────────────────────────────────


@router.get("/by-status/{status_value}", response_model=list[DowntimeLogResponse])
async def list_downtime_by_status(
    status_value: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-breakdown")),
):
    return await crud.get_by_status(db, status_value)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{log_id}", response_model=DowntimeLogResponse)
async def get_downtime_log(
    log_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-breakdown")),
):
    record = await crud.get_by_id(db, log_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Downtime log '{log_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=DowntimeLogResponse, status_code=status.HTTP_201_CREATED
)
async def create_downtime_log(
    data: DowntimeLogCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-breakdown")),
):
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Downtime log '{data.id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{log_id}", response_model=DowntimeLogResponse)
async def update_downtime_log(
    log_id: str,
    data: DowntimeLogUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-breakdown")),
):
    updated = await crud.update(db, log_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Downtime log '{log_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_downtime_log(
    log_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-breakdown")),
):
    if not await crud.delete(db, log_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Downtime log '{log_id}' not found",
        )
