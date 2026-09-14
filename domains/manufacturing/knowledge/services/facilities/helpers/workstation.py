import re
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.facilities.models.workstation import (
    WorkstationCreate,
    WorkstationResponse,
    WorkstationUpdate,
)

COLLECTION = "workstations"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _normalize_mode(value: object, operator: object = None) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "robotic": "Robotic",
            "automated": "Robotic",
            "auto": "Robotic",
            "manual": "Manual",
            "hybrid": "Hybrid",
            "semi-automated": "Hybrid",
            "semi automated": "Hybrid",
        }
        if normalized in aliases:
            return aliases[normalized]
    return "Manual" if operator else "Robotic"


def _normalize_status(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "active": "Active",
            "running": "Active",
            "online": "Active",
            "alert": "Alert",
            "warning": "Alert",
            "idle": "Idle",
            "offline": "Offline",
            "down": "Offline",
            "inactive": "Offline",
        }
        if normalized in aliases:
            return aliases[normalized]
    return "Active"


def _coerce_float(value: object, fallback: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return fallback
    return fallback


def _normalize_workstation_record(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None

    record = _serialize(doc)
    workstation_id = str(
        record.get("id")
        or record.get("workstation_id")
        or record.get("name")
        or "WORKSTATION"
    )
    record["id"] = workstation_id
    record.setdefault("name", workstation_id)

    operator = (
        record.get("operator") or record.get("operator_id") or record.get("worker_id")
    )
    mode = _normalize_mode(record.get("mode"), operator)
    if mode == "Robotic":
        operator = None
    record["mode"] = mode
    record["operator"] = operator
    record["status"] = _normalize_status(record.get("status"))

    target = _coerce_float(
        record.get("cycle_time_target")
        or record.get("target_cycle_time")
        or record.get("takt_time_seconds"),
        1.0,
    )
    if target <= 0:
        target = 1.0
    actual = _coerce_float(
        record.get("cycle_time_actual")
        or record.get("actual_cycle_time")
        or record.get("cycle_time_seconds"),
        0.0 if record["status"] == "Idle" else target,
    )
    if record["status"] == "Idle":
        actual = 0.0

    record["cycle_time_actual"] = max(actual, 0.0)
    record["cycle_time_target"] = target
    record["is_bottleneck"] = record["cycle_time_actual"] > record["cycle_time_target"]

    return WorkstationResponse(**record).model_dump()


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_normalize_workstation_record(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, workstation_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": workstation_id})
    return _normalize_workstation_record(doc) if doc else None


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
    stages = (
        await db["industrial_stages"]
        .find(
            {"line_id": {"$in": list(line_ids)}},
            {"id": 1, "name": 1},
        )
        .to_list(length=200)
    )
    stage_ids = {
        str(value)
        for stage in stages
        for value in (stage.get("id"), stage.get("name"))
        if value
    }
    query: dict = {"line_id": {"$in": list(line_ids)}}
    if stage_ids:
        query = {
            "$or": [
                {"line_id": {"$in": list(line_ids)}},
                {"stage_id": {"$in": list(stage_ids)}},
            ]
        }
    cursor = db[COLLECTION].find(query)
    return [_normalize_workstation_record(d) async for d in cursor]


async def get_by_stage_id(db: AsyncIOMotorDatabase, stage_id: str) -> list[dict]:
    stage_ids = {stage_id}
    stage = await db["industrial_stages"].find_one(
        {
            "$or": [
                {"id": stage_id},
                {"name": {"$regex": f"^{re.escape(stage_id)}$", "$options": "i"}},
            ]
        },
        {"id": 1, "name": 1},
    )
    if stage:
        if stage.get("id"):
            stage_ids.add(str(stage["id"]))
        if stage.get("name"):
            stage_ids.add(str(stage["name"]))
    cursor = db[COLLECTION].find({"stage_id": {"$in": list(stage_ids)}})
    return [_normalize_workstation_record(d) async for d in cursor]


async def get_bottlenecks(
    db: AsyncIOMotorDatabase, line_id: Optional[str] = None
) -> list[dict]:
    query: dict = {"is_bottleneck": True}
    if line_id:
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
        query["line_id"] = {"$in": list(line_ids)}
    cursor = db[COLLECTION].find(query)
    return [_normalize_workstation_record(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: WorkstationCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _normalize_workstation_record(doc)


async def update(
    db: AsyncIOMotorDatabase, workstation_id: str, data: WorkstationUpdate
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, workstation_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": workstation_id},
        {"$set": fields},
        return_document=True,
    )
    return _normalize_workstation_record(result) if result else None


async def delete(db: AsyncIOMotorDatabase, workstation_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": workstation_id})
    return result.deleted_count == 1
