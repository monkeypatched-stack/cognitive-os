from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.shipping.models.waybill import WaybillCreate, WaybillUpdate, utc_now

COLLECTION = "waybills"


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
        return str(value)
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


async def get_by_id(db: AsyncIOMotorDatabase, waybill_id: str) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"id": waybill_id}))


async def get_by_number(
    db: AsyncIOMotorDatabase, waybill_number: str
) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"waybill_number": waybill_number}))


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    cursor = db[COLLECTION].find({"status": status})
    return [_serialize(doc) async for doc in cursor]


async def get_by_barcode(db: AsyncIOMotorDatabase, barcode: str) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"barcode": barcode}))


async def create(db: AsyncIOMotorDatabase, data: WaybillCreate) -> dict:
    doc = _prepare(data.model_dump(mode="python"))
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, waybill_id: str, data: WaybillUpdate
) -> Optional[dict]:
    fields = _prepare(data.model_dump(mode="python", exclude_unset=True))
    if not fields:
        return await get_by_id(db, waybill_id)
    fields["updated_at"] = utc_now()
    result = await db[COLLECTION].find_one_and_update(
        {"id": waybill_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, waybill_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": waybill_id})
    return result.deleted_count == 1
