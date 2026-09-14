from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.workorders.models.area_room_usage_ledger import (
    AreaRoomUsageLedgerCreate,
    AreaRoomUsageLedgerEntry,
    AreaRoomUsageLedgerUpdate,
)

COLLECTION = "area_room_usage_ledger"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _to_utc(value):
    if not value:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return value


def _prepare(doc: dict) -> dict:
    result = {}
    for key, value in doc.items():
        if isinstance(value, datetime):
            result[key] = _to_utc(value)
        elif isinstance(value, dict):
            result[key] = _prepare(value)
        elif isinstance(value, list):
            result[key] = [
                _prepare(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            result[key] = value
    return result


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    batch_execution_record_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    process_step_id: Optional[str] = None,
    operator_id: Optional[str] = None,
    room_id: Optional[str] = None,
    area_id: Optional[str] = None,
    bay_id: Optional[str] = None,
    started_from: Optional[datetime] = None,
    started_to: Optional[datetime] = None,
    status: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if batch_execution_record_id:
        query["batch_execution_record_id"] = batch_execution_record_id
    if batch_id:
        query["batch_id"] = batch_id
    if process_step_id:
        query["process_step_id"] = process_step_id
    if operator_id:
        query["operator_id"] = operator_id
    if room_id:
        query["room_id"] = room_id
    if area_id:
        query["area_id"] = area_id
    if bay_id:
        query["bay_id"] = bay_id
    if status:
        query["status"] = status
    time_filter: dict = {}
    if started_from:
        time_filter["$gte"] = _to_utc(started_from)
    if started_to:
        time_filter["$lte"] = _to_utc(started_to)
    if time_filter:
        query["started_at"] = time_filter

    total = await db[COLLECTION].count_documents(query)
    cursor = (
        db[COLLECTION]
        .find(query)
        .sort("started_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, usage_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"usage_id": usage_id})
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: AreaRoomUsageLedgerCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    usage_id: str,
    data: AreaRoomUsageLedgerUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, usage_id)

    existing = await db[COLLECTION].find_one({"usage_id": usage_id})
    if not existing:
        return None

    merged = _serialize(existing)
    merged.update(fields)
    validated = AreaRoomUsageLedgerEntry(**merged)
    fields = _prepare(validated.model_dump(exclude_unset=True))

    result = await db[COLLECTION].find_one_and_update(
        {"usage_id": usage_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, usage_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"usage_id": usage_id})
    return result.deleted_count == 1
