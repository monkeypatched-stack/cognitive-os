from typing import Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.customers.models.customer_details import (
    CustomerDetailsCreate,
    CustomerDetailsUpdate,
)

COLLECTION = "customer_details"


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


async def get_by_customer_id(
    db: AsyncIOMotorDatabase,
    customer_id: str,
) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"customer_id": customer_id}))


async def create(db: AsyncIOMotorDatabase, data: CustomerDetailsCreate) -> dict:
    doc = data.model_dump()
    if not doc.get("customer_id"):
        doc["customer_id"] = f"customer-{uuid4().hex[:12]}"
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    customer_id: str,
    data: CustomerDetailsUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_customer_id(db, customer_id)
    result = await db[COLLECTION].find_one_and_update(
        {"customer_id": customer_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, customer_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"customer_id": customer_id})
    return result.deleted_count == 1
