from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.procurement.models.purchase_orders import (
    PurchaseOrderCreate,
    PurchaseOrderUpdate,
)

COLLECTION = "purchase_orders"


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
    return [_serialize(doc) async for doc in cursor], total


async def get_by_po_number(db: AsyncIOMotorDatabase, po_number: str) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"po_number": po_number}))


async def get_by_supplier_id(db: AsyncIOMotorDatabase, supplier_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"supplier_id": supplier_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_buyer_id(db: AsyncIOMotorDatabase, buyer_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"buyer_id": buyer_id})
    return [_serialize(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: PurchaseOrderCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    po_number: str,
    data: PurchaseOrderUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_po_number(db, po_number)
    result = await db[COLLECTION].find_one_and_update(
        {"po_number": po_number},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, po_number: str) -> bool:
    result = await db[COLLECTION].delete_one({"po_number": po_number})
    return result.deleted_count == 1
