from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.workorders.models.batch_release_workflows import (
    BatchReleaseWorkflow,
    BatchReleaseWorkflowCreate,
    BatchReleaseWorkflowUpdate,
)

COLLECTION = "batch_release_workflows"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _to_utc(value):
    if not value:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return value


def _prepare(doc: dict) -> dict:
    result = {}
    for key, value in doc.items():
        if isinstance(value, datetime):
            result[key] = _to_utc(value)
        elif isinstance(value, dict):
            result[key] = _prepare(value)
        elif isinstance(value, list):
            result[key] = [
                _prepare(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            result[key] = value
    return result


async def _attach_to_batch_record(db: AsyncIOMotorDatabase, record: dict) -> None:
    batch_execution_record_id = record.get("batch_execution_record_id")
    workflow_id = record.get("batch_release_workflow_id")
    if not batch_execution_record_id or not workflow_id:
        return

    release_snapshot = {
        "batch_release_workflow_id": workflow_id,
        "status": record.get("status"),
        "release_decision": record.get("release_decision"),
        "qa_reviewer_id": record.get("qa_reviewer_id"),
        "released_by": record.get("released_by"),
        "released_at": record.get("released_at"),
        "rejected_by": record.get("rejected_by"),
        "rejected_at": record.get("rejected_at"),
        "rejection_reason": record.get("rejection_reason"),
        "executed_bmr_package": record.get("executed_bmr_package"),
        "executed_bpr_package": record.get("executed_bpr_package"),
    }
    await db.batch_production_execution_records.update_one(
        {"batch_execution_record_id": batch_execution_record_id},
        {
            "$set": {
                "metadata.batch_release_workflow": release_snapshot,
                "metadata.bmr_package.execution_status": (
                    record.get("executed_bmr_package") or {}
                ).get("status"),
                "metadata.bpr_package.execution_status": (
                    record.get("executed_bpr_package") or {}
                ).get("status"),
            },
            "$addToSet": {
                "metadata.batch_record_package.batch_release_workflow_ids": workflow_id,
                "metadata.bmr_package.batch_release_workflow_ids": workflow_id,
                "metadata.bpr_package.batch_release_workflow_ids": workflow_id,
                "evidence_document_ids": {
                    "$each": record.get("evidence_document_ids") or []
                },
            },
        },
    )


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    batch_execution_record_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    status: Optional[str] = None,
    release_decision: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if batch_execution_record_id:
        query["batch_execution_record_id"] = batch_execution_record_id
    if batch_id:
        query["batch_id"] = batch_id
    if status:
        query["status"] = status
    if release_decision:
        query["release_decision"] = release_decision

    total = await db[COLLECTION].count_documents(query)
    cursor = (
        db[COLLECTION]
        .find(query)
        .sort("updated_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase, batch_release_workflow_id: str
) -> Optional[dict]:
    doc = await db[COLLECTION].find_one(
        {"batch_release_workflow_id": batch_release_workflow_id}
    )
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: BatchReleaseWorkflowCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    await _attach_to_batch_record(db, doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    batch_release_workflow_id: str,
    data: BatchReleaseWorkflowUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, batch_release_workflow_id)

    existing = await db[COLLECTION].find_one(
        {"batch_release_workflow_id": batch_release_workflow_id}
    )
    if not existing:
        return None
    merged = _serialize(existing)
    merged.update(fields)
    validated = BatchReleaseWorkflow(**merged)
    fields = _prepare(validated.model_dump(exclude_unset=True))
    result = await db[COLLECTION].find_one_and_update(
        {"batch_release_workflow_id": batch_release_workflow_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    if result:
        serialized = _serialize(result)
        await _attach_to_batch_record(db, serialized)
        return serialized
    return None


async def delete(db: AsyncIOMotorDatabase, batch_release_workflow_id: str) -> bool:
    result = await db[COLLECTION].delete_one(
        {"batch_release_workflow_id": batch_release_workflow_id}
    )
    return result.deleted_count == 1
