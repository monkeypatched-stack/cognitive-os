from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.changeover.helpers.changeover_common import (
    create,
    delete,
    dump,
    get_all,
    get_by_id,
    serialize,
    update,
)
from services.changeover.models.changeover_procedures import (
    ChangeoverProcedureCreate,
    ChangeoverProcedureUpdate,
)
from services.changeover.models.changeover_tasks import ChangeoverTaskUpdate
from services.common.models.enums import ChangeoverStatus, TaskStatus

COLLECTION = "changeover_procedures"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_is_complete(task: dict) -> bool:
    return bool(task.get("is_complete")) or task.get("status") == TaskStatus.DONE.value


def _procedure_completion_fields(tasks: list[dict]) -> dict:
    if tasks and all(_task_is_complete(task) for task in tasks):
        return {
            "status": ChangeoverStatus.COMPLETED.value,
            "completed_at": _now(),
        }
    return {
        "status": ChangeoverStatus.IN_PROGRESS.value,
        "completed_at": None,
    }


def _apply_task_completion(task: dict, is_complete: bool) -> None:
    task["is_complete"] = is_complete
    if is_complete:
        now = _now()
        task["status"] = TaskStatus.DONE.value
        if not task.get("started_at"):
            task["started_at"] = now
        task["completed_at"] = task.get("completed_at") or now
        task["blocked_reason"] = None
    else:
        task["status"] = TaskStatus.PENDING.value
        task["completed_at"] = None


async def get_all_procedures(
    db: AsyncIOMotorDatabase, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    return await get_all(db, COLLECTION, page, page_size)


async def get_procedure_by_id(
    db: AsyncIOMotorDatabase, procedure_id: str
) -> Optional[dict]:
    return await get_by_id(db, COLLECTION, procedure_id)


async def get_procedures_by_factory(
    db: AsyncIOMotorDatabase, factory_id: str
) -> list[dict]:
    records, _ = await get_all(
        db,
        COLLECTION,
        query={"$or": [{"plant_id": factory_id}, {"factory_id": factory_id}]},
        page_size=1000,
    )
    return records


async def get_procedures_by_plant(
    db: AsyncIOMotorDatabase, plant_id: str
) -> list[dict]:
    records, _ = await get_all(
        db, COLLECTION, query={"plant_id": plant_id}, page_size=1000
    )
    return records


async def create_procedure(
    db: AsyncIOMotorDatabase, data: ChangeoverProcedureCreate
) -> dict:
    doc = await create(db, COLLECTION, data)
    completion_fields = _procedure_completion_fields(doc.get("tasks", []))
    if completion_fields["status"] == ChangeoverStatus.COMPLETED.value:
        await db[COLLECTION].update_one({"id": doc["id"]}, {"$set": completion_fields})
        doc.update(completion_fields)
    return doc


async def update_procedure(
    db: AsyncIOMotorDatabase, procedure_id: str, data: ChangeoverProcedureUpdate
) -> Optional[dict]:
    updated = await update(db, COLLECTION, procedure_id, data)
    if not updated or "tasks" not in data.model_dump(exclude_unset=True):
        return updated

    completion_fields = _procedure_completion_fields(updated.get("tasks", []))
    result = await db[COLLECTION].find_one_and_update(
        {"id": procedure_id},
        {"$set": completion_fields},
        return_document=ReturnDocument.AFTER,
    )
    return serialize(result) if result else None


async def update_procedure_task(
    db: AsyncIOMotorDatabase,
    procedure_id: str,
    task_id: str,
    data: ChangeoverTaskUpdate,
) -> Optional[dict]:
    procedure = await get_procedure_by_id(db, procedure_id)
    if not procedure:
        return None

    tasks = procedure.get("tasks", [])
    fields = dump(data)
    matched = False
    for task in tasks:
        if str(task.get("id")) != task_id:
            continue
        matched = True
        task.update(fields)
        if fields.get("is_complete") is not None:
            _apply_task_completion(task, fields["is_complete"])
        elif fields.get("status") == TaskStatus.DONE.value:
            _apply_task_completion(task, True)
        elif fields.get("completed_at") is not None:
            task["is_complete"] = True
        break

    if not matched:
        return None

    update_fields = {"tasks": tasks, **_procedure_completion_fields(tasks)}
    result = await db[COLLECTION].find_one_and_update(
        {"id": procedure_id},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
    return serialize(result) if result else None


async def set_procedure_task_complete(
    db: AsyncIOMotorDatabase,
    procedure_id: str,
    task_id: str,
    is_complete: bool,
) -> Optional[dict]:
    procedure = await get_procedure_by_id(db, procedure_id)
    if not procedure:
        return None

    tasks = procedure.get("tasks", [])
    matched = False
    for task in tasks:
        if str(task.get("id")) == task_id:
            matched = True
            _apply_task_completion(task, is_complete)
            break

    if not matched:
        return None

    update_fields = {"tasks": tasks, **_procedure_completion_fields(tasks)}
    result = await db[COLLECTION].find_one_and_update(
        {"id": procedure_id},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
    return serialize(result) if result else None


async def delete_procedure(db: AsyncIOMotorDatabase, procedure_id: str) -> bool:
    return await delete(db, COLLECTION, procedure_id)
