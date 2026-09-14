from datetime import date, datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.products.models.product_common import utc_now
from services.inventory.models.stock_adjustment_requests import (
    StockAdjustmentRequestCreate,
    StockAdjustmentRequestUpdate,
)

COLLECTION = "stock_adjustment_requests"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _prepare(value):
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
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_request_id(
    db: AsyncIOMotorDatabase,
    request_id: str,
) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"request_id": request_id}))


async def get_by_inventory_id(
    db: AsyncIOMotorDatabase, inventory_id: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"inventory_id": inventory_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_product_id(db: AsyncIOMotorDatabase, product_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"product_id": product_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_location_id(db: AsyncIOMotorDatabase, location_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"location_id": location_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    cursor = db[COLLECTION].find({"status": status})
    return [_serialize(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: StockAdjustmentRequestCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    request_id: str,
    data: StockAdjustmentRequestUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_request_id(db, request_id)
    fields["updated_at"] = utc_now()
    result = await db[COLLECTION].find_one_and_update(
        {"request_id": request_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, request_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"request_id": request_id})
    return result.deleted_count == 1
