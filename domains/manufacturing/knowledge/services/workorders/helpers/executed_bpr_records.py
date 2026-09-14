from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.workorders.models.executed_bpr_records import (
    ExecutedBatchPackagingRecord,
    ExecutedBprRecordCreate,
    ExecutedBprRecordUpdate,
)

COLLECTION = "executed_bpr_records"


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
    executed_bpr_record_id = record.get("executed_bpr_record_id")
    if not batch_execution_record_id or not executed_bpr_record_id:
        return

    package_snapshot = {
        "executed_bpr_record_id": executed_bpr_record_id,
        "executed_bpr_document_id": record.get("executed_bpr_document_id"),
        "packing_instruction_id": record.get("packing_instruction_id"),
        "instruction_template_id": record.get("instruction_template_id"),
        "bpr_template_document_id": record.get("bpr_template_document_id"),
        "template_revision": record.get("template_revision"),
        "generated_from_approved_instruction": record.get(
            "generated_from_approved_instruction"
        ),
        "status": record.get("status"),
        "executed_by": record.get("executed_by"),
        "executed_at": record.get("executed_at"),
        "reviewed_by": record.get("reviewed_by"),
        "reviewed_at": record.get("reviewed_at"),
        "approved_by": record.get("approved_by"),
        "approved_at": record.get("approved_at"),
        "step_execution_ids": [
            step.get("step_execution_id")
            for step in record.get("steps") or []
            if isinstance(step, dict) and step.get("step_execution_id")
        ],
    }
    await db.batch_production_execution_records.update_one(
        {"batch_execution_record_id": batch_execution_record_id},
        {
            "$set": {
                "metadata.executed_bpr_record_id": executed_bpr_record_id,
                "metadata.executed_bpr_document_id": record.get(
                    "executed_bpr_document_id"
                ),
                "metadata.bpr_package.executed_bpr_record_id": executed_bpr_record_id,
                "metadata.bpr_package.executed_bpr_document_id": record.get(
                    "executed_bpr_document_id"
                ),
                "metadata.bpr_package.executed_bpr_status": record.get("status"),
                "metadata.bpr_package.executed_bpr": package_snapshot,
                "metadata.batch_record_package.executed_bpr_record_id": executed_bpr_record_id,
                "metadata.batch_record_package.executed_bpr_document_id": record.get(
                    "executed_bpr_document_id"
                ),
            },
            "$addToSet": {
                "metadata.batch_record_package.executed_bpr_record_ids": executed_bpr_record_id,
                "metadata.bpr_package.executed_bpr_record_ids": executed_bpr_record_id,
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
    packing_instruction_id: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if batch_execution_record_id:
        query["batch_execution_record_id"] = batch_execution_record_id
    if batch_id:
        query["batch_id"] = batch_id
    if status:
        query["status"] = status
    if packing_instruction_id:
        query["packing_instruction_id"] = packing_instruction_id

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
    db: AsyncIOMotorDatabase, executed_bpr_record_id: str
) -> Optional[dict]:
    doc = await db[COLLECTION].find_one(
        {"executed_bpr_record_id": executed_bpr_record_id}
    )
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: ExecutedBprRecordCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    await _attach_to_batch_record(db, doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    executed_bpr_record_id: str,
    data: ExecutedBprRecordUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, executed_bpr_record_id)

    existing = await db[COLLECTION].find_one(
        {"executed_bpr_record_id": executed_bpr_record_id}
    )
    if not existing:
        return None
    merged = _serialize(existing)
    merged.update(fields)
    validated = ExecutedBatchPackagingRecord(**merged)
    fields = _prepare(validated.model_dump(exclude_unset=True))
    result = await db[COLLECTION].find_one_and_update(
        {"executed_bpr_record_id": executed_bpr_record_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    if result:
        serialized = _serialize(result)
        await _attach_to_batch_record(db, serialized)
        return serialized
    return None


async def delete(db: AsyncIOMotorDatabase, executed_bpr_record_id: str) -> bool:
    result = await db[COLLECTION].delete_one(
        {"executed_bpr_record_id": executed_bpr_record_id}
    )
    return result.deleted_count == 1
