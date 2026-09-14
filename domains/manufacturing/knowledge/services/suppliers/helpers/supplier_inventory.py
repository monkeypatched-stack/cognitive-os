from typing import Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.suppliers.models.supplier_inventory import (
    SupplierInventoryCreate,
    SupplierInventoryUpdate,
)

COLLECTION = "supplier_inventory"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


async def get_all(
    db: AsyncIOMotorDatabase, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase, supplier_inventory_id: str
) -> Optional[dict]:
    return _serialize(
        await db[COLLECTION].find_one({"supplier_inventory_id": supplier_inventory_id})
    )


async def get_by_supplier(db: AsyncIOMotorDatabase, supplier_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"supplier_id": supplier_id})
    return [_serialize(d) async for d in cursor]


async def get_by_item(db: AsyncIOMotorDatabase, item_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"item_id": item_id})
    return [_serialize(d) async for d in cursor]


async def get_by_location(db: AsyncIOMotorDatabase, location_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"location_id": location_id})
    return [_serialize(d) async for d in cursor]


async def get_by_sku(db: AsyncIOMotorDatabase, sku: str) -> list[dict]:
    cursor = db[COLLECTION].find({"sku": sku})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: SupplierInventoryCreate) -> dict:
    doc = {
        **data.model_dump(),
        "supplier_inventory_id": f"supplier-inv-{uuid4().hex[:12]}",
    }
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, supplier_inventory_id: str, data: SupplierInventoryUpdate
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, supplier_inventory_id)
    result = await db[COLLECTION].find_one_and_update(
        {"supplier_inventory_id": supplier_inventory_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, supplier_inventory_id: str) -> bool:
    result = await db[COLLECTION].delete_one(
        {"supplier_inventory_id": supplier_inventory_id}
    )
    return result.deleted_count == 1
