from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.floor_layout.models.buildings import BuildingCreate, BuildingUpdate

COLLECTION = "plant_locations"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query = {"type": "building"}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, building_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one(
        {"building_id": building_id, "type": "building"}
    )
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: BuildingCreate) -> dict:
    doc = data.model_dump()
    doc["type"] = "building"
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    building_id: str,
    data: BuildingUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, building_id)
    result = await db[COLLECTION].find_one_and_update(
        {"building_id": building_id, "type": "building"},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, building_id: str) -> bool:
    result = await db[COLLECTION].delete_one(
        {"building_id": building_id, "type": "building"}
    )
    return result.deleted_count == 1
