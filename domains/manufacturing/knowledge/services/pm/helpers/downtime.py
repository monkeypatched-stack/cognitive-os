"""Downtime logs CRUD — reads/writes from InfluxDB downtime_log measurement."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.config import settings
from services.pm.models.downtime import DowntimeLogCreate, DowntimeLogUpdate

logger = logging.getLogger(__name__)

COLLECTION = "downtime_log"

CATEGORY_MAP = {
    "Mechanical-Failure": "Mechanical Failure",
    "Electrical-Fault": "Electrical Fault",
    "Planned-Maintenance": "Planned Maintenance",
    "Software-Error": "Software Error",
    "Operator-Error": "Operator Error",
    "Material-Shortage": "Material Shortage",
    "Quality-Issue": "Quality Issue",
    "Quality-Hold": "Quality Issue",
    "External": "External",
}

EVENT_DURATIONS = {
    "maintenance_log": {
        "Preventive": 120,
        "Corrective": 240,
        "Calibration": 60,
        "Emergency": 360,
    },
    "calibration_log": 60,
    "cleaning_log": {
        "CIP": 45,
        "COP": 30,
        "Manual": 20,
        "SOP-Based": 20,
    },
    "downtime_log": 60,
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


def _parse_payload(row: dict) -> dict:
    pj = row.get("payload_json")
    if pj:
        try:
            return json.loads(pj) if isinstance(pj, str) else pj
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _estimate_duration(measurement: str, row: dict) -> int:
    payload = _parse_payload(row)
    if "duration_minutes" in payload and payload["duration_minutes"]:
        return int(payload["duration_minutes"])
    if "duration_min" in payload and payload["duration_min"]:
        return int(payload["duration_min"])

    if measurement == "maintenance_log":
        mtype = row.get("maintenance_type", row.get("event_type", "Preventive"))
        table = EVENT_DURATIONS.get("maintenance_log", {})
        return table.get(mtype, 120)
    elif measurement == "calibration_log":
        return EVENT_DURATIONS.get("calibration_log", 60)
    elif measurement == "cleaning_log":
        ctype = row.get("clean_type", row.get("cleaning_method", "Manual"))
        table = EVENT_DURATIONS.get("cleaning_log", {})
        return table.get(ctype, 30)
    elif measurement == "downtime_log":
        return EVENT_DURATIONS.get("downtime_log", 60)
    return 30


def _row_to_dict(row: dict) -> dict:
    from datetime import timedelta

    payload = _parse_payload(row)
    time_val = row.get("time", "")
    raw_machine_id = row.get("machine_id", payload.get("machine_id", ""))
    machine_name = row.get("machine_name", payload.get("machine_name", ""))
    duration = payload.get("duration_minutes", payload.get("duration_min"))
    if duration is None:
        duration = _estimate_duration("downtime_log", row)
    raw_category = row.get(
        "reason", payload.get("reason", payload.get("category", "Mechanical Failure"))
    )
    category = CATEGORY_MAP.get(raw_category, "Mechanical Failure")
    start_time = payload.get("start_time", time_val)
    end_time = payload.get("end_time")
    if not end_time and start_time and duration:
        try:
            from datetime import datetime as _dt

            st = _dt.fromisoformat(start_time.replace("Z", "+00:00"))
            end_time = (st + timedelta(minutes=int(duration))).isoformat()
        except (ValueError, TypeError):
            pass
    log_id = payload.get("id", f"DT-{raw_machine_id[-8:]}" if raw_machine_id else "")
    stage = row.get("stage", payload.get("stage", ""))
    responsible_team = payload.get(
        "responsible_team",
        (
            "Mechanical"
            if "Mechanical" in raw_category
            else (
                "Operations"
                if "Operator" in raw_category
                else "Electrical" if "Electrical" in raw_category else "Operations"
            )
        ),
    )
    linked_work_order = payload.get("linked_work_order", None)
    return {
        "id": log_id,
        "machine_id": raw_machine_id,
        "equipment_id": row.get("equipment_id", payload.get("equipment_id", None)),
        "machine_name": machine_name,
        "line_id": payload.get("line_id", None),
        "start_time": start_time,
        "end_time": end_time,
        "duration_minutes": duration,
        "category": category,
        "cause": payload.get("cause", row.get("reason", None)),
        "status": payload.get("status", "Resolved"),
        "responsible_team": responsible_team,
        "linked_work_order": linked_work_order,
        "linked_maintenance_id": payload.get("linked_maintenance_id", None),
        "notes": payload.get("notes", None),
        "created_at": time_val or datetime.now(timezone.utc).isoformat(),
        "updated_at": time_val or datetime.now(timezone.utc).isoformat(),
    }


def _record_to_line(data: dict, ts_ns: int) -> str:
    machine_id = data.get("machine_id", "")
    tags = (
        f"machine_id={_escape_tag(machine_id)},"
        f"machine_name={_escape_tag(data.get('machine_name', ''))},"
        f"impact={_escape_tag(data.get('category', 'Mechanical Failure').replace(' ', '-'))},"
        f"reason={_escape_tag(data.get('cause', '').replace(' ', '-') if data.get('cause') else data.get('category', 'Unknown').replace(' ', '-'))}"
    )
    fields = (
        f"id={_escape_tag(data.get('id', ''))},"
        f"machine_id={_escape_tag(machine_id)},"
        f"equipment_id={_escape_tag(data.get('equipment_id', '') or '')},"
        f"machine_name={_escape_tag(data.get('machine_name', ''))},"
        f"category={_escape_tag(data.get('category', 'Mechanical Failure'))},"
        f"cause={_escape_tag(data.get('cause', '') or '')},"
        f"status={_escape_tag(data.get('status', 'Active'))},"
        f"duration_minutes={data.get('duration_minutes', 0) or 0}"
    )
    return f"{COLLECTION},{tags} {fields} {ts_ns}"


# ── Summary: per-equipment total downtime + MTTR ──────────────────────────────


async def get_equipment_downtime_summary() -> list[dict]:
    machines: dict[str, dict] = {}

    for measurement in [
        "downtime_log",
        "maintenance_log",
        "calibration_log",
        "cleaning_log",
    ]:
        sql = f"SELECT * FROM {measurement} ORDER BY time DESC LIMIT 500"
        try:
            rows = _influx_query(sql)
        except Exception:
            continue
        for row in rows:
            machine_id = row.get("machine_id", "")
            if not machine_id:
                continue
            if machine_id not in machines:
                machines[machine_id] = {
                    "machine_id": machine_id,
                    "machine_name": row.get("machine_name", ""),
                    "total_downtime_minutes": 0,
                    "event_count": 0,
                    "maintenance_count": 0,
                    "calibration_count": 0,
                    "cleaning_count": 0,
                    "downtime_count": 0,
                }
            m = machines[machine_id]
            duration = _estimate_duration(measurement, row)
            m["total_downtime_minutes"] += duration
            m["event_count"] += 1
            if measurement == "maintenance_log":
                m["maintenance_count"] += 1
            elif measurement == "calibration_log":
                m["calibration_count"] += 1
            elif measurement == "cleaning_log":
                m["cleaning_count"] += 1
            elif measurement == "downtime_log":
                m["downtime_count"] += 1

    result = []
    for m in machines.values():
        m["mttr_minutes"] = (
            round(m["total_downtime_minutes"] / m["event_count"])
            if m["event_count"]
            else 0
        )
        result.append(m)

    result.sort(key=lambda x: x["total_downtime_minutes"], reverse=True)
    return result


# ── Read ──────────────────────────────────────────────────────────────────────


def _normalize_name(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]", "", name.lower())


STAGE_LINE_MAP = {
    "granulation": "LINE-TAB-001",
    "drying": "LINE-TAB-001",
    "compression": "LINE-TAB-001",
    "film-coating": "LINE-TAB-001",
    "coating": "LINE-TAB-001",
    "blending": "LINE-TAB-001",
    "sifting": "LINE-TAB-001",
    "milling": "LINE-TAB-001",
    "packaging": "LINE-TAB-001",
    "dispensing": "LINE-TAB-001",
    "capsulefilling": "LINE-CAP-001",
    "polishing": "LINE-CAP-001",
    "inspection": "LINE-TAB-001",
}

NAME_LINE_HINTS = {
    "highsheargranulator": "LINE-TAB-001",
    "coatingpan": "LINE-TAB-001",
    "tabletpress": "LINE-TAB-001",
    "conemill": "LINE-TAB-001",
    "fluidbeddryer": "LINE-TAB-001",
    "binblender": "LINE-TAB-001",
    "cartoner": "LINE-TAB-001",
    "checkweigher": "LINE-TAB-001",
    "metaldetector": "LINE-TAB-001",
    "blisterpacker": "LINE-TAB-001",
}


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
    records = [_row_to_dict(r) for r in rows]

    machine_ids = list({r["machine_id"] for r in records if r["machine_id"]})
    wo_map: dict[str, str] = {}
    name_to_line: dict[str, str] = {}
    if machine_ids:
        wo_cursor = db.work_orders.find(
            {"machine_id": {"$in": machine_ids}},
            {"work_order_id": 1, "machine_id": 1, "_id": 0},
        )
        async for wo in wo_cursor:
            if wo.get("machine_id") not in wo_map:
                wo_map[wo["machine_id"]] = wo["work_order_id"]

        mach_cursor = db.pharmaceutical_machines.find(
            {},
            {"machine_id": 1, "name": 1, "line_id": 1, "_id": 0},
        )
        async for m in mach_cursor:
            if m.get("line_id") and m.get("name"):
                name_to_line[_normalize_name(m["name"])] = m["line_id"]

    for r, raw_row in zip(records, rows):
        if not r["linked_work_order"] and r["machine_id"] in wo_map:
            r["linked_work_order"] = wo_map[r["machine_id"]]
        if not r["line_id"]:
            payload = _parse_payload(raw_row)
            stage = payload.get("stage", "").lower()
            if stage in STAGE_LINE_MAP:
                r["line_id"] = STAGE_LINE_MAP[stage]
            elif r.get("machine_name"):
                normalized = _normalize_name(r["machine_name"])
                if normalized in NAME_LINE_HINTS:
                    r["line_id"] = NAME_LINE_HINTS[normalized]

    return records, total


async def get_by_id(db: AsyncIOMotorDatabase, log_id: str) -> Optional[dict]:
    sql = f"SELECT * FROM {COLLECTION} LIMIT 500"
    rows = _influx_query(sql)
    for r in rows:
        d = _row_to_dict(r)
        if d["id"] == log_id:
            return d
    return None


async def get_by_machine(db: AsyncIOMotorDatabase, machine_id: str) -> list[dict]:
    sql = f"SELECT * FROM {COLLECTION} WHERE machine_id = '{machine_id}' ORDER BY time DESC LIMIT 100"
    rows = _influx_query(sql)
    return [_row_to_dict(r) for r in rows]


async def get_by_equipment_id(
    db: AsyncIOMotorDatabase, equipment_id: str
) -> list[dict]:
    sql = f"SELECT * FROM {COLLECTION} LIMIT 500"
    rows = _influx_query(sql)
    return [_row_to_dict(r) for r in rows if r.get("equipment_id") == equipment_id]


async def get_by_line(db: AsyncIOMotorDatabase, line_id: str) -> list[dict]:
    sql = f"SELECT * FROM {COLLECTION} LIMIT 500"
    rows = _influx_query(sql)
    return [
        _row_to_dict(r) for r in rows if _parse_payload(r).get("line_id") == line_id
    ]


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    sql = f"SELECT * FROM {COLLECTION} WHERE status = '{status}' ORDER BY time DESC LIMIT 100"
    rows = _influx_query(sql)
    return [_row_to_dict(r) for r in rows]


# ── Create ────────────────────────────────────────────────────────────────────


async def create(db: AsyncIOMotorDatabase, data: DowntimeLogCreate) -> dict:
    record = data.model_dump()
    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    line = _record_to_line(record, ts_ns)
    _influx_write([line])
    return record


# ── Update ────────────────────────────────────────────────────────────────────


async def update(
    db: AsyncIOMotorDatabase, log_id: str, data: DowntimeLogUpdate
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
    existing["status"] = "Resolved"
    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    line = _record_to_line(existing, ts_ns)
    _influx_write([line])
    return True
