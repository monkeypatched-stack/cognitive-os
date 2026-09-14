from datetime import date, datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.assets.models.plc import PLCResponse, PLCCreate, PLCUpdate, utc_now

COLLECTION = "plcs"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _prepare(value):
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


def _normalize_status(value: object, is_online: object = None) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "active": "online",
            "running": "online",
            "online": "online",
            "inactive": "offline",
            "offline": "offline",
            "down": "offline",
            "fault": "faulted",
            "faulted": "faulted",
            "maintenance": "maintenance",
            "unknown": "unknown",
        }
        if normalized in aliases:
            return aliases[normalized]
    if is_online is True:
        return "online"
    if is_online is False:
        return "offline"
    return "unknown"


def _normalize_cpu(value: object, record: dict) -> dict:
    cpu = dict(value) if isinstance(value, dict) else {}
    cpu.setdefault("manufacturer", record.get("manufacturer") or record.get("vendor"))
    cpu.setdefault(
        "model", record.get("cpu_model") or record.get("model") or "Unknown CPU"
    )
    cpu.setdefault("serial_number", record.get("serial_number"))
    cpu.setdefault("firmware_version", record.get("firmware_version"))
    return cpu


def _normalize_plc_record(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None

    record = _serialize(doc)
    plc_id = str(
        record.get("plc_id") or record.get("id") or record.get("asset_tag") or "PLC"
    )
    record["plc_id"] = plc_id
    record.setdefault("name", plc_id)
    record["is_online"] = (
        bool(record.get("is_online"))
        if record.get("is_online") is not None
        else _normalize_status(record.get("status")) == "online"
    )
    record["status"] = _normalize_status(record.get("status"), record.get("is_online"))
    record["cpu"] = _normalize_cpu(record.get("cpu"), record)

    for list_field in (
        "io_modules",
        "programs",
        "tags",
        "communication_ports",
        "connected_devices",
        "diagnostic_events",
    ):
        if not isinstance(record.get(list_field), list):
            record[list_field] = []

    return PLCResponse.model_validate(record).model_dump()


async def get_all(
    db: AsyncIOMotorDatabase, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_normalize_plc_record(doc) async for doc in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, plc_id: str) -> Optional[dict]:
    return _normalize_plc_record(await db[COLLECTION].find_one({"plc_id": plc_id}))


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    cursor = db[COLLECTION].find({"status": status})
    return [_normalize_plc_record(doc) async for doc in cursor]


async def get_by_online(db: AsyncIOMotorDatabase, is_online: bool) -> list[dict]:
    cursor = db[COLLECTION].find({"is_online": is_online})
    return [_normalize_plc_record(doc) async for doc in cursor]


async def get_by_plant_area(db: AsyncIOMotorDatabase, plant_area: str) -> list[dict]:
    cursor = db[COLLECTION].find({"plant_area": plant_area})
    return [_normalize_plc_record(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: PLCCreate) -> dict:
    doc = _prepare(data.model_dump(mode="python"))
    await db[COLLECTION].insert_one(doc)
    return _normalize_plc_record(doc)


async def update(
    db: AsyncIOMotorDatabase, plc_id: str, data: PLCUpdate
) -> Optional[dict]:
    fields = _prepare(data.model_dump(mode="python", exclude_unset=True))
    if not fields:
        return await get_by_id(db, plc_id)
    fields["updated_at"] = utc_now()
    result = await db[COLLECTION].find_one_and_update(
        {"plc_id": plc_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _normalize_plc_record(result)


async def delete(db: AsyncIOMotorDatabase, plc_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"plc_id": plc_id})
    return result.deleted_count == 1
