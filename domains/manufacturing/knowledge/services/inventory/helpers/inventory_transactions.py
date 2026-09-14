from datetime import date, datetime, timezone
from typing import Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.inventory.models.inventory_transactions import (
    InventoryTransactionCreate,
    InventoryTransactionRecord,
    InventoryTransactionUpdate,
)
from services.products.models.product_common import utc_now

COLLECTION = "inventory_transactions"


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


def _as_record_doc(data: dict) -> dict:
    return _prepare(InventoryTransactionRecord(**data).model_dump())


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_transaction_id(
    db: AsyncIOMotorDatabase,
    transaction_id: str,
) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"transaction_id": transaction_id}))


async def get_by_inventory_id(
    db: AsyncIOMotorDatabase, inventory_id: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"inventory_id": inventory_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_product_id(db: AsyncIOMotorDatabase, product_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"product_id": product_id})
    return [_serialize(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: InventoryTransactionCreate) -> dict:
    now = utc_now()
    transaction_id = f"inventory-transaction-{uuid4().hex[:12]}"
    doc = _as_record_doc(
        {
            **data.model_dump(),
            "transaction_id": transaction_id,
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    transaction_id: str,
    data: InventoryTransactionUpdate,
) -> Optional[dict]:
    existing = await get_by_transaction_id(db, transaction_id)
    if not existing:
        return None
    merged = {
        **existing,
        **data.model_dump(exclude_unset=True),
        "transaction_id": transaction_id,
        "updated_at": utc_now(),
    }
    doc = _as_record_doc(merged)
    result = await db[COLLECTION].find_one_and_update(
        {"transaction_id": transaction_id},
        {"$set": doc},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, transaction_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"transaction_id": transaction_id})
    return result.deleted_count == 1
