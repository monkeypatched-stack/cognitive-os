from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.shipping.models.delivery_note import (
    DeliveryNoteCreate,
    DeliveryNoteUpdate,
    utc_now,
)

COLLECTION = "delivery_notes"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _prepare(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, dict):
        return {key: _prepare(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare(item) for item in value]
    return value


async def get_all(
    db: AsyncIOMotorDatabase, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, delivery_note_id: str) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"id": delivery_note_id}))


async def get_by_number(
    db: AsyncIOMotorDatabase, delivery_note_number: str
) -> Optional[dict]:
    return _serialize(
        await db[COLLECTION].find_one({"delivery_note_number": delivery_note_number})
    )


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    cursor = db[COLLECTION].find({"status": status})
    return [_serialize(doc) async for doc in cursor]


async def get_by_carrier(db: AsyncIOMotorDatabase, carrier_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"carrier_id": carrier_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_vehicle(db: AsyncIOMotorDatabase, vehicle_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"vehicle_id": vehicle_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_tracking_number(
    db: AsyncIOMotorDatabase, tracking_number: str
) -> Optional[dict]:
    return _serialize(
        await db[COLLECTION].find_one({"tracking_number": tracking_number})
    )


async def create(db: AsyncIOMotorDatabase, data: DeliveryNoteCreate) -> dict:
    doc = _prepare(data.model_dump(mode="python"))
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    delivery_note_id: str,
    data: DeliveryNoteUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(mode="python", exclude_unset=True))
    if not fields:
        return await get_by_id(db, delivery_note_id)
    fields["updated_at"] = utc_now()
    result = await db[COLLECTION].find_one_and_update(
        {"id": delivery_note_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, delivery_note_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": delivery_note_id})
    return result.deleted_count == 1
