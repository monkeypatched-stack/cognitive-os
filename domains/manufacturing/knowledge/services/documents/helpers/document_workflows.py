from datetime import date, datetime, timezone
from typing import Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.documents.models.document_workflows import (
    DocumentWorkflowCreate,
    DocumentWorkflowResponse,
    DocumentWorkflowStep,
    DocumentWorkflowStepStatus,
    DocumentWorkflowStepUpdate,
    DocumentWorkflowUpdate,
    DocumentWorkflowStatus,
    utc_now,
)

COLLECTION = "document_workflows"


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
        return {key: _prepare(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare(item) for item in value]
    return value


def _as_response_doc(data: dict) -> dict:
    return _prepare(DocumentWorkflowResponse(**data).model_dump())


def _next_current_step_id(steps: list[dict]) -> str | None:
    active_statuses = {
        DocumentWorkflowStepStatus.PENDING.value,
        DocumentWorkflowStepStatus.IN_PROGRESS.value,
    }
    active = [
        step
        for step in sorted(steps, key=lambda item: item.get("sequence", 0))
        if step.get("status") in active_statuses
    ]
    return active[0]["step_id"] if active else None


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_workflow_id(
    db: AsyncIOMotorDatabase,
    workflow_id: str,
) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"workflow_id": workflow_id}))


async def get_by_document_id(db: AsyncIOMotorDatabase, document_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"document_id": document_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    cursor = db[COLLECTION].find({"status": status})
    return [_serialize(doc) async for doc in cursor]


async def get_by_assignee(db: AsyncIOMotorDatabase, assignee_id: str) -> list[dict]:
    cursor = db[COLLECTION].find(
        {
            "$or": [
                {"owner_id": assignee_id},
                {"reviewer_ids": assignee_id},
                {"approver_ids": assignee_id},
                {"steps.assignee_id": assignee_id},
            ]
        }
    )
    return [_serialize(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: DocumentWorkflowCreate) -> dict:
    now = utc_now()
    workflow_id = f"document-workflow-{uuid4().hex[:12]}"
    doc = _as_response_doc(
        {
            **data.model_dump(),
            "workflow_id": workflow_id,
            "updated_at": now,
        }
    )
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    workflow_id: str,
    data: DocumentWorkflowUpdate,
) -> Optional[dict]:
    existing = await get_by_workflow_id(db, workflow_id)
    if not existing:
        return None
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return existing
    merged = {
        **existing,
        **fields,
        "workflow_id": workflow_id,
        "updated_at": utc_now(),
    }
    doc = _as_response_doc(merged)
    result = await db[COLLECTION].find_one_and_update(
        {"workflow_id": workflow_id},
        {"$set": doc},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def add_step(
    db: AsyncIOMotorDatabase,
    workflow_id: str,
    step: DocumentWorkflowStep,
) -> Optional[dict]:
    existing = await get_by_workflow_id(db, workflow_id)
    if not existing:
        return None
    steps = [*existing.get("steps", []), _prepare(step.model_dump())]
    updates = {
        "steps": steps,
        "current_step_id": existing.get("current_step_id")
        or _next_current_step_id(steps),
        "updated_at": utc_now(),
    }
    result = await db[COLLECTION].find_one_and_update(
        {"workflow_id": workflow_id},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def update_step(
    db: AsyncIOMotorDatabase,
    workflow_id: str,
    step_id: str,
    data: DocumentWorkflowStepUpdate,
) -> Optional[dict]:
    existing = await get_by_workflow_id(db, workflow_id)
    if not existing:
        return None
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return existing

    steps = []
    found = False
    for step in existing.get("steps", []):
        if step.get("step_id") == step_id:
            found = True
            steps.append({**step, **fields})
        else:
            steps.append(step)
    if not found:
        return None

    status = existing.get("status")
    if steps and all(
        step.get("status")
        in {
            DocumentWorkflowStepStatus.APPROVED.value,
            DocumentWorkflowStepStatus.SKIPPED.value,
        }
        for step in steps
    ):
        status = DocumentWorkflowStatus.APPROVED.value

    updates = {
        "steps": steps,
        "status": status,
        "current_step_id": _next_current_step_id(steps),
        "updated_at": utc_now(),
    }
    if status == DocumentWorkflowStatus.APPROVED.value:
        updates["completed_at"] = utc_now()

    result = await db[COLLECTION].find_one_and_update(
        {"workflow_id": workflow_id},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, workflow_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"workflow_id": workflow_id})
    return result.deleted_count == 1
