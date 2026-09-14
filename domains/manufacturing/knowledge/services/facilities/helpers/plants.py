from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.facilities.models.industrialPlant import (
    IndustrialPlantCreate,
    IndustrialPlantUpdate,
)

COLLECTION = "industrial_plants"


def _serialize(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    plant_type: Optional[str] = None,
    country: Optional[str] = None,
    timezone: Optional[str] = None,
    manager: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if status:
        query["status"] = status
    if plant_type:
        query["type"] = plant_type
    if country:
        query["country"] = country
    if timezone:
        query["timezone"] = timezone
    if manager:
        query["manager"] = manager

    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, plant_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": plant_id})
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: IndustrialPlantCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, plant_id: str, data: IndustrialPlantUpdate
) -> Optional[dict]:
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_by_id(db, plant_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": plant_id}, {"$set": fields}, return_document=True
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, plant_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": plant_id})
    return result.deleted_count == 1
