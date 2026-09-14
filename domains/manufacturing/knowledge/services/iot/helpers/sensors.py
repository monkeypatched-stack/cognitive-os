from datetime import date, datetime
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.iot.models.sensors import SensorCreate, SensorUpdate

COLLECTION = "sensors"


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


async def get_by_id(db: AsyncIOMotorDatabase, sensor_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"sensor_id": sensor_id})
    return _serialize(doc) if doc else None


async def get_by_machine(db: AsyncIOMotorDatabase, machine_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"machine_id": machine_id})
    return [_serialize(d) async for d in cursor]


async def get_by_equipment_id(
    db: AsyncIOMotorDatabase, equipment_id: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"equipment_id": equipment_id})
    return [_serialize(d) async for d in cursor]


async def get_by_edge_server(
    db: AsyncIOMotorDatabase, edge_server_id: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"edge_server_id": edge_server_id})
    return [_serialize(d) async for d in cursor]


async def get_by_facility(db: AsyncIOMotorDatabase, facility: str) -> list[dict]:
    cursor = db[COLLECTION].find({"facility": facility})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: SensorCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, sensor_id: str, data: SensorUpdate
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, sensor_id)
    result = await db[COLLECTION].find_one_and_update(
        {"sensor_id": sensor_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, sensor_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"sensor_id": sensor_id})
    return result.deleted_count == 1
