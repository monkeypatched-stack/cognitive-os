from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.pm.helpers import weekly_schedules as crud
from services.pm.models.weekly_schedule import (
    PaginatedWeeklyScheduleResponse,
    WeeklyScheduleCreate,
    WeeklyScheduleResponse,
    WeeklyScheduleUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedWeeklyScheduleResponse)
async def list_weekly_schedules(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-weekly-schedules")),
):
    schedules, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedWeeklyScheduleResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=schedules,
    )


@router.get("/by-factory/{factory_id}", response_model=list[WeeklyScheduleResponse])
async def list_weekly_schedules_by_factory(
    factory_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-weekly-schedules")),
):
    return await crud.get_by_factory(db, factory_id)


@router.get("/by-week-start/{week_start}", response_model=list[WeeklyScheduleResponse])
async def list_weekly_schedules_by_week_start(
    week_start: date,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-weekly-schedules")),
):
    return await crud.get_by_week_start(db, week_start)


@router.get("/{schedule_id}", response_model=WeeklyScheduleResponse)
async def get_weekly_schedule(
    schedule_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-weekly-schedules")),
):
    record = await crud.get_by_id(db, schedule_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Weekly schedule '{schedule_id}' not found",
        )
    return record


@router.post(
    "/", response_model=WeeklyScheduleResponse, status_code=status.HTTP_201_CREATED
)
async def create_weekly_schedule(
    data: WeeklyScheduleCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-weekly-schedules")),
):
    if await crud.get_by_id(db, str(data.id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Weekly schedule '{data.id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{schedule_id}", response_model=WeeklyScheduleResponse)
async def update_weekly_schedule(
    schedule_id: str,
    data: WeeklyScheduleUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-weekly-schedules")),
):
    updated = await crud.update(db, schedule_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Weekly schedule '{schedule_id}' not found",
        )
    return updated


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_weekly_schedule(
    schedule_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-weekly-schedules")),
):
    if not await crud.delete(db, schedule_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Weekly schedule '{schedule_id}' not found",
        )
