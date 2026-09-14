from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.utils import _prepare, _serialize
from services.assets.models.instruments import InstrumentCreate, InstrumentUpdate

COLLECTION = "instruments"


def _normalize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    category: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if category:
        query["category"] = category
    if status:
        query["status"] = status
    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"instrument_id": {"$regex": q, "$options": "i"}},
            {"manufacturer": {"$regex": q, "$options": "i"}},
        ]
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_normalize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, instrument_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"instrument_id": instrument_id})
    return _normalize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: InstrumentCreate) -> dict:
    doc = data.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    doc["updated_at"] = datetime.now(timezone.utc)
    await db[COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def update(
    db: AsyncIOMotorDatabase, instrument_id: str, data: InstrumentUpdate
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, instrument_id)
    fields["updated_at"] = datetime.now(timezone.utc)
    result = await db[COLLECTION].find_one_and_update(
        {"instrument_id": instrument_id},
        {"$set": fields},
        return_document=True,
    )
    return _normalize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, instrument_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"instrument_id": instrument_id})
    return result.deleted_count == 1


from datetime import datetime, timezone
