from datetime import date, datetime, time, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.shifts.models.timesheet import (
    TimeSheetCreate,
    TimeSheetResponse,
    TimeSheetUpdate,
)

COLLECTION = "timesheets"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _coerce_datetime(value: object, fallback: Optional[datetime] = None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback or datetime.now(timezone.utc)


def _coerce_date(value: object, fallback: Optional[date] = None) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return fallback or date.today()


def _normalize_timesheet_record(doc: Optional[dict]) -> Optional[dict]:
    record = _serialize(doc)
    if not record:
        return None

    timesheet_id = str(
        record.get("id")
        or record.get("timesheet_id")
        or record.get("payroll_reference")
        or "TIMESHEET"
    )
    record["id"] = timesheet_id
    record["worker_id"] = str(
        record.get("worker_id") or record.get("user_id") or "UNKNOWN-WORKER"
    )
    record["worker_name"] = str(
        record.get("worker_name") or record.get("worker") or record["worker_id"]
    )
    period_start = _coerce_date(
        record.get("period_start")
        or record.get("work_date")
        or record.get("created_at")
    )
    period_end = _coerce_date(
        record.get("period_end") or record.get("work_date") or record.get("updated_at"),
        period_start,
    )
    if period_end < period_start:
        period_end = period_start
    record["period_start"] = period_start
    record["period_end"] = period_end
    regular_hours = float(
        record.get("total_regular_hours") or record.get("hours_worked") or 0
    )
    overtime_hours = float(
        record.get("total_overtime_hours") or record.get("overtime_hours") or 0
    )
    record["total_regular_hours"] = max(regular_hours, 0.0)
    record["total_overtime_hours"] = max(overtime_hours, 0.0)
    record["total_paid_hours"] = max(
        float(record.get("total_paid_hours") or regular_hours + overtime_hours), 0.0
    )
    record["currency"] = str(record.get("currency") or "USD")[:3].upper()
    record["overtime_rate_multiplier"] = float(
        record.get("overtime_rate_multiplier") or 1.5
    )
    record["submitted"] = bool(record.get("submitted", False))
    record["approved"] = bool(record.get("approved", False))
    entries = record.get("entries")
    record["entries"] = entries if isinstance(entries, list) else []
    record["created_at"] = _coerce_datetime(record.get("created_at"))
    if record.get("updated_at") is not None:
        record["updated_at"] = _coerce_datetime(record.get("updated_at"))

    return TimeSheetResponse.model_validate(record).model_dump()


def _dump(data) -> dict:
    doc = data.model_dump(mode="json", exclude_unset=True)
    doc.pop("gross_pay", None)
    for entry in doc.get("entries", []):
        entry.pop("total_break_minutes", None)
        entry.pop("gross_hours_worked", None)
        entry.pop("net_hours_worked", None)
        for break_period in entry.get("breaks", []):
            break_period.pop("duration_minutes", None)
    return doc


async def get_all(
    db: AsyncIOMotorDatabase, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_normalize_timesheet_record(doc) async for doc in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, timesheet_id: str) -> Optional[dict]:
    return _normalize_timesheet_record(
        await db[COLLECTION].find_one({"id": timesheet_id})
    )


async def get_by_worker(db: AsyncIOMotorDatabase, worker_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"worker_id": worker_id})
    return [_normalize_timesheet_record(doc) async for doc in cursor]


async def get_by_payroll_reference(
    db: AsyncIOMotorDatabase, payroll_reference: str
) -> Optional[dict]:
    return _normalize_timesheet_record(
        await db[COLLECTION].find_one({"payroll_reference": payroll_reference})
    )


async def get_by_approval(db: AsyncIOMotorDatabase, approved: bool) -> list[dict]:
    cursor = db[COLLECTION].find({"approved": approved})
    return [_normalize_timesheet_record(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: TimeSheetCreate) -> dict:
    doc = _dump(data)
    await db[COLLECTION].insert_one(doc)
    return _normalize_timesheet_record(doc)


async def update(
    db: AsyncIOMotorDatabase, timesheet_id: str, data: TimeSheetUpdate
) -> Optional[dict]:
    fields = _dump(data)
    if not fields:
        return await get_by_id(db, timesheet_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": timesheet_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _normalize_timesheet_record(result)


async def delete(db: AsyncIOMotorDatabase, timesheet_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": timesheet_id})
    return result.deleted_count == 1
