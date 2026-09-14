from datetime import date
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.pm.models.weekly_schedule import (
    WeeklyScheduleCreate,
    WeeklyScheduleUpdate,
)

COLLECTION = "weekly_schedules"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _dump(data) -> dict:
    doc = data.model_dump(mode="json", exclude_unset=True)
    doc.pop("week_end", None)
    doc.pop("total_shifts", None)
    doc.pop("total_headcount", None)
    for shift in doc.get("shifts", []):
        if isinstance(shift, dict):
            shift.pop("headcount", None)
    return doc


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, schedule_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": schedule_id})
    return _serialize(doc) if doc else None


async def get_by_factory(db: AsyncIOMotorDatabase, factory_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"factory_id": factory_id})
    return [_serialize(d) async for d in cursor]


async def get_by_week_start(db: AsyncIOMotorDatabase, week_start: date) -> list[dict]:
    cursor = db[COLLECTION].find({"week_start": week_start.isoformat()})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: WeeklyScheduleCreate) -> dict:
    doc = _dump(data)
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    schedule_id: str,
    data: WeeklyScheduleUpdate,
) -> Optional[dict]:
    fields = _dump(data)
    if not fields:
        return await get_by_id(db, schedule_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": schedule_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, schedule_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": schedule_id})
    return result.deleted_count == 1
