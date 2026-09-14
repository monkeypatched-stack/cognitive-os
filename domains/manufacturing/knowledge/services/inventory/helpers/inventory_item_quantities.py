from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.inventory.models.inventory_item_quantity import (
    InventoryItemQuantityCreate,
    InventoryItemQuantityResponse,
    InventoryItemQuantityUpdate,
)
from services.products.models.product_common import utc_now

COLLECTION = "inventory_item_quantities"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


def _prepare(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, dict):
        return {key: _prepare(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare(item) for item in value]
    return value


def _as_response_doc(data: dict) -> dict:
    return _prepare(InventoryItemQuantityResponse(**data).model_dump())


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, record_id: str) -> Optional[dict]:
    try:
        object_id = ObjectId(record_id)
    except Exception:
        return None
    return _serialize(await db[COLLECTION].find_one({"_id": object_id}))


async def get_by_sku(db: AsyncIOMotorDatabase, sku: str) -> list[dict]:
    cursor = db[COLLECTION].find({"sku": sku})
    return [_serialize(doc) async for doc in cursor]


async def get_by_srn(db: AsyncIOMotorDatabase, srn: str) -> list[dict]:
    cursor = db[COLLECTION].find({"srn": srn})
    return [_serialize(doc) async for doc in cursor]


async def get_by_warehouse_id(
    db: AsyncIOMotorDatabase, warehouse_id: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"warehouse_id": warehouse_id})
    return [_serialize(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: InventoryItemQuantityCreate) -> dict:
    now = utc_now()
    doc = _prepare({**data.model_dump(), "updated_at": now})
    result = await db[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    record_id: str,
    data: InventoryItemQuantityUpdate,
) -> Optional[dict]:
    existing = await get_by_id(db, record_id)
    if not existing:
        return None
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return existing
    merged = {**existing, **fields, "id": record_id, "updated_at": utc_now()}
    doc = _as_response_doc(merged)
    object_id = ObjectId(record_id)
    result = await db[COLLECTION].find_one_and_update(
        {"_id": object_id},
        {"$set": {key: value for key, value in doc.items() if key != "id"}},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, record_id: str) -> bool:
    try:
        object_id = ObjectId(record_id)
    except Exception:
        return False
    result = await db[COLLECTION].delete_one({"_id": object_id})
    return result.deleted_count == 1
