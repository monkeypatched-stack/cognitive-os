from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.workorders.models.batch_step_executions import (
    BatchStepExecution,
    BatchStepExecutionCreate,
    BatchStepExecutionUpdate,
)

COLLECTION = "batch_step_executions"


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
    step_execution_id = record.get("batch_step_execution_id")
    if not batch_execution_record_id or not step_execution_id:
        return
    executed_instruction_evidence_ids = (
        record.get("executed_instruction_evidence_ids") or []
    )
    await db.batch_production_execution_records.update_one(
        {"batch_execution_record_id": batch_execution_record_id},
        {
            "$addToSet": {
                "metadata.batch_record_package.batch_step_execution_ids": step_execution_id,
                "metadata.bmr_package.batch_step_execution_ids": step_execution_id,
                "metadata.bpr_package.batch_step_execution_ids": step_execution_id,
                "metadata.batch_record_package.executed_instruction_evidence_ids": {
                    "$each": executed_instruction_evidence_ids
                },
                "metadata.bmr_package.executed_instruction_evidence_ids": {
                    "$each": executed_instruction_evidence_ids
                },
                "evidence_document_ids": {
                    "$each": record.get("evidence_document_ids") or []
                },
            }
        },
    )


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    batch_execution_record_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    process_step_id: Optional[str] = None,
    operator_id: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if batch_execution_record_id:
        query["batch_execution_record_id"] = batch_execution_record_id
    if batch_id:
        query["batch_id"] = batch_id
    if process_step_id:
        query["process_step_id"] = process_step_id
    if operator_id:
        query["operator_id"] = operator_id
    if status:
        query["status"] = status

    total = await db[COLLECTION].count_documents(query)
    cursor = (
        db[COLLECTION]
        .find(query)
        .sort([("batch_id", 1), ("sequence", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase, batch_step_execution_id: str
) -> Optional[dict]:
    doc = await db[COLLECTION].find_one(
        {"batch_step_execution_id": batch_step_execution_id}
    )
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: BatchStepExecutionCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    await _attach_to_batch_record(db, doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    batch_step_execution_id: str,
    data: BatchStepExecutionUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, batch_step_execution_id)

    existing = await db[COLLECTION].find_one(
        {"batch_step_execution_id": batch_step_execution_id}
    )
    if not existing:
        return None
    merged = _serialize(existing)
    merged.update(fields)
    validated = BatchStepExecution(**merged)
    fields = _prepare(validated.model_dump(exclude_unset=True))
    result = await db[COLLECTION].find_one_and_update(
        {"batch_step_execution_id": batch_step_execution_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    if result:
        serialized = _serialize(result)
        await _attach_to_batch_record(db, serialized)
        return serialized
    return None


async def delete(db: AsyncIOMotorDatabase, batch_step_execution_id: str) -> bool:
    result = await db[COLLECTION].delete_one(
        {"batch_step_execution_id": batch_step_execution_id}
    )
    return result.deleted_count == 1
