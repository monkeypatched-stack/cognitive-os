from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.floor_layout.models.rooms import RoomCreate, RoomUpdate

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
    query = {"type": "room"}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, room_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"room_id": room_id, "type": "room"})
    return _serialize(doc) if doc else None


async def get_by_floor(db: AsyncIOMotorDatabase, floor_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"floor_id": floor_id, "type": "room"})
    return [_serialize(d) async for d in cursor]


async def get_by_building(db: AsyncIOMotorDatabase, building_id: str) -> list[dict]:
    floors = (
        await db[COLLECTION]
        .find({"building_id": building_id, "type": "floor"})
        .to_list(None)
    )
    floor_ids = [floor["floor_id"] for floor in floors]
    cursor = db[COLLECTION].find({"floor_id": {"$in": floor_ids}, "type": "room"})
    return [_serialize(d) async for d in cursor]


async def get_by_facility(db: AsyncIOMotorDatabase, facility_id: str) -> list[dict]:
    buildings = (
        await db[COLLECTION]
        .find({"plant_id": facility_id, "type": "building"})
        .to_list(None)
    )
    building_ids = [building["building_id"] for building in buildings]
    floors = (
        await db[COLLECTION]
        .find({"building_id": {"$in": building_ids}, "type": "floor"})
        .to_list(None)
    )
    floor_ids = [floor["floor_id"] for floor in floors]
    cursor = db[COLLECTION].find({"floor_id": {"$in": floor_ids}, "type": "room"})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: RoomCreate) -> dict:
    doc = data.model_dump()
    doc["type"] = "room"
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    room_id: str,
    data: RoomUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, room_id)
    result = await db[COLLECTION].find_one_and_update(
        {"room_id": room_id, "type": "room"},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, room_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"room_id": room_id, "type": "room"})
    return result.deleted_count == 1
