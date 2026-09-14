import re
from datetime import datetime, timezone
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.workorders.models.work_orders import (
    WorkOrderCreate,
    WorkOrderSubtaskCreate,
    WorkOrderSubtaskUpdate,
    WorkOrderUpdate,
)

COLLECTION = "work_orders"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _to_utc(dt):
    """Normalize any datetime/string → timezone-aware UTC datetime"""
    if not dt:
        return None

    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)

    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    return dt


def _prepare(doc: dict) -> dict:
    """Recursively convert date → datetime so BSON can encode them."""
    result = {}
    for k, v in doc.items():
        if isinstance(v, datetime):
            result[k] = _to_utc(v)
        elif isinstance(v, str):
            # try parsing ISO datetime safely
            try:
                parsed = datetime.fromisoformat(v)
                result[k] = _to_utc(parsed)
            except Exception:
                result[k] = v
        elif isinstance(v, dict):
            result[k] = _prepare(v)
        elif isinstance(v, list):
            result[k] = [_prepare(i) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = v
    return result


def _compute_is_overdue(expected, status):
    if not expected:
        return False

    if status in ("Done", "Completed", "Cancelled"):
        return False

    expected = _to_utc(expected)
    now = datetime.now(timezone.utc)

    return now > expected


def _case_insensitive_exact(value: str) -> re.Pattern:
    return re.compile(f"^{re.escape(value)}$", re.IGNORECASE)


async def _assigned_to_values(db: AsyncIOMotorDatabase, assigned_to: str) -> list[str]:
    values = {assigned_to}
    user = await db["users"].find_one(
        {
            "$or": [
                {"user_id": assigned_to},
                {"id": assigned_to},
                {"name": assigned_to},
                {"email": assigned_to},
                {"username": assigned_to},
                {"name": _case_insensitive_exact(assigned_to)},
                {"email": _case_insensitive_exact(assigned_to)},
                {"username": _case_insensitive_exact(assigned_to)},
            ]
        }
    )
    if user:
        for key in ("user_id", "id", "name", "email", "username"):
            value = user.get(key)
            if value:
                values.add(str(value))
    return sorted(values)


# ── Read ──────────────────────────────────────────────────────────────────────


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    total = await db[COLLECTION].count_documents({})
    cursor = db[COLLECTION].find({}).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, work_order_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"work_order_id": work_order_id})
    return _serialize(doc) if doc else None


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    cursor = db[COLLECTION].find({"status": status})
    return [_serialize(d) async for d in cursor]


async def get_by_priority(db: AsyncIOMotorDatabase, priority: str) -> list[dict]:
    cursor = db[COLLECTION].find({"priority": priority})
    return [_serialize(d) async for d in cursor]


async def get_by_machine(db: AsyncIOMotorDatabase, machine_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"machine_id": machine_id})
    return [_serialize(d) async for d in cursor]


async def get_by_equipment_id(
    db: AsyncIOMotorDatabase, equipment_id: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"equipment_id": equipment_id})
    return [_serialize(d) async for d in cursor]


async def get_by_line(db: AsyncIOMotorDatabase, line_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"line_id": line_id})
    return [_serialize(d) async for d in cursor]


async def get_by_circuit(db: AsyncIOMotorDatabase, circuit_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"circuit_id": circuit_id})
    return [_serialize(d) async for d in cursor]


async def get_by_team(db: AsyncIOMotorDatabase, team_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"assigned_team_id": team_id})
    return [_serialize(d) async for d in cursor]


async def get_overdue(db: AsyncIOMotorDatabase) -> list[dict]:
    cursor = db[COLLECTION].find({"is_overdue": True})
    return [_serialize(d) async for d in cursor]


async def get_by_user(db: AsyncIOMotorDatabase, user_id: str) -> list:
    if not user_id:
        return []

    assigned_values = await _assigned_to_values(db, user_id)
    cursor = db[COLLECTION].find(
        {
            "$or": [
                {"assigned_to": {"$in": assigned_values}},
                {"assigned_to_id": {"$in": assigned_values}},
                {"assigned_to_name": {"$in": assigned_values}},
                {"assigned_user_id": {"$in": assigned_values}},
                {"assigned_user_name": {"$in": assigned_values}},
                {"assigned_user_email": {"$in": assigned_values}},
                {"created_by": {"$in": assigned_values}},
            ]
        }
    )

    return [_serialize(d) async for d in cursor]


async def get_subtasks(db: AsyncIOMotorDatabase, work_order_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"parent_work_order_id": work_order_id})
    return [_serialize(d) async for d in cursor]


async def get_for_kanban(
    db: AsyncIOMotorDatabase,
    line_id: Optional[str] = None,
    circuit_id: Optional[str] = None,
    workstation_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    equipment_id: Optional[str] = None,
    team_id: Optional[str] = None,
    assigned_to: Optional[str] = None,
    parent_work_order_id: Optional[str] = None,
) -> list[dict]:
    query: dict = {}
    if line_id:
        query["line_id"] = line_id
    if circuit_id:
        query["circuit_id"] = circuit_id
    if workstation_id:
        query["workstation_id"] = workstation_id
    if machine_id:
        query["machine_id"] = machine_id
    if equipment_id:
        query["equipment_id"] = equipment_id
    if team_id:
        query["$or"] = [
            {"assigned_team_id": team_id},
            {"assigned_team_name": team_id},
        ]
    if assigned_to:
        assigned_values = await _assigned_to_values(db, assigned_to)
        assigned_query = [
            {"assigned_to": {"$in": assigned_values}},
            {"assigned_to_id": {"$in": assigned_values}},
            {"assigned_to_name": {"$in": assigned_values}},
            {"assigned_user_id": {"$in": assigned_values}},
            {"assigned_user_name": {"$in": assigned_values}},
            {"assigned_user_email": {"$in": assigned_values}},
            {"created_by": {"$in": assigned_values}},
        ]
        if "$or" in query:
            query = {"$and": [query, {"$or": assigned_query}]}
        else:
            query["$or"] = assigned_query
    if parent_work_order_id:
        query["parent_work_order_id"] = parent_work_order_id
    cursor = db[COLLECTION].find(query)
    return [_serialize(d) async for d in cursor]


async def get_count_stats(db: AsyncIOMotorDatabase) -> dict:
    total = await db[COLLECTION].count_documents({})
    completed_statuses = ["Done", "Completed", "Cancelled"]
    completed = await db[COLLECTION].count_documents(
        {"status": {"$in": completed_statuses}}
    )
    overdue = await db[COLLECTION].count_documents({"is_overdue": True})

    by_status_cursor = db[COLLECTION].aggregate(
        [
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]
    )
    by_priority_cursor = db[COLLECTION].aggregate(
        [
            {"$group": {"_id": "$priority", "count": {"$sum": 1}}},
        ]
    )

    by_status = {
        str(doc["_id"] or "Unknown"): doc["count"] async for doc in by_status_cursor
    }
    by_priority = {
        str(doc["_id"] or "Unknown"): doc["count"] async for doc in by_priority_cursor
    }

    return {
        "total": total,
        "open": total - completed,
        "completed": completed,
        "overdue": overdue,
        "by_status": by_status,
        "by_priority": by_priority,
    }


# ── Create ────────────────────────────────────────────────────────────────────


async def create(db: AsyncIOMotorDatabase, data: WorkOrderCreate) -> dict:
    doc = _prepare(data.model_dump())

    # compute is_overdue at creation time
    doc["is_overdue"] = _compute_is_overdue(
        doc.get("expected_completion"),
        doc.get("status"),
    )

    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


# ── Update ────────────────────────────────────────────────────────────────────


async def update(
    db: AsyncIOMotorDatabase,
    work_order_id: str,
    data: WorkOrderUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))

    if not fields:
        return await get_by_id(db, work_order_id)

    # Fetch existing doc to merge missing fields
    existing = await db[COLLECTION].find_one({"work_order_id": work_order_id})
    if not existing:
        return None

    expected = fields.get("expected_completion", existing.get("expected_completion"))
    status = fields.get("status", existing.get("status"))

    fields["is_overdue"] = _compute_is_overdue(expected, status)

    result = await db[COLLECTION].find_one_and_update(
        {"work_order_id": work_order_id},
        {"$set": fields},
        return_document=True,
    )

    return _serialize(result) if result else None


async def add_subtask(
    db: AsyncIOMotorDatabase,
    work_order_id: str,
    data: WorkOrderSubtaskCreate,
) -> Optional[dict]:
    parent = await get_by_id(db, work_order_id)
    if not parent:
        return None

    doc = _prepare(data.model_dump())
    doc["parent_work_order_id"] = work_order_id
    doc["is_overdue"] = _compute_is_overdue(
        doc.get("expected_completion"),
        doc.get("status"),
    )
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update_subtask(
    db: AsyncIOMotorDatabase,
    work_order_id: str,
    subtask_work_order_id: str,
    data: WorkOrderSubtaskUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        doc = await db[COLLECTION].find_one(
            {
                "work_order_id": subtask_work_order_id,
                "parent_work_order_id": work_order_id,
            }
        )
        return _serialize(doc) if doc else None

    existing = await db[COLLECTION].find_one(
        {"work_order_id": subtask_work_order_id, "parent_work_order_id": work_order_id}
    )
    if not existing:
        return None

    expected = fields.get("expected_completion", existing.get("expected_completion"))
    status = fields.get("status", existing.get("status"))
    fields["is_overdue"] = _compute_is_overdue(expected, status)

    result = await db[COLLECTION].find_one_and_update(
        {"work_order_id": subtask_work_order_id, "parent_work_order_id": work_order_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def remove_subtask(
    db: AsyncIOMotorDatabase,
    work_order_id: str,
    subtask_work_order_id: str,
) -> bool:
    result = await db[COLLECTION].delete_one(
        {"work_order_id": subtask_work_order_id, "parent_work_order_id": work_order_id}
    )
    return result.deleted_count == 1


# ── Delete ────────────────────────────────────────────────────────────────────


async def delete(db: AsyncIOMotorDatabase, work_order_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"work_order_id": work_order_id})
    return result.deleted_count == 1
