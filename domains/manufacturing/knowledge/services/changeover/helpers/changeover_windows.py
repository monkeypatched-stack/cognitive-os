from datetime import datetime, timedelta
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.pm.helpers import calendar_bookings
from services.changeover.helpers.changeover_common import (
    create,
    delete,
    get_all,
    get_by_id,
    update,
)
from services.pm.models.calendar_booking import CalendarBookingCreate
from services.changeover.models.changeover_windows import (
    ChangeoverWindowCalendarBookingCreate,
    ChangeoverWindowCreate,
    ChangeoverWindowUpdate,
)

COLLECTION = "changeover_windows"


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _calendar_title(window: dict) -> str:
    return f"Changeover window - workstation {window['workstation_id']}"


async def get_all_windows(
    db: AsyncIOMotorDatabase, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    return await get_all(db, COLLECTION, page, page_size)


async def get_window_by_id(db: AsyncIOMotorDatabase, window_id: str) -> Optional[dict]:
    return await get_by_id(db, COLLECTION, window_id)


async def get_windows_by_workstation(
    db: AsyncIOMotorDatabase, workstation_id: str
) -> list[dict]:
    records, _ = await get_all(
        db, COLLECTION, query={"workstation_id": workstation_id}, page_size=1000
    )
    return records


async def create_window(db: AsyncIOMotorDatabase, data: ChangeoverWindowCreate) -> dict:
    return await create(db, COLLECTION, data)


async def update_window(
    db: AsyncIOMotorDatabase, window_id: str, data: ChangeoverWindowUpdate
) -> Optional[dict]:
    return await update(db, COLLECTION, window_id, data)


async def add_window_to_calendar(
    db: AsyncIOMotorDatabase,
    window_id: str,
    data: ChangeoverWindowCalendarBookingCreate,
) -> Optional[dict]:
    window = await get_window_by_id(db, window_id)
    if not window:
        return None

    existing_booking_id = window.get("calendar_booking_id")
    if existing_booking_id:
        existing_booking = await calendar_bookings.get_by_id(
            db, str(existing_booking_id)
        )
        if existing_booking:
            return existing_booking

    start_at = _to_datetime(window["start_dt"])
    end_at = _to_datetime(window["end_dt"])
    if data.include_buffer:
        end_at = end_at + timedelta(minutes=window.get("buffer_minutes", 0))

    booking = CalendarBookingCreate(
        calendar_id=data.calendar_id or str(window["workstation_id"]),
        title=data.title or _calendar_title(window),
        start_at=start_at,
        end_at=end_at,
        booked_by=data.booked_by,
        attendee_ids=data.attendee_ids,
        description=data.description
        or f"Changeover window {window_id} for workstation {window['workstation_id']}.",
    )

    if await calendar_bookings.has_conflict(
        db, booking.calendar_id, booking.start_at, booking.end_at
    ):
        return {"conflict": True}

    created = await calendar_bookings.create(db, booking)
    booking_id = created.get("id") or str(booking.id)
    await db[COLLECTION].find_one_and_update(
        {"id": window_id},
        {"$set": {"calendar_booking_id": booking_id}},
        return_document=ReturnDocument.AFTER,
    )
    created["id"] = booking_id
    return created


async def delete_window(db: AsyncIOMotorDatabase, window_id: str) -> bool:
    return await delete(db, COLLECTION, window_id)
