from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.taxonomy.models.family import FamilyCreate, FamilyUpdate

COLLECTION = "families"


def _serialize(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    tag: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if tag:
        query["tags"] = tag

    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, family_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": family_id})
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: FamilyCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, family_id: str, data: FamilyUpdate
) -> Optional[dict]:
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_by_id(db, family_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": family_id}, {"$set": fields}, return_document=True
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, family_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": family_id})
    return result.deleted_count == 1
