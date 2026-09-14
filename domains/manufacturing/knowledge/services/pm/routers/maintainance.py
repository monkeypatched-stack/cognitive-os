import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.auth import require_permission
from services.common.config import settings
from services.common.db import get_database
from services.pm.helpers import maintainance as crud
from services.pm.models.maintainanceLog import (
    MaintenanceLogCreate,
    MaintenanceLogResponse,
    MaintenanceLogUpdate,
    PaginatedMaintenanceLogResponse,
)

router = APIRouter()


def _query_influx_maintenance() -> list[dict]:
    """Query maintenance records from InfluxDB."""
    query = (
        "SELECT * FROM maintenance_log "
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


def _influx_to_maintenance_log(row: dict) -> dict:
    """Convert InfluxDB row to maintenance log format."""
    payload_str = row.get("payload_json", "{}")
    try:
        payload = json.loads(payload_str) if payload_str else {}
    except json.JSONDecodeError:
        payload = {}

    # Use tags if available, fall back to payload
    maint_type = row.get("maintenance_type") or payload.get(
        "maintenance_type", "Preventive"
    )
    status_val = (
        row.get("log_status") or row.get("status") or payload.get("status", "Scheduled")
    )
    severity = row.get("severity") or payload.get("severity", "Medium")
    machine_name = row.get("equipment_name") or payload.get("machine_name", "")
    machine_id = row.get("machine_id") or payload.get("machine_id", "")
    equipment_id = row.get("equipment_id") or payload.get(
        "id", row.get("equipment_id", "")
    )
    performed = row.get("performed_by") or payload.get("performed_by", "")
    resolution = payload.get(
        "resolution", "Completed per procedure" if status_val == "Completed" else None
    )

    # Compute next_due_date
    interval = payload.get("maintenance_interval_days")
    _raw_start = row.get("time", payload.get("actual_start", ""))
    actual_start = _raw_start[:10] if _raw_start else None
    next_due = None
    if actual_start and interval:
        from datetime import datetime as dt
        from datetime import timedelta

        try:
            p_date = dt.strptime(actual_start, "%Y-%m-%d")
            next_due = (p_date + timedelta(days=int(interval))).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass

    _raw_end = row.get("actual_end", payload.get("actual_end", ""))
    actual_end = _raw_end[:10] if _raw_end else None
    _row_end = row.get("actual_end", "")
    downtime_end = _row_end[:10] if _row_end else None

    return {
        "id": equipment_id,
        "machine_id": machine_id,
        "equipment_id": equipment_id,
        "machine_name": machine_name,
        "maintenance_type": maint_type,
        "status": status_val,
        "severity": severity,
        "description": payload.get("description", ""),
        "issue_reported": payload.get("issue_reported"),
        "resolution": resolution,
        "scheduled_start": row.get("scheduled_start", payload.get("scheduled_start")),
        "scheduled_end": row.get("scheduled_end", payload.get("scheduled_end")),
        "actual_start": actual_start,
        "actual_end": actual_end,
        "next_due_date": next_due,
        "maintenance_interval_days": interval,
        "performed_by": performed,
        "reviewed_by": payload.get("reviewed_by"),
        "approved_by": None,
        "downtime": (
            {"start_time": actual_start, "end_time": downtime_end}
            if actual_start and status_val in ("Completed", "In Progress")
            else None
        ),
        "parts_used": payload.get("parts_used", []),
        "work_order_id": payload.get("work_order_id"),
        "created_at": row.get("time", ""),
        "updated_at": row.get("time", ""),
    }


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedMaintenanceLogResponse)
async def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    machine_id: str | None = Query(None, description="Filter by machine ID"),
    status: str | None = Query(
        None, description="Scheduled | In Progress | Completed | Cancelled | Delayed"
    ),
    maintenance_type: str | None = Query(
        None, description="Preventive | Corrective | Predictive | Inspection"
    ),
    severity: str | None = Query(None, description="Low | Medium | High | Critical"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-maintenance")),
):
    rows = _query_influx_maintenance()
    logs = [_influx_to_maintenance_log(row) for row in rows]

    # Enrich with expected_completion from linked work orders
    wo_ids = list(
        {log.get("work_order_id") for log in logs if log.get("work_order_id")}
    )
    if wo_ids:
        wos = (
            await db["work_orders"]
            .find({"work_order_id": {"$in": wo_ids}})
            .to_list(100)
        )
        wo_map = {wo["work_order_id"]: wo for wo in wos}
        for log in logs:
            wo = wo_map.get(log.get("work_order_id"))
            if wo and wo.get("expected_completion"):
                expected = wo["expected_completion"]
                log["next_due_date"] = (
                    expected.strftime("%Y-%m-%d")
                    if hasattr(expected, "strftime")
                    else str(expected)[:10]
                )

    # Apply filters
    if machine_id:
        logs = [log for log in logs if log.get("machine_id") == machine_id]
    if status:
        logs = [log for log in logs if log.get("status") == status]
    if maintenance_type:
        logs = [log for log in logs if log.get("maintenance_type") == maintenance_type]
    if severity:
        logs = [log for log in logs if log.get("severity") == severity]
    total = len(logs)
    start = (page - 1) * page_size
    page_logs = logs[start : start + page_size]
    return PaginatedMaintenanceLogResponse(
        total=total, page=page, page_size=page_size, results=page_logs
    )


# ── Get by machine ────────────────────────────────────────────────────────────


@router.get("/by-machine/{machine_id}", response_model=list[MaintenanceLogResponse])
async def list_logs_by_machine(
    machine_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-maintenance")),
):
    rows = _query_influx_maintenance()
    logs = [
        _influx_to_maintenance_log(row)
        for row in rows
        if row.get("machine_id") == machine_id
    ]
    return logs


@router.get("/by-equipment/{equipment_id}", response_model=list[MaintenanceLogResponse])
async def list_logs_by_equipment(
    equipment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-maintenance")),
):
    return await crud.get_by_equipment_id(db, equipment_id)


# ── Get open logs ─────────────────────────────────────────────────────────────


@router.get("/open", response_model=list[MaintenanceLogResponse])
async def list_open_logs(
    machine_id: str | None = Query(
        None, description="Optionally scope to a specific machine"
    ),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-maintenance")),
):
    return await crud.get_open(db, machine_id=machine_id)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{log_id}", response_model=MaintenanceLogResponse)
async def get_log(
    log_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-maintenance")),
):
    record = await crud.get_by_id(db, log_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MaintenanceLog '{log_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=MaintenanceLogResponse, status_code=status.HTTP_201_CREATED
)
async def create_log(
    data: MaintenanceLogCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-maintenance")),
):
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MaintenanceLog '{data.id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{log_id}", response_model=MaintenanceLogResponse)
async def update_log(
    log_id: str,
    data: MaintenanceLogUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-maintenance")),
):
    updated = await crud.update(db, log_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MaintenanceLog '{log_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_log(
    log_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-maintenance")),
):
    if not await crud.delete(db, log_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MaintenanceLog '{log_id}' not found",
        )


# TODO_ENDPOINT: GET /api/v1/maintenance/{log_id}/parts — list all parts used in a specific log
# TODO_ENDPOINT: GET /api/v1/maintenance/{log_id}/technicians — list all technicians assigned to a log
