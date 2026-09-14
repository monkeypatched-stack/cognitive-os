from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.inventory.models.inventory_allocations import (
    InventoryAllocationCreate,
    InventoryAllocationResponse,
    InventoryAllocationUpdate,
)
from services.products.models.product_common import utc_now

COLLECTION = "inventory_allocations"


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
    return _prepare(InventoryAllocationResponse(**data).model_dump())


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_allocation_id(
    db: AsyncIOMotorDatabase,
    allocation_id: str,
) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"allocation_id": allocation_id}))


async def get_by_workstation_id(
    db: AsyncIOMotorDatabase,
    workstation_id: str,
) -> list[dict]:
    cursor = db[COLLECTION].find({"workstation_id": workstation_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_warehouse_id(
    db: AsyncIOMotorDatabase, warehouse_id: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"warehouse_id": warehouse_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_sku(db: AsyncIOMotorDatabase, sku: str) -> list[dict]:
    cursor = db[COLLECTION].find({"sku": sku})
    return [_serialize(doc) async for doc in cursor]


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    cursor = db[COLLECTION].find({"status": status})
    return [_serialize(doc) async for doc in cursor]


async def get_by_request_type(
    db: AsyncIOMotorDatabase, request_type: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"request_type": request_type})
    return [_serialize(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: InventoryAllocationCreate) -> dict:
    now = utc_now()
    allocation_id = f"inventory-allocation-{uuid4().hex[:12]}"
    doc = _prepare(
        {
            **data.model_dump(),
            "allocation_id": allocation_id,
            "updated_at": now,
        }
    )
    result = await db[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    allocation_id: str,
    data: InventoryAllocationUpdate,
) -> Optional[dict]:
    existing = await get_by_allocation_id(db, allocation_id)
    if not existing:
        return None
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return existing
    merged = {
        **existing,
        **fields,
        "allocation_id": allocation_id,
        "updated_at": utc_now(),
    }
    doc = _as_response_doc(merged)
    result = await db[COLLECTION].find_one_and_update(
        {"allocation_id": allocation_id},
        {"$set": {key: value for key, value in doc.items() if key != "id"}},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, allocation_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"allocation_id": allocation_id})
    return result.deleted_count == 1
