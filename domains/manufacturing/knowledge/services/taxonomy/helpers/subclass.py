from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.taxonomy.models.subclass import (
    EquipmentSubClassCreate,
    EquipmentSubClassUpdate,
)

COLLECTION = "subclasses"


def _serialize(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    family_id: Optional[str] = None,
    class_id: Optional[str] = None,
    tag: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if family_id:
        query["family_id"] = family_id
    if class_id:
        query["class_id"] = class_id
    if tag:
        query["tags"] = tag

    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, subclass_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": subclass_id})
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: EquipmentSubClassCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, subclass_id: str, data: EquipmentSubClassUpdate
) -> Optional[dict]:
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_by_id(db, subclass_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": subclass_id}, {"$set": fields}, return_document=True
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, subclass_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": subclass_id})
    return result.deleted_count == 1
