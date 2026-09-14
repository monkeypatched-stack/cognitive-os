from typing import Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.suppliers.models.supplier_pricing import (
    SupplierPricingCreate,
    SupplierPricingUpdate,
)

COLLECTION = "supplier_pricing"


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
    db: AsyncIOMotorDatabase, supplier_pricing_id: str
) -> Optional[dict]:
    return _serialize(
        await db[COLLECTION].find_one({"supplier_pricing_id": supplier_pricing_id})
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


async def create(db: AsyncIOMotorDatabase, data: SupplierPricingCreate) -> dict:
    doc = {
        **data.model_dump(),
        "supplier_pricing_id": f"supplier-price-{uuid4().hex[:12]}",
    }
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, supplier_pricing_id: str, data: SupplierPricingUpdate
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, supplier_pricing_id)
    result = await db[COLLECTION].find_one_and_update(
        {"supplier_pricing_id": supplier_pricing_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, supplier_pricing_id: str) -> bool:
    result = await db[COLLECTION].delete_one(
        {"supplier_pricing_id": supplier_pricing_id}
    )
    return result.deleted_count == 1
