import re
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.facilities.models.industrialLine import (
    IndustrialLineCreate,
    IndustrialLineUpdate,
)

COLLECTION = "industrial_lines"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)  # shallow copy — never mutate Motor's result
    doc.pop("_id", None)
    doc["id"] = str(doc.get("id") or doc.get("line_id") or "")
    doc["type"] = doc.get("type") or "Batch"
    doc["status"] = (
        "Operational"
        if doc.get("status") == "Active"
        else doc.get("status", "Operational")
    )
    doc["takt_time"] = doc.get("takt_time") or 1.0
    doc["efficiency"] = (
        doc.get("efficiency") if doc.get("efficiency") is not None else 0
    )
    return doc


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


async def get_by_id(db: AsyncIOMotorDatabase, line_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": line_id})
    return _serialize(doc) if doc else None


async def get_by_plant(db: AsyncIOMotorDatabase, plant_id: str) -> list[dict]:
    plant_ids = {plant_id}
    plant = await db["industrial_plants"].find_one(
        {
            "$or": [
                {"id": plant_id},
                {"name": {"$regex": f"^{re.escape(plant_id)}$", "$options": "i"}},
            ]
        },
        {"id": 1, "name": 1},
    )
    if plant:
        if plant.get("id"):
            plant_ids.add(str(plant["id"]))
        if plant.get("name"):
            plant_ids.add(str(plant["name"]))

    cursor = db[COLLECTION].find({"plant_id": {"$in": list(plant_ids)}})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: IndustrialLineCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, line_id: str, data: IndustrialLineUpdate
) -> Optional[dict]:
    # exclude_unset so we never overwrite existing fields with None
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, line_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": line_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, line_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": line_id})
    return result.deleted_count == 1
