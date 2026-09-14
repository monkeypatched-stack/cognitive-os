from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.floor_layout.models.bays import BayCreate, BayUpdate

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
    query = {"type": "bay"}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, bay_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"bay_id": bay_id, "type": "bay"})
    return _serialize(doc) if doc else None


async def get_by_room(db: AsyncIOMotorDatabase, room_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"room_id": room_id, "type": "bay"})
    return [_serialize(d) async for d in cursor]


async def get_by_machine(db: AsyncIOMotorDatabase, machine_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"machine_id": machine_id, "type": "bay"})
    return [_serialize(d) async for d in cursor]


async def get_by_floor(db: AsyncIOMotorDatabase, floor_id: str) -> list[dict]:
    rooms = (
        await db[COLLECTION].find({"floor_id": floor_id, "type": "room"}).to_list(None)
    )
    room_ids = [r["room_id"] for r in rooms]
    cursor = db[COLLECTION].find({"room_id": {"$in": room_ids}, "type": "bay"})
    return [_serialize(d) async for d in cursor]


async def get_by_building(db: AsyncIOMotorDatabase, building_id: str) -> list[dict]:
    floors = (
        await db[COLLECTION]
        .find({"building_id": building_id, "type": "floor"})
        .to_list(None)
    )
    floor_ids = [f["floor_id"] for f in floors]
    rooms = (
        await db[COLLECTION]
        .find({"floor_id": {"$in": floor_ids}, "type": "room"})
        .to_list(None)
    )
    room_ids = [r["room_id"] for r in rooms]
    cursor = db[COLLECTION].find({"room_id": {"$in": room_ids}, "type": "bay"})
    return [_serialize(d) async for d in cursor]


async def get_by_facility(db: AsyncIOMotorDatabase, facility_id: str) -> list[dict]:
    buildings = (
        await db[COLLECTION]
        .find({"plant_id": facility_id, "type": "building"})
        .to_list(None)
    )
    building_ids = [b["building_id"] for b in buildings]
    floors = (
        await db[COLLECTION]
        .find({"building_id": {"$in": building_ids}, "type": "floor"})
        .to_list(None)
    )
    floor_ids = [f["floor_id"] for f in floors]
    rooms = (
        await db[COLLECTION]
        .find({"floor_id": {"$in": floor_ids}, "type": "room"})
        .to_list(None)
    )
    room_ids = [r["room_id"] for r in rooms]
    cursor = db[COLLECTION].find({"room_id": {"$in": room_ids}, "type": "bay"})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: BayCreate) -> dict:
    doc = data.model_dump()
    doc["type"] = "bay"
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    bay_id: str,
    data: BayUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, bay_id)
    result = await db[COLLECTION].find_one_and_update(
        {"bay_id": bay_id, "type": "bay"},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, bay_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"bay_id": bay_id, "type": "bay"})
    return result.deleted_count == 1
