from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.pm.helpers import calendar_bookings as crud
from services.pm.models.calendar_booking import (
    CalendarBookingCreate,
    CalendarBookingResponse,
    CalendarBookingUpdate,
    PaginatedCalendarBookingResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedCalendarBookingResponse)
async def list_calendar_bookings(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-calendar-bookings")),
):
    bookings, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedCalendarBookingResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=bookings,
    )


@router.get("/by-calendar/{calendar_id}", response_model=list[CalendarBookingResponse])
async def list_calendar_bookings_by_calendar(
    calendar_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-calendar-bookings")),
):
    return await crud.get_by_calendar(db, calendar_id)


@router.get("/by-user/{booked_by}", response_model=list[CalendarBookingResponse])
async def list_calendar_bookings_by_user(
    booked_by: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-calendar-bookings")),
):
    return await crud.get_by_booked_by(db, booked_by)


@router.get("/{booking_id}", response_model=CalendarBookingResponse)
async def get_calendar_booking(
    booking_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-calendar-bookings")),
):
    record = await crud.get_by_id(db, booking_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Calendar booking '{booking_id}' not found",
        )
    return record


@router.post(
    "/book", response_model=CalendarBookingResponse, status_code=status.HTTP_201_CREATED
)
async def book_calendar_time(
    data: CalendarBookingCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-calendar-bookings")),
):
    if await crud.get_by_id(db, str(data.id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Calendar booking '{data.id}' already exists",
        )
    if data.status == "booked" and await crud.has_conflict(
        db, data.calendar_id, data.start_at, data.end_at
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Calendar time is already booked",
        )
    return await crud.create(db, data)


@router.post(
    "/", response_model=CalendarBookingResponse, status_code=status.HTTP_201_CREATED
)
async def create_calendar_booking(
    data: CalendarBookingCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-calendar-bookings")),
):
    return await book_calendar_time(data, db, _)


@router.patch("/{booking_id}", response_model=CalendarBookingResponse)
async def update_calendar_booking(
    booking_id: str,
    data: CalendarBookingUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-calendar-bookings")),
):
    existing = await crud.get_by_id(db, booking_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Calendar booking '{booking_id}' not found",
        )

    calendar_id = data.calendar_id or existing["calendar_id"]
    start_at = data.start_at or existing["start_at"]
    end_at = data.end_at or existing["end_at"]
    next_status = data.status or existing.get("status", "booked")
    if next_status == "booked" and await crud.has_conflict(
        db,
        calendar_id,
        start_at,
        end_at,
        exclude_id=booking_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Calendar time is already booked",
        )

    updated = await crud.update(db, booking_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Calendar booking '{booking_id}' not found",
        )
    return updated


@router.delete("/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_calendar_booking(
    booking_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-calendar-bookings")),
):
    if not await crud.delete(db, booking_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Calendar booking '{booking_id}' not found",
        )
