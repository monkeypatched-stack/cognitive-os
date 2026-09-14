from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.workorders.models.production_batches import (
    ProductionBatch,
    ProductionBatchCreate,
    ProductionBatchUpdate,
)

COLLECTION = "production_batches"


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
    product_id: Optional[str] = None,
    work_order_id: Optional[str] = None,
    process_definition_id: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if product_id:
        query["product_id"] = product_id
    if work_order_id:
        query["work_order_id"] = work_order_id
    if process_definition_id:
        query["process_definition_id"] = process_definition_id
    if status:
        query["status"] = status

    total = await db[COLLECTION].count_documents(query)
    cursor = (
        db[COLLECTION]
        .find(query)
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, batch_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"batch_id": batch_id})
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: ProductionBatchCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    batch_id: str,
    data: ProductionBatchUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, batch_id)

    existing = await db[COLLECTION].find_one({"batch_id": batch_id})
    if not existing:
        return None

    merged = _serialize(existing)
    merged.update(fields)
    validated = ProductionBatch(**merged)
    fields = _prepare(validated.model_dump(exclude_unset=True))
    result = await db[COLLECTION].find_one_and_update(
        {"batch_id": batch_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, batch_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"batch_id": batch_id})
    return result.deleted_count == 1
