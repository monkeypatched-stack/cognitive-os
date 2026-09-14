from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.shifts.helpers import shifts as crud
from services.shifts.models.shift import (
    PaginatedShiftScheduleResponse,
    ShiftScheduleCreate,
    ShiftScheduleResponse,
    ShiftScheduleUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedShiftScheduleResponse)
async def list_shifts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-shifts")),
):
    shifts, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedShiftScheduleResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=shifts,
    )


@router.get("/by-factory/{factory_id}", response_model=list[ShiftScheduleResponse])
async def list_shifts_by_factory(
    factory_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-shifts")),
):
    return await crud.get_by_factory(db, factory_id)


@router.get("/by-template/{template_id}", response_model=list[ShiftScheduleResponse])
async def list_shifts_by_template(
    template_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-shifts")),
):
    return await crud.get_by_template(db, template_id)


@router.get("/by-date/{shift_date}", response_model=list[ShiftScheduleResponse])
async def list_shifts_by_date(
    shift_date: date,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-shifts")),
):
    return await crud.get_by_date(db, shift_date)


@router.get("/{shift_id}", response_model=ShiftScheduleResponse)
async def get_shift(
    shift_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-shifts")),
):
    record = await crud.get_by_id(db, shift_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shift '{shift_id}' not found",
        )
    return record


@router.post(
    "/", response_model=ShiftScheduleResponse, status_code=status.HTTP_201_CREATED
)
async def create_shift(
    data: ShiftScheduleCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-shifts")),
):
    if await crud.get_by_id(db, str(data.id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Shift '{data.id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{shift_id}", response_model=ShiftScheduleResponse)
async def update_shift(
    shift_id: str,
    data: ShiftScheduleUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-shifts")),
):
    updated = await crud.update(db, shift_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shift '{shift_id}' not found",
        )
    return updated


@router.delete("/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shift(
    shift_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-shifts")),
):
    if not await crud.delete(db, shift_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shift '{shift_id}' not found",
        )
