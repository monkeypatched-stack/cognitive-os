"""Calibration records CRUD — reads/writes from InfluxDB calibration_log measurement."""

import json
import logging
from datetime import date, datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.config import settings
from services.pm.models.calibrations import (
    CalibrationRecordCreate,
    CalibrationRecordUpdate,
)

logger = logging.getLogger(__name__)

COLLECTION = "calibration_log"  # InfluxDB measurement name


def _escape_tag(value: str) -> str:
    return str(value).replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def _influx_write(lines: list[str]) -> None:
    """Write line protocol lines to InfluxDB."""
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
    """Execute SQL query against InfluxDB."""
    params = urlencode({"db": settings.INFLUXDB_BUCKET, "q": sql})
    url = f"{settings.INFLUXDB_URL.rstrip('/')}/api/v3/query_sql?{params}"
    headers = {"Content-Type": "application/json"}
    if settings.INFLUXDB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.INFLUXDB_TOKEN}"
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _row_to_dict(row: dict) -> dict:
    """Convert InfluxDB row to dict format expected by API."""
    time_val = row.get("time", "")
    return {
        "record_id": row.get("equipment_id", ""),
        "equipment_id": row.get("equipment_id", ""),
        "work_order_id": row.get("work_order_id", None),
        "instrument_tag": row.get("instrument_tag", None),
        "instrument_range": row.get("instrument_range", None),
        "instrument_resolution": row.get("instrument_resolution", None),
        "calibration_type": row.get("calibration_type", "Routine"),
        "status": row.get("status", "Scheduled"),
        "standard_used": row.get("standard_used", "In-House"),
        "certificate_no": row.get("certificate_no", None),
        "scheduled_date": None,
        "performed_date": time_val[:10] if time_val else None,
        "next_due_date": None,
        "calibration_interval_days": None,
        "performed_by": row.get("technician", None),
        "reviewed_by": None,
        "approved_by": None,
        "reference_instrument": None,
        "reference_cert_no": None,
        "reference_expiry": None,
        "data_points": [],
        "tolerance": None,
        "max_deviation_found": None,
        "unit": None,
        "room_temp_celsius": None,
        "room_humidity_pct": None,
        "out_of_tolerance": False,
        "corrective_action": None,
        "as_found_condition": row.get("result", None),
        "as_left_condition": None,
        "remarks": None,
        "attachments": [],
        "created_at": time_val or datetime.now(timezone.utc).isoformat(),
        "updated_at": time_val or datetime.now(timezone.utc).isoformat(),
    }


def _record_to_line(data: dict, ts_ns: int) -> str:
    """Convert calibration record to InfluxDB line protocol."""
    tags = (
        f"equipment_id={_escape_tag(data.get('equipment_id', ''))},"
        f"machine_id={_escape_tag(data.get('machine_id', ''))},"
        f"status={_escape_tag(data.get('status', 'Scheduled'))},"
        f"technician={_escape_tag(data.get('technician', ''))},"
        f"result={_escape_tag(data.get('result', ''))},"
        f"calibration_type={_escape_tag(data.get('calibration_type', 'Routine'))},"
        f"standard_used={_escape_tag(data.get('standard_used', 'In-House'))}"
    )
    fields = (
        f"record_id={_escape_tag(data.get('record_id', ''))},"
        f"equipment_id={_escape_tag(data.get('equipment_id', ''))},"
        f"machine_id={_escape_tag(data.get('machine_id', ''))},"
        f"calibration_type={_escape_tag(data.get('calibration_type', 'Routine'))},"
        f"status={_escape_tag(data.get('status', 'Scheduled'))},"
        f"standard_used={_escape_tag(data.get('standard_used', 'In-House'))},"
        f"technician={_escape_tag(data.get('technician', ''))},"
        f"result={_escape_tag(data.get('result', ''))}"
    )
    return f"{COLLECTION},{tags} {fields} {ts_ns}"


# ── Read ──────────────────────────────────────────────────────────────────────


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    sql = f"SELECT * FROM {COLLECTION} ORDER BY time DESC LIMIT {page_size} OFFSET {(page - 1) * page_size}"
    rows = _influx_query(sql)
    total_sql = f"SELECT count(*) as cnt FROM {COLLECTION}"
    total_rows = _influx_query(total_sql)
    total = total_rows[0].get("cnt", 0) if total_rows else 0
    return [_row_to_dict(r) for r in rows], total


async def get_by_id(db: AsyncIOMotorDatabase, record_id: str) -> Optional[dict]:
    sql = f"SELECT * FROM {COLLECTION} WHERE record_id = '{record_id}' LIMIT 1"
    rows = _influx_query(sql)
    return _row_to_dict(rows[0]) if rows else None


async def get_by_machine(db: AsyncIOMotorDatabase, machine_id: str) -> list[dict]:
    sql = f"SELECT * FROM {COLLECTION} WHERE machine_id = '{machine_id}' ORDER BY time DESC LIMIT 100"
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


async def create(db: AsyncIOMotorDatabase, data: CalibrationRecordCreate) -> dict:
    record = data.model_dump()
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    line = _record_to_line(record, ts_ns)
    _influx_write([line])
    return record


# ── Update ────────────────────────────────────────────────────────────────────


async def update(
    db: AsyncIOMotorDatabase,
    record_id: str,
    data: CalibrationRecordUpdate,
) -> Optional[dict]:
    # InfluxDB doesn't support update-in-place; write new record with updated fields
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
    # InfluxDB doesn't support delete-by-field; mark as deleted via status update
    existing = await get_by_id(db, record_id)
    if not existing:
        return False
    existing["status"] = "Deleted"
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    line = _record_to_line(existing, ts_ns)
    _influx_write([line])
    return True
