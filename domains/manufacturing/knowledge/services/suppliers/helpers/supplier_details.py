from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.suppliers.models.supplier_details import (
    SupplierDetailsCreate,
    SupplierDetailsUpdate,
)

COLLECTION = "supplier_details"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_supplier_id(
    db: AsyncIOMotorDatabase, supplier_id: str
) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"supplier_id": supplier_id}))


async def create(db: AsyncIOMotorDatabase, data: SupplierDetailsCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    supplier_id: str,
    data: SupplierDetailsUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_supplier_id(db, supplier_id)
    result = await db[COLLECTION].find_one_and_update(
        {"supplier_id": supplier_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, supplier_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"supplier_id": supplier_id})
    return result.deleted_count == 1
