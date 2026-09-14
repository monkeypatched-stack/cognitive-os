from datetime import date, datetime, timezone
from typing import Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.auth.helpers.influx_store import write_websocket_event
from services.auth.helpers.nats_store import subject_for_event_type
from services.common.embeddings import build_embedding
from services.common.neo4j_mirror import mirror_document, safe_mirror
from services.pm.models.checklists import (
    ChecklistCreate,
    ChecklistItemCreate,
    ChecklistItemUpdate,
    ChecklistUpdate,
)

COLLECTION = "checklists"
HVAC_TEMPERATURE_WORK_ORDER_ID = "WO-HVAC-TEMP-THRESHOLD-001"
HVAC_TEMPERATURE_WORKFLOW_ID = "WF-HVAC-TEMP-THRESHOLD-001"


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
        return {k: _prepare(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_prepare(v) for v in value]
    return value


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, checklist_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"checklist_id": checklist_id})
    return _serialize(doc)


async def get_by_owner(db: AsyncIOMotorDatabase, owner_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"owner_id": owner_id})
    return [_serialize(d) async for d in cursor]


async def get_by_work_order(db: AsyncIOMotorDatabase, work_order_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"work_order_id": work_order_id})
    return [_serialize(d) async for d in cursor]


async def get_by_process_definition(
    db: AsyncIOMotorDatabase, process_definition_id: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"process_definition_id": process_definition_id})
    return [_serialize(d) async for d in cursor]


async def get_by_tag(db: AsyncIOMotorDatabase, tag: str) -> list[dict]:
    cursor = db[COLLECTION].find({"tags": tag})
    return [_serialize(d) async for d in cursor]


async def get_by_stage(db: AsyncIOMotorDatabase, stage_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"items.stage_id": stage_id})
    return [_serialize(d) async for d in cursor]


async def get_open(db: AsyncIOMotorDatabase) -> list[dict]:
    cursor = db[COLLECTION].find({"is_archived": False, "items.is_completed": False})
    return [_serialize(d) async for d in cursor]


async def get_completed(db: AsyncIOMotorDatabase) -> list[dict]:
    cursor = db[COLLECTION].find(
        {
            "items": {"$not": {"$elemMatch": {"is_completed": False}}},
            "is_archived": False,
        }
    )
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: ChecklistCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    checklist_id: str,
    data: ChecklistUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, checklist_id)
    result = await db[COLLECTION].find_one_and_update(
        {"checklist_id": checklist_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, checklist_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"checklist_id": checklist_id})
    return result.deleted_count == 1


async def add_item(
    db: AsyncIOMotorDatabase,
    checklist_id: str,
    item: ChecklistItemCreate,
) -> Optional[dict]:
    result = await db[COLLECTION].find_one_and_update(
        {"checklist_id": checklist_id},
        {
            "$push": {"items": _prepare(item.model_dump())},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
        return_document=True,
    )
    return _serialize(result)


async def update_item(
    db: AsyncIOMotorDatabase,
    checklist_id: str,
    item_id: str,
    data: ChecklistItemUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, checklist_id)
    updates = {f"items.$.{key}": value for key, value in fields.items()}
    updates["updated_at"] = datetime.now(timezone.utc)
    result = await db[COLLECTION].find_one_and_update(
        {"checklist_id": checklist_id, "items.item_id": item_id},
        {"$set": updates},
        return_document=True,
    )
    return _serialize(result)


async def set_item_completed(
    db: AsyncIOMotorDatabase,
    checklist_id: str,
    item_id: str,
    is_completed: bool,
) -> Optional[dict]:
    now = datetime.now(timezone.utc)
    before = await db[COLLECTION].find_one(
        {"checklist_id": checklist_id, "items.item_id": item_id},
        {
            "items.$": 1,
            "checklist_id": 1,
            "title": 1,
            "work_order_id": 1,
            "process_definition_id": 1,
        },
    )
    was_completed = (
        bool((before or {}).get("items", [{}])[0].get("is_completed"))
        if before
        else False
    )
    updates = {
        "items.$.is_completed": is_completed,
        "items.$.completed_at": now if is_completed else None,
        "items.$.updated_at": now,
        "updated_at": now,
    }
    result = await db[COLLECTION].find_one_and_update(
        {"checklist_id": checklist_id, "items.item_id": item_id},
        {"$set": updates},
        return_document=True,
    )
    serialized = _serialize(result)
    if serialized and is_completed and not was_completed:
        await _write_checklist_item_completed_event(serialized, item_id, now)
    return serialized


async def remove_item(
    db: AsyncIOMotorDatabase,
    checklist_id: str,
    item_id: str,
) -> Optional[dict]:
    result = await db[COLLECTION].find_one_and_update(
        {"checklist_id": checklist_id, "items.item_id": item_id},
        {
            "$pull": {"items": {"item_id": item_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
        return_document=True,
    )
    return _serialize(result)


async def ensure_hvac_temperature_threshold_checklist(
    db: AsyncIOMotorDatabase,
    sensor: dict,
    reading: dict,
) -> dict:
    sensor_id = str(sensor.get("sensor_id") or "unknown")
    sensor_token = sensor_id.replace(".", "-").replace(" ", "-")
    checklist_id = f"CHK-HVAC-TEMP-THRESHOLD-{sensor_token}"
    existing = await get_by_id(db, checklist_id)
    if existing:
        return existing

    now = datetime.now(timezone.utc)
    sensor_name = str(sensor.get("name") or sensor_id)
    high_threshold = sensor.get("alert_threshold_high")
    value = reading.get("value", reading.get("raw_value"))
    unit = reading.get("unit") or sensor.get("measurement_unit") or "C"
    document = {
        "checklist_id": checklist_id,
        "work_order_id": HVAC_TEMPERATURE_WORK_ORDER_ID,
        "title": f"HVAC temperature threshold response for {sensor_name}",
        "description": (
            f"Auto-created checklist because {sensor_name} reported {value} {unit}"
            f" against high threshold {high_threshold} {unit}."
        ),
        "owner_id": "FACILITIES-ENGINEERING",
        "process_definition_id": HVAC_TEMPERATURE_WORKFLOW_ID,
        "tags": ["hvac", "temperature", "threshold", "auto-created", sensor_id],
        "items": [
            _hvac_checklist_item(
                checklist_id,
                "001",
                "Confirm latest sensor reading, timestamp, quality, and threshold.",
                f"Record {sensor_name} value {value} {unit}, threshold {high_threshold} {unit}, and reading time {reading.get('time') or reading.get('timestamp')}.",
                "FACILITIES-ENGINEERING",
                now,
            ),
            _hvac_checklist_item(
                checklist_id,
                "002",
                "Adjust HVAC setpoint within approved range.",
                "Apply approved HVAC setpoint correction and document before/after setpoints.",
                "FACILITIES-ENGINEERING",
                now,
            ),
            _hvac_checklist_item(
                checklist_id,
                "003",
                "Monitor recovery below temperature threshold.",
                "Confirm readings are back below the configured threshold and stable during the observation window.",
                "FACILITIES-ENGINEERING",
                now,
            ),
            _hvac_checklist_item(
                checklist_id,
                "004",
                "Document QA/facilities release decision.",
                "Record excursion impact, corrective action, and QA/facilities release or hold decision.",
                "QUALITY-ASSURANCE",
                now,
            ),
        ],
        "is_archived": False,
        "created_at": now,
        "updated_at": now,
    }
    document["embedding"] = build_embedding(COLLECTION, document)
    await db[COLLECTION].insert_one(_prepare(document))
    await safe_mirror(
        mirror_document(
            COLLECTION, document, "ensure_hvac_temperature_threshold_checklist"
        )
    )
    return _serialize(document)


def _hvac_checklist_item(
    checklist_id: str,
    suffix: str,
    title: str,
    description: str,
    assigned_to: str,
    now: datetime,
) -> dict:
    return {
        "item_id": f"{checklist_id}-{suffix}",
        "work_order_id": HVAC_TEMPERATURE_WORK_ORDER_ID,
        "title": title,
        "description": description,
        "assigned_to": assigned_to,
        "due_at": now,
        "is_completed": False,
        "completed_at": None,
        "created_at": now,
        "updated_at": now,
    }


async def _write_checklist_item_completed_event(
    checklist: dict, item_id: str, completed_at: datetime
) -> None:
    item = next(
        (
            entry
            for entry in checklist.get("items", [])
            if entry.get("item_id") == item_id
        ),
        {},
    )
    payload = {
        "id": f"EVT-CHECKLIST-COMPLETE-{item_id}",
        "type": "Checklist Item Completed",
        "category": "Checklist Completion",
        "checklist_id": checklist.get("checklist_id"),
        "checklist_title": checklist.get("title"),
        "item_id": item_id,
        "item_title": item.get("title"),
        "work_order_id": checklist.get("work_order_id") or item.get("work_order_id"),
        "process_definition_id": checklist.get("process_definition_id"),
        "assigned_to": item.get("assigned_to"),
        "completed_at": completed_at.isoformat(),
        "details": f"Checklist item completed: {item.get('title') or item_id}",
    }
    event = {
        "event_id": str(uuid4()),
        "received_at": completed_at.isoformat(),
        "source": "pm.checklists",
        "event_type": "checklist-item-completed",
        "payload_type": "json",
        "payload": payload,
    }
    await write_websocket_event(
        event, subject_for_event_type("checklist-item-completed")
    )
