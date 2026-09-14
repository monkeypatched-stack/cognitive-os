"""Cleaning records CRUD — reads/writes from InfluxDB cleaning_log measurement."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.config import settings
from services.pm.models.cleaning import CleaningRecordCreate, CleaningRecordUpdate

logger = logging.getLogger(__name__)

COLLECTION = "cleaning_log"

CLEANING_TYPE_MAP = {
    "SOP-Based": "Routine",
    "CIP": "Routine",
    "COP": "COP",
    "Manual": "Routine",
    "Deep Clean": "Deep Clean",
    "Post-Production": "Post-Production",
    "Pre-Production": "Pre-Production",
    "Changeover": "Changeover",
    "Validation": "Validation",
    "Sanitization": "Sanitization",
}

CLEANING_METHOD_MAP = {
    "CIP": "CIP",
    "COP": "COP",
    "SOP-Based": "Manual",
    "Manual": "Manual",
    "Automated": "Automated",
    "Spray Wash": "Spray Wash",
    "Ultrasonic": "Ultrasonic",
}

STATUS_MAP = {
    "Pending": "Scheduled",
    "Scheduled": "Scheduled",
    "In Progress": "In Progress",
    "Completed": "Completed",
    "Verified": "Verified",
    "Failed": "Failed",
    "Cancelled": "Cancelled",
    "Pre-Production": "Pre-Production",
    "Post-Production": "Post-Production",
}


def _escape_tag(value: str) -> str:
    return str(value).replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def _influx_write(lines: list[str]) -> None:
    query = urlencode({"db": settings.INFLUXDB_BUCKET})
    url = f"{settings.INFLUXDB_URL.rstrip('/')}/api/v3/write_lp?{query}"
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if settings.INFLUXDB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.INFLUXDB_TOKEN}"
    body = "\n".join(lines)
    request = Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    with urlopen(request, timeout=10) as response:
        response.read()


def _influx_query(sql: str) -> list[dict]:
    params = urlencode({"db": settings.INFLUXDB_BUCKET, "q": sql})
    url = f"{settings.INFLUXDB_URL.rstrip('/')}/api/v3/query_sql?{params}"
    headers = {"Content-Type": "application/json"}
    if settings.INFLUXDB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.INFLUXDB_TOKEN}"
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _row_to_dict(row: dict) -> dict:
    raw_clean_type = row.get("clean_type", row.get("cleaning_type", "Routine"))
    cleaning_type = CLEANING_TYPE_MAP.get(raw_clean_type, "Routine")
    raw_method = row.get("cleaning_method", "")
    cleaning_method = (
        CLEANING_METHOD_MAP.get(raw_method, "Manual") if raw_method else "Manual"
    )
    machine_id = row.get("machine_id", "")
    record_id = row.get("record_id", f"CLN-{machine_id[-8:]}" if machine_id else "")
    raw_status = row.get("status", "Scheduled")
    status = STATUS_MAP.get(raw_status, "Scheduled")
    return {
        "record_id": record_id,
        "equipment_id": machine_id,
        "machine_id": machine_id,
        "work_order_id": row.get("work_order_id", None),
        "batch_execution_record_id": row.get("batch_execution_record_id", None),
        "batch_id": row.get("batch_id", None),
        "process_definition_id": row.get("process_definition_id", None),
        "process_step_id": row.get("process_step_id", None),
        "instruction_template_id": row.get("instruction_template_id", None),
        "instruction_step_sequence": row.get("instruction_step_sequence", None),
        "cleaning_requirement_id": row.get("cleaning_requirement_id", None),
        "cleaning_type": cleaning_type,
        "cleaning_method": cleaning_method,
        "status": status,
        "scheduled_start": row.get("scheduled_start", None) or None,
        "scheduled_end": row.get("scheduled_end", None) or None,
        "actual_start": row.get("actual_start", None) or None,
        "actual_end": row.get("actual_end", None) or None,
        "performed_by": row.get("performed_by", row.get("technician", None)),
        "verified_by": row.get("verified_by", None),
        "approved_by": row.get("approved_by", None),
        "chemicals_used": [],
        "water_type": row.get("water_type", None),
        "water_volume_liters": row.get("water_volume_liters", None),
        "verification_method": row.get("verification_method", "Visual Inspection"),
        "verification_result": row.get("verification_result", None),
        "residue_limit_ppm": row.get("residue_limit_ppm", None),
        "residue_found_ppm": row.get("residue_found_ppm", None),
        "previous_product": row.get("previous_product", None),
        "current_product": row.get("current_product", None),
        "batch_no": row.get("batch_no", None),
        "room_temp_celsius": row.get("room_temp_celsius", None),
        "room_humidity_pct": row.get("room_humidity_pct", None),
        "remarks": row.get("remarks", None),
        "attachments": row.get("attachments", []) or [],
        "created_at": row.get("time", datetime.now(timezone.utc).isoformat()),
        "updated_at": row.get("time", datetime.now(timezone.utc).isoformat()),
    }


def _record_to_line(data: dict, ts_ns: int) -> str:
    tags = (
        f"equipment_id={_escape_tag(data.get('equipment_id', ''))},"
        f"machine_id={_escape_tag(data.get('machine_id', data.get('equipment_id', '')))},"
        f"status={_escape_tag(data.get('status', 'Scheduled'))},"
        f"technician={_escape_tag(data.get('performed_by', ''))},"
        f"cleaning_type={_escape_tag(data.get('cleaning_type', 'Routine'))},"
        f"cleaning_method={_escape_tag(data.get('cleaning_method', 'Manual'))}"
    )
    fields = (
        f"record_id={_escape_tag(data.get('record_id', ''))},"
        f"equipment_id={_escape_tag(data.get('equipment_id', ''))},"
        f"machine_id={_escape_tag(data.get('machine_id', data.get('equipment_id', '')))},"
        f"cleaning_type={_escape_tag(data.get('cleaning_type', 'Routine'))},"
        f"cleaning_method={_escape_tag(data.get('cleaning_method', 'Manual'))},"
        f"status={_escape_tag(data.get('status', 'Scheduled'))},"
        f"technician={_escape_tag(data.get('performed_by', ''))},"
        f"work_order_id={_escape_tag(data.get('work_order_id', '') or '')}"
    )
    return f"{COLLECTION},{tags} {fields} {ts_ns}"


# ── Read ──────────────────────────────────────────────────────────────────────


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    sql = f"SELECT * FROM {COLLECTION} ORDER BY time DESC LIMIT 500"
    rows = _influx_query(sql)
    seen: dict[str, dict] = {}
    for r in rows:
        rid = r.get("record_id", "")
        if rid and rid not in seen:
            seen[rid] = r
    deduped = list(seen.values())
    total = len(deduped)
    start = (page - 1) * page_size
    return [_row_to_dict(r) for r in deduped[start : start + page_size]], total


async def get_by_id(db: AsyncIOMotorDatabase, record_id: str) -> Optional[dict]:
    sql = f"SELECT * FROM {COLLECTION} WHERE record_id = '{record_id}' LIMIT 1"
    rows = _influx_query(sql)
    return _row_to_dict(rows[0]) if rows else None


async def get_by_equipment_id(
    db: AsyncIOMotorDatabase, equipment_id: str
) -> list[dict]:
    sql = f"SELECT * FROM {COLLECTION} WHERE equipment_id = '{equipment_id}' ORDER BY time DESC LIMIT 100"
    rows = _influx_query(sql)
    return [_row_to_dict(r) for r in rows]


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    sql = f"SELECT * FROM {COLLECTION} WHERE status = '{status}' ORDER BY time DESC LIMIT 100"
    rows = _influx_query(sql)
    return [_row_to_dict(r) for r in rows]


async def get_by_work_order(db: AsyncIOMotorDatabase, work_order_id: str) -> list[dict]:
    sql = f"SELECT * FROM {COLLECTION} WHERE work_order_id = '{work_order_id}' ORDER BY time DESC LIMIT 100"
    rows = _influx_query(sql)
    return [_row_to_dict(r) for r in rows]


# ── Create ────────────────────────────────────────────────────────────────────


async def create(db: AsyncIOMotorDatabase, data: CleaningRecordCreate) -> dict:
    record = data.model_dump()
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    line = _record_to_line(record, ts_ns)
    _influx_write([line])
    await _attach_to_batch_record(db, record)
    return record


# ── Update ────────────────────────────────────────────────────────────────────


async def update(
    db: AsyncIOMotorDatabase,
    record_id: str,
    data: CleaningRecordUpdate,
) -> Optional[dict]:
    existing = await get_by_id(db, record_id)
    if not existing:
        return None
    updates = data.model_dump(exclude_unset=True)
    existing.update(updates)
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    line = _record_to_line(existing, ts_ns)
    _influx_write([line])
    return existing


# ── Delete ────────────────────────────────────────────────────────────────────


async def delete(db: AsyncIOMotorDatabase, record_id: str) -> bool:
    existing = await get_by_id(db, record_id)
    if not existing:
        return False
    existing["status"] = "Cancelled"
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    line = _record_to_line(existing, ts_ns)
    _influx_write([line])
    await _detach_from_batch_record(db, existing)
    return True


# ── Batch record attachment (kept as-is — operates on separate MongoDB collections) ──


def _package_updates(record: dict) -> dict:
    record_id = record.get("record_id")
    requirement_id = record.get("cleaning_requirement_id")
    attachments = [item for item in record.get("attachments") or [] if item]
    add_to_set: dict = {}
    set_fields: dict = {}
    if record_id:
        add_to_set["cleaning_record_ids"] = record_id
        add_to_set["metadata.batch_record_package.cleaning_record_ids"] = record_id
        add_to_set["metadata.bmr_package.cleaning_record_ids"] = record_id
        add_to_set["metadata.bpr_package.cleaning_record_ids"] = record_id
    if record_id and requirement_id:
        add_to_set["metadata.required_cleaning_requirement_ids"] = requirement_id
        add_to_set[
            "metadata.batch_record_package.required_cleaning_requirement_ids"
        ] = requirement_id
        add_to_set["metadata.bmr_package.required_cleaning_requirement_ids"] = (
            requirement_id
        )
        add_to_set["metadata.bpr_package.required_cleaning_requirement_ids"] = (
            requirement_id
        )
        set_fields[f"metadata.cleaning_requirement_record_map.{requirement_id}"] = (
            record_id
        )
        set_fields[
            f"metadata.batch_record_package.cleaning_requirement_record_map.{requirement_id}"
        ] = record_id
        set_fields[
            f"metadata.bmr_package.cleaning_requirement_record_map.{requirement_id}"
        ] = record_id
        set_fields[
            f"metadata.bpr_package.cleaning_requirement_record_map.{requirement_id}"
        ] = record_id
    if attachments:
        add_to_set["evidence_document_ids"] = {"$each": attachments}
        add_to_set["metadata.batch_record_package.cleaning_attachment_ids"] = {
            "$each": attachments
        }
        add_to_set["metadata.bmr_package.cleaning_attachment_ids"] = {
            "$each": attachments
        }
        add_to_set["metadata.bpr_package.cleaning_attachment_ids"] = {
            "$each": attachments
        }
    updates: dict = {}
    if add_to_set:
        updates["$addToSet"] = add_to_set
    if set_fields:
        updates["$set"] = set_fields
    return updates


async def _attach_to_batch_record(db: AsyncIOMotorDatabase, record: dict) -> None:
    batch_execution_record_id = record.get("batch_execution_record_id")
    if not batch_execution_record_id:
        return
    updates = _package_updates(record)
    if updates:
        await db.batch_production_execution_records.update_one(
            {"batch_execution_record_id": batch_execution_record_id},
            updates,
        )


def _package_removals(record: dict) -> dict:
    record_id = record.get("record_id")
    requirement_id = record.get("cleaning_requirement_id")
    attachments = [item for item in record.get("attachments") or [] if item]
    pull: dict = {}
    unset: dict = {}
    if record_id:
        pull["cleaning_record_ids"] = record_id
        pull["metadata.batch_record_package.cleaning_record_ids"] = record_id
        pull["metadata.bmr_package.cleaning_record_ids"] = record_id
        pull["metadata.bpr_package.cleaning_record_ids"] = record_id
    if requirement_id:
        pull["metadata.required_cleaning_requirement_ids"] = requirement_id
        pull["metadata.batch_record_package.required_cleaning_requirement_ids"] = (
            requirement_id
        )
        pull["metadata.bmr_package.required_cleaning_requirement_ids"] = requirement_id
        pull["metadata.bpr_package.required_cleaning_requirement_ids"] = requirement_id
        unset[f"metadata.cleaning_requirement_record_map.{requirement_id}"] = ""
        unset[
            f"metadata.batch_record_package.cleaning_requirement_record_map.{requirement_id}"
        ] = ""
        unset[
            f"metadata.bmr_package.cleaning_requirement_record_map.{requirement_id}"
        ] = ""
        unset[
            f"metadata.bpr_package.cleaning_requirement_record_map.{requirement_id}"
        ] = ""
    if attachments:
        pull["evidence_document_ids"] = {"$in": attachments}
        pull["metadata.batch_record_package.cleaning_attachment_ids"] = {
            "$in": attachments
        }
        pull["metadata.bmr_package.cleaning_attachment_ids"] = {"$in": attachments}
        pull["metadata.bpr_package.cleaning_attachment_ids"] = {"$in": attachments}
    removals: dict = {}
    if pull:
        removals["$pull"] = pull
    if unset:
        removals["$unset"] = unset
    return removals


async def _detach_from_batch_record(db: AsyncIOMotorDatabase, record: dict) -> None:
    batch_execution_record_id = record.get("batch_execution_record_id")
    if not batch_execution_record_id:
        return
    removals = _package_removals(record)
    if removals:
        await db.batch_production_execution_records.update_one(
            {"batch_execution_record_id": batch_execution_record_id},
            removals,
        )
