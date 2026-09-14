import re
from datetime import date, datetime
from typing import Optional
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.facilities.models.stages import (
    IndustrialStageCreate,
    IndustrialStageUpdate,
)
from bson.errors import InvalidDocument

COLLECTION = "industrial_stages"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    if "last_pm" in doc and hasattr(doc["last_pm"], "date"):
        doc["last_pm"] = doc["last_pm"].date()
    return doc


def _prepare(doc: dict) -> dict:
    return {
        k: (
            datetime.combine(v, datetime.min.time())
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
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, stage_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": stage_id})
    return _serialize(doc) if doc else None


async def get_by_line(db: AsyncIOMotorDatabase, line_id: str) -> list[dict]:
    line_ids = {line_id}
    line = await db["industrial_lines"].find_one(
        {
            "$or": [
                {"id": line_id},
                {"name": {"$regex": f"^{re.escape(line_id)}$", "$options": "i"}},
            ]
        },
        {"id": 1, "name": 1},
    )
    if line:
        if line.get("id"):
            line_ids.add(str(line["id"]))
        if line.get("name"):
            line_ids.add(str(line["name"]))
    cursor = db[COLLECTION].find({"line_id": {"$in": list(line_ids)}})
    return [_serialize(d) async for d in cursor]


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


async def get_by_circuit(db: AsyncIOMotorDatabase, circuit_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"circuit_id": circuit_id})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: IndustrialStageCreate) -> dict:
    doc = _prepare(data.model_dump())
    try:
        await db[COLLECTION].insert_one(doc)
    except InvalidDocument as e:
        raise HTTPException(status_code=422, detail=f"Unprocessable document: {e}")
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, stage_id: str, data: IndustrialStageUpdate
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, stage_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": stage_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, stage_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": stage_id})
    return result.deleted_count == 1
