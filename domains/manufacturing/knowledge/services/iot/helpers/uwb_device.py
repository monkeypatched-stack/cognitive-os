from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.iot.models.uwb_device import UWBDeviceCreate, UWBDeviceUpdate

COLLECTION = "uwb_devices"


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
    if isinstance(value, dict):
        return {key: _prepare(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare(item) for item in value]
    return value


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, device_id: str) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"id": device_id}))


async def get_by_mac_address(
    db: AsyncIOMotorDatabase, mac_address: str
) -> Optional[dict]:
    return _serialize(
        await db[COLLECTION].find_one({"mac_address": mac_address.strip().upper()})
    )


async def get_by_serial_number(
    db: AsyncIOMotorDatabase, serial_number: str
) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"serial_number": serial_number}))


async def get_by_anchor_id(db: AsyncIOMotorDatabase, anchor_id: str) -> Optional[dict]:
    return _serialize(
        await db[COLLECTION].find_one({"anchor_config.anchor_id": anchor_id})
    )


async def get_by_active(db: AsyncIOMotorDatabase, active: bool) -> list[dict]:
    cursor = db[COLLECTION].find({"active": active})
    return [_serialize(doc) async for doc in cursor]


async def get_by_tag(db: AsyncIOMotorDatabase, tag: str) -> list[dict]:
    cursor = db[COLLECTION].find({"tags": tag})
    return [_serialize(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: UWBDeviceCreate) -> dict:
    doc = _prepare(data.model_dump(mode="python"))
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, device_id: str, data: UWBDeviceUpdate
) -> Optional[dict]:
    fields = _prepare(data.model_dump(mode="python", exclude_unset=True))
    if not fields:
        return await get_by_id(db, device_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": device_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, device_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": device_id})
    return result.deleted_count == 1
