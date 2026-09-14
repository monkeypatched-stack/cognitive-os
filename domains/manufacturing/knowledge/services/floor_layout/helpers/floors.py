from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.floor_layout.models.floors import FloorCreate, FloorUpdate

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
    query = {"type": "floor"}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, floor_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"floor_id": floor_id, "type": "floor"})
    return _serialize(doc) if doc else None


async def get_by_building(db: AsyncIOMotorDatabase, building_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"building_id": building_id, "type": "floor"})
    return [_serialize(d) async for d in cursor]


async def get_by_facility(db: AsyncIOMotorDatabase, facility_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"plant_id": facility_id, "type": "floor"})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: FloorCreate) -> dict:
    doc = data.model_dump()
    doc["type"] = "floor"
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    floor_id: str,
    data: FloorUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, floor_id)
    result = await db[COLLECTION].find_one_and_update(
        {"floor_id": floor_id, "type": "floor"},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, floor_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"floor_id": floor_id, "type": "floor"})
    return result.deleted_count == 1
