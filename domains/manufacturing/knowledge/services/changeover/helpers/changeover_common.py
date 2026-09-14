from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument


def serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def dump(data) -> dict:
    return data.model_dump(mode="json", exclude_unset=True)


async def get_all(
    db: AsyncIOMotorDatabase,
    collection: str,
    page: int = 1,
    page_size: int = 20,
    query: Optional[dict] = None,
) -> tuple[list[dict], int]:
    query = query or {}
    total = await db[collection].count_documents(query)
    cursor = db[collection].find(query).skip((page - 1) * page_size).limit(page_size)
    return [serialize(d) async for d in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase, collection: str, record_id: str
) -> Optional[dict]:
    doc = await db[collection].find_one({"id": record_id})
    return serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, collection: str, data) -> dict:
    doc = data.model_dump(mode="json")
    await db[collection].insert_one(doc)
    return serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, collection: str, record_id: str, data
) -> Optional[dict]:
    fields = dump(data)
    if not fields:
        return await get_by_id(db, collection, record_id)
    result = await db[collection].find_one_and_update(
        {"id": record_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, collection: str, record_id: str) -> bool:
    result = await db[collection].delete_one({"id": record_id})
    return result.deleted_count == 1
