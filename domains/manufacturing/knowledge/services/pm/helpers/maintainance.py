"""Maintenance logs CRUD — reads/writes from InfluxDB maintenance_log measurement."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.config import settings
from services.pm.models.maintainanceLog import (
    MaintenanceLogCreate,
    MaintenanceLogUpdate,
)

logger = logging.getLogger(__name__)

COLLECTION = "maintenance_log"


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
    return {
        "id": row.get("id", ""),
        "machine_id": row.get("machine_id", ""),
        "equipment_id": row.get("equipment_id", ""),
        "machine_name": row.get("machine_name", ""),
        "maintenance_type": row.get("maintenance_type", "Preventive"),
        "status": row.get("status", "Scheduled"),
        "severity": row.get("severity", "Medium"),
        "description": row.get("description", ""),
        "performed_by": row.get("performed_by", ""),
        "work_order_id": row.get("work_order_id", ""),
    }


def _record_to_line(data: dict, ts_ns: int) -> str:
    tags = (
        f"machine_id={_escape_tag(data.get('machine_id', ''))},"
        f"machine_name={_escape_tag(data.get('machine_name', ''))},"
        f"maintenance_type={_escape_tag(data.get('maintenance_type', 'Preventive'))},"
        f"status={_escape_tag(data.get('status', 'Scheduled'))},"
        f"severity={_escape_tag(data.get('severity', 'Medium'))},"
        f"performed_by={_escape_tag(data.get('performed_by', ''))}"
    )
    fields = (
        f"id={_escape_tag(data.get('id', ''))},"
        f"machine_id={_escape_tag(data.get('machine_id', ''))},"
        f"equipment_id={_escape_tag(data.get('equipment_id', ''))},"
        f"machine_name={_escape_tag(data.get('machine_name', ''))},"
        f"maintenance_type={_escape_tag(data.get('maintenance_type', 'Preventive'))},"
        f"status={_escape_tag(data.get('status', 'Scheduled'))},"
        f"severity={_escape_tag(data.get('severity', 'Medium'))},"
        f"description={_escape_tag(data.get('description', ''))},"
        f"performed_by={_escape_tag(data.get('performed_by', ''))},"
        f"work_order_id={_escape_tag(data.get('work_order_id', ''))}"
    )
    return f"{COLLECTION},{tags} {fields} {ts_ns}"


# ── Read ──────────────────────────────────────────────────────────────────────


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    machine_id: Optional[str] = None,
    status: Optional[str] = None,
    maintenance_type: Optional[str] = None,
    severity: Optional[str] = None,
) -> tuple[list[dict], int]:
    filters = []
    if machine_id:
        filters.append(f"machine_id = '{machine_id}'")
    if status:
        filters.append(f"status = '{status}'")
    if maintenance_type:
        filters.append(f"maintenance_type = '{maintenance_type}'")
    if severity:
        filters.append(f"severity = '{severity}'")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    sql = f"SELECT * FROM {COLLECTION} {where} ORDER BY time DESC LIMIT {page_size} OFFSET {(page - 1) * page_size}"
    rows = _influx_query(sql)
    total_sql = f"SELECT count(*) as cnt FROM {COLLECTION} {where}"
    total_rows = _influx_query(total_sql)
    total = total_rows[0].get("cnt", 0) if total_rows else 0
    return [_row_to_dict(r) for r in rows], total


async def get_by_id(db: AsyncIOMotorDatabase, log_id: str) -> Optional[dict]:
    sql = f"SELECT * FROM {COLLECTION} WHERE id = '{log_id}' LIMIT 1"
    rows = _influx_query(sql)
    return _row_to_dict(rows[0]) if rows else None


async def get_by_machine(db: AsyncIOMotorDatabase, machine_id: str) -> list[dict]:
    sql = f"SELECT * FROM {COLLECTION} WHERE machine_id = '{machine_id}' ORDER BY time DESC LIMIT 100"
    rows = _influx_query(sql)
    return [_row_to_dict(r) for r in rows]


async def get_by_equipment_id(
    db: AsyncIOMotorDatabase, equipment_id: str
) -> list[dict]:
    sql = f"SELECT * FROM {COLLECTION} WHERE equipment_id = '{equipment_id}' ORDER BY time DESC LIMIT 100"
    rows = _influx_query(sql)
    return [_row_to_dict(r) for r in rows]


async def get_open(
    db: AsyncIOMotorDatabase, *, machine_id: str | None = None
) -> list[dict]:
    where = "status != 'Completed' AND status != 'Cancelled'"
    if machine_id:
        where += f" AND machine_id = '{machine_id}'"
    sql = f"SELECT * FROM {COLLECTION} WHERE {where} ORDER BY time DESC LIMIT 200"
    rows = _influx_query(sql)
    return [_row_to_dict(r) for r in rows]


# ── Create ────────────────────────────────────────────────────────────────────


async def create(db: AsyncIOMotorDatabase, data: MaintenanceLogCreate) -> dict:
    record = data.model_dump()
    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    line = _record_to_line(record, ts_ns)
    _influx_write([line])
    return record


# ── Update ────────────────────────────────────────────────────────────────────


async def update(
    db: AsyncIOMotorDatabase, log_id: str, data: MaintenanceLogUpdate
) -> Optional[dict]:
    existing = await get_by_id(db, log_id)
    if not existing:
        return None
    updates = data.model_dump(exclude_unset=True)
    existing.update(updates)
    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    line = _record_to_line(existing, ts_ns)
    _influx_write([line])
    return existing


# ── Delete ────────────────────────────────────────────────────────────────────


async def delete(db: AsyncIOMotorDatabase, log_id: str) -> bool:
    existing = await get_by_id(db, log_id)
    if not existing:
        return False
    existing["status"] = "Cancelled"
    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    line = _record_to_line(existing, ts_ns)
    _influx_write([line])
    return True
