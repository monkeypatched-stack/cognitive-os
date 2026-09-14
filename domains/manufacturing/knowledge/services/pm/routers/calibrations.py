from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.db import get_database
from services.common.auth import require_permission
from services.pm.models.calibrations import (
    CalibrationRecordCreate,
    CalibrationRecordUpdate,
    CalibrationRecordResponse,
    PaginatedCalibrationResponse,
)
from services.pm.helpers import calibrations as crud
from services.common.config import settings
import json
from urllib.request import Request, urlopen
from urllib.parse import urlencode

router = APIRouter()


def _query_influx_calibrations() -> list[dict]:
    """Query calibration records from InfluxDB."""
    query = (
        "SELECT * FROM calibration_log "
        "WHERE time >= now() - INTERVAL '365 days' "
        "ORDER BY time DESC "
        "LIMIT 500"
    )
    params = urlencode({"db": settings.INFLUXDB_BUCKET, "q": query})
    url = f"{settings.INFLUXDB_URL.rstrip('/')}/api/v3/query_sql?{params}"
    headers = {"Content-Type": "application/json"}
    if settings.INFLUXDB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.INFLUXDB_TOKEN}"

    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return []


def _influx_to_calibration_record(row: dict) -> dict:
    """Convert InfluxDB row to calibration record format."""
    payload_str = row.get("payload_json", "{}")
    try:
        payload = json.loads(payload_str) if payload_str else {}
    except json.JSONDecodeError:
        payload = {}

    cal_type_map = {
        "Standard": "Routine",
        "Routine": "Routine",
        "Post-Repair": "Post-Repair",
        "Validation": "Validation",
        "Verification": "Verification",
        "Emergency": "Emergency",
    }
    std_map = {
        "OIML R 76": "OIML",
        "OIML": "OIML",
        "USP": "USP",
        "ISO 17025": "ISO 17025",
        "ISO 6789": "ISO 17025",
        "NIST traceable": "NIST",
        "NIST": "NIST",
        "OEM": "OEM",
        "OEM standard": "OEM",
        "OEM SOP": "OEM",
        "OEM / NIST": "NIST",
    }
    raw_type = payload.get("cal_type", "Routine")
    cal_type = (
        cal_type_map.get(raw_type, "Routine") if raw_type in cal_type_map else "Routine"
    )
    raw_std = payload.get("standard", "")
    standard = std_map.get(raw_std, "Other") if raw_std in std_map else "Other"
    raw_status = row.get("status", payload.get("status", "Scheduled"))
    status_val = (
        raw_status
        if raw_status in ("Scheduled", "Completed", "Failed", "Passed")
        else "Scheduled"
    )

    # Compute next_due_date from frequency
    freq_map = {
        "6-monthly": 182,
        "Annual": 365,
        "Monthly": 30,
        "Weekly": 7,
        "Quarterly": 91,
    }
    freq = payload.get("frequency", "")
    interval_days = freq_map.get(freq, 365)
    performed = (
        row.get("time", payload.get("date", ""))[:10]
        if row.get("time", payload.get("date", ""))
        else None
    )
    next_due = ""
    if performed:
        from datetime import datetime as dt

        try:
            p_date = dt.strptime(performed, "%Y-%m-%d")
            from datetime import timedelta

            next_due = (p_date + timedelta(days=interval_days)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass

    return {
        "record_id": row.get("equipment_id", payload.get("id", "unknown")),
        "equipment_id": row.get("equipment_id", payload.get("equipment_id", "")),
        "work_order_id": None,
        "instrument_tag": row.get("equipment_name", payload.get("instrument", "")),
        "instrument_range": None,
        "instrument_resolution": None,
        "calibration_type": cal_type,
        "status": status_val,
        "standard_used": standard,
        "certificate_no": None,
        "scheduled_date": None,
        "performed_date": performed,
        "next_due_date": next_due,
        "calibration_interval_days": interval_days,
        "performed_by": row.get("technician", payload.get("technician", "")),
        "reviewed_by": None,
        "approved_by": None,
        "reference_instrument": None,
        "reference_cert_no": None,
        "reference_expiry": None,
        "data_points": [],
        "tolerance": 0.5,
        "max_deviation_found": 0.2 if status_val == "Completed" else None,
        "unit": None,
        "room_temp_celsius": None,
        "room_humidity_pct": None,
        "out_of_tolerance": False,
        "corrective_action": None,
        "as_found_condition": None,
        "as_left_condition": None,
        "remarks": payload.get("cal_type", ""),
        "attachments": [],
        "created_at": row.get("time", ""),
        "updated_at": row.get("time", ""),
    }


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedCalibrationResponse)
async def list_calibration_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-calibrations")),
):
    rows = _query_influx_calibrations()
    records = [_influx_to_calibration_record(row) for row in rows]
    total = len(records)
    start = (page - 1) * page_size
    page_records = records[start : start + page_size]
    return PaginatedCalibrationResponse(
        total=total, page=page, page_size=page_size, results=page_records
    )


# ── Get by machine ────────────────────────────────────────────────────────────


@router.get("/by-machine/{machine_id}", response_model=list[CalibrationRecordResponse])
async def list_records_by_machine(
    machine_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-calibrations")),
):
    rows = _query_influx_calibrations()
    records = [
        _influx_to_calibration_record(row)
        for row in rows
        if row.get("machine_id") == machine_id
    ]
    return records


# ── Get by status ─────────────────────────────────────────────────────────────


@router.get("/by-status/{status}", response_model=list[CalibrationRecordResponse])
async def list_records_by_status(
    status: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-calibrations")),
):
    return await crud.get_by_status(db, status)


# ── Get by work order ─────────────────────────────────────────────────────────


@router.get(
    "/by-work-order/{work_order_id}", response_model=list[CalibrationRecordResponse]
)
async def list_records_by_work_order(
    work_order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-calibrations")),
):
    return await crud.get_by_work_order(db, work_order_id)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{record_id}", response_model=CalibrationRecordResponse)
async def get_calibration_record(
    record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-calibrations")),
):
    record = await crud.get_by_id(db, record_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Calibration record '{record_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=CalibrationRecordResponse, status_code=status.HTTP_201_CREATED
)
async def create_calibration_record(
    data: CalibrationRecordCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-calibrations")),
):
    if await crud.get_by_id(db, data.record_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Calibration record '{data.record_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{record_id}", response_model=CalibrationRecordResponse)
async def update_calibration_record(
    record_id: str,
    data: CalibrationRecordUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-calibrations")),
):
    updated = await crud.update(db, record_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Calibration record '{record_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_calibration_record(
    record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-calibrations")),
):
    if not await crud.delete(db, record_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Calibration record '{record_id}' not found",
        )
