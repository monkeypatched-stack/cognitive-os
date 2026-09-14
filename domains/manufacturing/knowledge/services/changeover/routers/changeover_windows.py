from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.changeover.helpers import changeover as crud
from services.pm.models.calendar_booking import CalendarBookingResponse
from services.changeover.models.changeover import (
    ChangeoverWindowCalendarBookingCreate,
    ChangeoverWindowCreate,
    ChangeoverWindowResponse,
    ChangeoverWindowUpdate,
    PaginatedChangeoverWindowResponse,
)

router = APIRouter()


@router.get("/windows", response_model=PaginatedChangeoverWindowResponse)
async def list_windows(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    results, total = await crud.get_all_windows(db, page=page, page_size=page_size)
    return PaginatedChangeoverWindowResponse(
        total=total, page=page, page_size=page_size, results=results
    )


@router.get(
    "/windows/by-workstation/{workstation_id}",
    response_model=list[ChangeoverWindowResponse],
)
async def list_windows_by_workstation(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    return await crud.get_windows_by_workstation(db, workstation_id)


@router.post(
    "/windows/{window_id}/calendar-booking",
    response_model=CalendarBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_window_to_calendar(
    window_id: str,
    data: ChangeoverWindowCalendarBookingCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-changeovers")),
    __: dict = Depends(require_permission("perm-create-calendar-bookings")),
):
    booking = await crud.add_window_to_calendar(db, window_id, data)
    if not booking:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Changeover window '{window_id}' not found",
        )
    if booking.get("conflict"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Calendar time is already booked"
        )
    return booking


@router.get("/windows/{window_id}", response_model=ChangeoverWindowResponse)
async def get_window(
    window_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    record = await crud.get_window_by_id(db, window_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Changeover window '{window_id}' not found",
        )
    return record


@router.post(
    "/windows",
    response_model=ChangeoverWindowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_window(
    data: ChangeoverWindowCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-changeovers")),
):
    if await crud.get_window_by_id(db, str(data.id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Changeover window '{data.id}' already exists",
        )
    return await crud.create_window(db, data)


@router.patch("/windows/{window_id}", response_model=ChangeoverWindowResponse)
async def update_window(
    window_id: str,
    data: ChangeoverWindowUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-changeovers")),
):
    updated = await crud.update_window(db, window_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Changeover window '{window_id}' not found",
        )
    return updated


@router.delete("/windows/{window_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_window(
    window_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-changeovers")),
):
    if not await crud.delete_window(db, window_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Changeover window '{window_id}' not found",
        )
