from datetime import time
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.shifts.models.shift_template import (
    ShiftTemplateCreate,
    ShiftTemplateResponse,
    ShiftTemplateUpdate,
)

COLLECTION = "shift_templates"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _dump(data) -> dict:
    doc = data.model_dump(mode="json", exclude_unset=True)
    doc.pop("net_work_minutes", None)
    return doc


def _parse_time(value: object, fallback: time) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            hour, minute, *_ = value.strip().split(":")
            return time(int(hour), int(minute))
        except (TypeError, ValueError):
            return fallback
    return fallback


def _normalize_shift_type(value: object, name: object = None) -> str:
    candidate = str(value or name or "").strip().lower()
    if "night" in candidate:
        return "night"
    if "afternoon" in candidate or "evening" in candidate:
        return "afternoon"
    if "split" in candidate:
        return "split"
    if "overtime" in candidate:
        return "overtime"
    return "morning"


def _normalize_days(value: object) -> list[str]:
    if isinstance(value, list) and value:
        return [str(item).strip().lower() for item in value if str(item).strip()]
    return ["monday", "tuesday", "wednesday", "thursday", "friday"]


def _normalize_shift_template_record(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None

    record = _serialize(doc)
    template_id = str(record.get("id") or record.get("template_id") or "SHIFT-TEMPLATE")
    record["id"] = template_id
    record["plant_id"] = str(
        record.get("plant_id")
        or record.get("factory_id")
        or record.get("site_id")
        or "UNKNOWN-PLANT"
    )
    record.setdefault("name", template_id)
    record["shift_type"] = _normalize_shift_type(
        record.get("shift_type"), record.get("name")
    )

    time_range = (
        record.get("time_range") if isinstance(record.get("time_range"), dict) else {}
    )
    start = _parse_time(time_range.get("start") or record.get("start_time"), time(6, 0))
    end = _parse_time(time_range.get("end") or record.get("end_time"), time(14, 0))
    if end <= start:
        end = time(23, 59)
    record["time_range"] = {"start": start, "end": end}
    record["days"] = _normalize_days(record.get("days"))
    if not isinstance(record.get("breaks"), list):
        record["breaks"] = []

    return ShiftTemplateResponse.model_validate(record).model_dump()


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_normalize_shift_template_record(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, template_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": template_id})
    return _normalize_shift_template_record(doc) if doc else None


async def get_by_factory(db: AsyncIOMotorDatabase, factory_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"factory_id": factory_id})
    return [_normalize_shift_template_record(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: ShiftTemplateCreate) -> dict:
    doc = _dump(data)
    await db[COLLECTION].insert_one(doc)
    return _normalize_shift_template_record(doc)


async def update(
    db: AsyncIOMotorDatabase,
    template_id: str,
    data: ShiftTemplateUpdate,
) -> Optional[dict]:
    fields = _dump(data)
    if not fields:
        return await get_by_id(db, template_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": template_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _normalize_shift_template_record(result) if result else None


async def delete(db: AsyncIOMotorDatabase, template_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": template_id})
    return result.deleted_count == 1
