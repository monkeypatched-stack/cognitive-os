import re
from datetime import date, datetime
from typing import Optional
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.assets.models.equipment import (
    PharmaceuticalEquipmentCreate,
    PharmaceuticalEquipmentUpdate,
)
from bson.errors import InvalidDocument

COLLECTION = "pharmaceutical_equipment"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    equipment_id = str(doc.get("equipment_id") or doc.get("id") or "")
    doc["equipment_id"] = equipment_id
    doc["name"] = doc.get("name") or equipment_id or "Equipment"
    doc["category"] = doc.get("category") or (
        "Machine" if doc.get("equipment_type") else "Instrument"
    )
    doc["manufacturer"] = doc.get("manufacturer") or "Unknown"
    if doc.get("status") == "Operational":
        doc["status"] = "active"
    doc["status"] = doc.get("status") or "active"
    doc["requires_calibration"] = (
        bool(doc.get("requires_calibration"))
        if doc.get("requires_calibration") is not None
        else False
    )
    for date_field in ("purchase_date", "expiry_date"):
        if date_field in doc and hasattr(doc[date_field], "date"):
            doc[date_field] = doc[date_field].date()
    return doc


def _prepare(doc: dict) -> dict:
    """Convert date -> datetime so BSON can encode it."""
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


async def get_by_id(db: AsyncIOMotorDatabase, equipment_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"equipment_id": equipment_id})
    return _serialize(doc) if doc else None


async def get_by_workstation_id(
    db: AsyncIOMotorDatabase, workstation_id: str
) -> list[dict]:
    workstation_ids = {workstation_id}
    workstation = await db["workstations"].find_one(
        {
            "$or": [
                {"id": workstation_id},
                {"name": {"$regex": f"^{re.escape(workstation_id)}$", "$options": "i"}},
            ]
        },
        {"id": 1, "name": 1, "plant_id": 1, "line_id": 1, "stage_id": 1},
    )
    if workstation:
        if workstation.get("id"):
            workstation_ids.add(str(workstation["id"]))
        if workstation.get("name"):
            workstation_ids.add(str(workstation["name"]))
    cursor = db[COLLECTION].find({"workstation_id": {"$in": list(workstation_ids)}})
    results = [_serialize(d) async for d in cursor]
    if results or not workstation:
        return results

    plant_id = await _plant_id_for_workstation(db, workstation)
    if not plant_id:
        return results
    valid_workstation_ids = await _valid_workstation_ids(db)
    fallback_query = {
        "plant_id": plant_id,
        "$or": [
            {"workstation_id": {"$exists": False}},
            {"workstation_id": None},
            {"workstation_id": {"$nin": valid_workstation_ids}},
        ],
    }
    cursor = db[COLLECTION].find(fallback_query)
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


async def _plant_id_for_workstation(
    db: AsyncIOMotorDatabase, workstation: dict
) -> str | None:
    if workstation.get("plant_id"):
        return str(workstation["plant_id"])
    stage_id = workstation.get("stage_id")
    if stage_id:
        stage = await db["industrial_stages"].find_one(
            {"id": stage_id}, {"plant_id": 1, "line_id": 1}
        )
        if stage and stage.get("plant_id"):
            return str(stage["plant_id"])
        if stage and stage.get("line_id"):
            line = await db["industrial_lines"].find_one(
                {"id": stage["line_id"]}, {"plant_id": 1}
            )
            if line and line.get("plant_id"):
                return str(line["plant_id"])
    line_id = workstation.get("line_id")
    if line_id:
        line = await db["industrial_lines"].find_one({"id": line_id}, {"plant_id": 1})
        if line and line.get("plant_id"):
            return str(line["plant_id"])
    return None


async def _valid_workstation_ids(db: AsyncIOMotorDatabase) -> list[str]:
    workstations = (
        await db["workstations"].find({}, {"id": 1, "name": 1}).to_list(length=1000)
    )
    return [
        str(value)
        for workstation in workstations
        for value in (workstation.get("id"), workstation.get("name"))
        if value
    ]


async def get_by_location(db: AsyncIOMotorDatabase, location: str) -> list[dict]:
    cursor = db[COLLECTION].find({"location": location})
    return [_serialize(d) async for d in cursor]


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    cursor = db[COLLECTION].find({"status": status})
    return [_serialize(d) async for d in cursor]


async def get_by_category(db: AsyncIOMotorDatabase, category: str) -> list[dict]:
    cursor = db[COLLECTION].find({"category": category})
    return [_serialize(d) async for d in cursor]


async def get_by_assigned_to(db: AsyncIOMotorDatabase, assigned_to: str) -> list[dict]:
    cursor = db[COLLECTION].find({"assigned_to": assigned_to})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: PharmaceuticalEquipmentCreate) -> dict:
    doc = _prepare(data.model_dump())
    try:
        await db[COLLECTION].insert_one(doc)
    except InvalidDocument as e:
        raise HTTPException(status_code=422, detail=f"Unprocessable document: {e}")
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, equipment_id: str, data: PharmaceuticalEquipmentUpdate
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, equipment_id)
    result = await db[COLLECTION].find_one_and_update(
        {"equipment_id": equipment_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, equipment_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"equipment_id": equipment_id})
    return result.deleted_count == 1
