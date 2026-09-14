from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.pm.models.calendar_booking import (
    CalendarBookingCreate,
    CalendarBookingUpdate,
)

COLLECTION = "calendar_bookings"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _json_safe(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _dump(data) -> dict:
    return _json_safe(data.model_dump(exclude_unset=True))


def _overlap_query(
    calendar_id: str,
    start_at: datetime,
    end_at: datetime,
    exclude_id: Optional[str] = None,
) -> dict:
    query = {
        "calendar_id": calendar_id,
        "status": "booked",
        "start_at": {"$lt": end_at},
        "end_at": {"$gt": start_at},
    }
    if exclude_id is not None:
        query["id"] = {"$ne": exclude_id}
    return query


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, booking_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": booking_id})
    return _serialize(doc) if doc else None


async def get_by_calendar(db: AsyncIOMotorDatabase, calendar_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"calendar_id": calendar_id})
    return [_serialize(d) async for d in cursor]


async def get_by_booked_by(db: AsyncIOMotorDatabase, booked_by: str) -> list[dict]:
    cursor = db[COLLECTION].find({"booked_by": booked_by})
    return [_serialize(d) async for d in cursor]


async def has_conflict(
    db: AsyncIOMotorDatabase,
    calendar_id: str,
    start_at: datetime,
    end_at: datetime,
    exclude_id: Optional[str] = None,
) -> bool:
    return (
        await db[COLLECTION].find_one(
            _overlap_query(calendar_id, start_at, end_at, exclude_id=exclude_id)
        )
        is not None
    )


async def create(db: AsyncIOMotorDatabase, data: CalendarBookingCreate) -> dict:
    doc = _json_safe(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    booking_id: str,
    data: CalendarBookingUpdate,
) -> Optional[dict]:
    fields = _dump(data)
    if not fields:
        return await get_by_id(db, booking_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": booking_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, booking_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": booking_id})
    return result.deleted_count == 1
