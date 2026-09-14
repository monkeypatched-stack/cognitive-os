from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.assets.models.device import DeviceCreate, DeviceUpdate

COLLECTION = "devices"


def _serialize(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, device_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": device_id})
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: DeviceCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, device_id: str, data: DeviceUpdate
) -> Optional[dict]:
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_by_id(db, device_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": device_id}, {"$set": fields}, return_document=True
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, device_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": device_id})
    return result.deleted_count == 1


async def get_by_user(db: AsyncIOMotorDatabase, user_id: str) -> list[dict]:
    if not user_id:
        return []

    cursor = db[COLLECTION].find(
        {
            "$or": [
                {"user_id": user_id},  # fallback (if nested ever used)
            ]
        }
    )

    return [_serialize(d) async for d in cursor]
