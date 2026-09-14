from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.inventory.models.inventory_items import (
    InventoryItemCreate,
    InventoryItemUpdate,
)
from services.products.models.product_common import utc_now

COLLECTION = "inventory_items"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


def _prepare(value):
    if isinstance(value, ObjectId):
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
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_sku(db: AsyncIOMotorDatabase, sku: str) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"sku": sku}))


async def get_by_warehouse_id(
    db: AsyncIOMotorDatabase, warehouse_id: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"warehouse_id": warehouse_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_supplier_id(db: AsyncIOMotorDatabase, supplier_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"supplier_id": supplier_id})
    return [_serialize(doc) async for doc in cursor]


async def get_low_stock(db: AsyncIOMotorDatabase) -> list[dict]:
    cursor = db[COLLECTION].find(
        {"$expr": {"$lte": ["$quantity_available", "$reorder_point"]}}
    )
    return [_serialize(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: InventoryItemCreate) -> dict:
    doc = _prepare(data.model_dump())
    doc["updated_at"] = utc_now()
    result = await db[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    sku: str,
    data: InventoryItemUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_sku(db, sku)
    fields["updated_at"] = utc_now()
    result = await db[COLLECTION].find_one_and_update(
        {"sku": sku},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, sku: str) -> bool:
    result = await db[COLLECTION].delete_one({"sku": sku})
    return result.deleted_count == 1
