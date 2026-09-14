from datetime import date, datetime
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.iot.models.servers import EdgeServerCreate, EdgeServerUpdate

COLLECTION = "edge_servers"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _prepare(doc: dict) -> dict:
    """Convert date → datetime so BSON can encode them."""
    return {
        k: (
            datetime(v.year, v.month, v.day)
            if isinstance(v, date) and not isinstance(v, datetime)
            else v
        )
        for k, v in doc.items()
    }


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    results = [_serialize(d) async for d in cursor]
    return results, total


async def get_by_id(db: AsyncIOMotorDatabase, server_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"server_id": server_id})
    return _serialize(doc) if doc else None


async def get_by_facility(db: AsyncIOMotorDatabase, facility: str) -> list[dict]:
    cursor = db[COLLECTION].find({"facility": facility})
    return [_serialize(d) async for d in cursor]


async def get_by_parent(db: AsyncIOMotorDatabase, parent_server_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"parent_server_id": parent_server_id})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: EdgeServerCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, server_id: str, data: EdgeServerUpdate
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, server_id)
    result = await db[COLLECTION].find_one_and_update(
        {"server_id": server_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, server_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"server_id": server_id})
    return result.deleted_count == 1
