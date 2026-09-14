from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.shipping.models.vehicle import VehicleCreate, VehicleUpdate, utc_now

COLLECTION = "vehicles"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _prepare(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, tuple):
        return [_prepare(item) for item in value]
    if isinstance(value, dict):
        return {key: _prepare(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare(item) for item in value]
    return value


async def get_all(
    db: AsyncIOMotorDatabase, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, vehicle_id: str) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"id": vehicle_id}))


async def get_by_carrier(db: AsyncIOMotorDatabase, carrier_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"carrier_id": carrier_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_registration_plate(
    db: AsyncIOMotorDatabase, registration_plate: str
) -> Optional[dict]:
    return _serialize(
        await db[COLLECTION].find_one({"registration_plate": registration_plate})
    )


async def get_by_active(db: AsyncIOMotorDatabase, active: bool) -> list[dict]:
    cursor = db[COLLECTION].find({"active": active})
    return [_serialize(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: VehicleCreate) -> dict:
    doc = _prepare(data.model_dump(mode="python"))
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, vehicle_id: str, data: VehicleUpdate
) -> Optional[dict]:
    fields = _prepare(data.model_dump(mode="python", exclude_unset=True))
    if not fields:
        return await get_by_id(db, vehicle_id)
    fields["updated_at"] = utc_now()
    result = await db[COLLECTION].find_one_and_update(
        {"id": vehicle_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, vehicle_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": vehicle_id})
    return result.deleted_count == 1
