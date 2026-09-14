from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.workorders.models.executed_instruction_evidence import (
    ExecutedInstructionEvidence,
    ExecutedInstructionEvidenceCreate,
    ExecutedInstructionEvidenceUpdate,
)

COLLECTION = "executed_instruction_evidence"


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


async def _attach_to_batch_step_and_record(
    db: AsyncIOMotorDatabase, record: dict
) -> None:
    evidence_id = record.get("executed_instruction_evidence_id")
    batch_step_execution_id = record.get("batch_step_execution_id")
    batch_execution_record_id = record.get("batch_execution_record_id")
    source_record_type = str(record.get("source_record_type") or "").upper()
    evidence_document_ids = record.get("evidence_document_ids") or []
    if batch_step_execution_id and evidence_id:
        await db.batch_step_executions.update_one(
            {"batch_step_execution_id": batch_step_execution_id},
            {
                "$set": {
                    "metadata.executed_instruction_evidence_id": evidence_id,
                    "metadata.executed_instruction_status": record.get("status"),
                },
                "$addToSet": {
                    "metadata.executed_instruction_evidence_ids": evidence_id,
                    "evidence_document_ids": {"$each": evidence_document_ids},
                },
            },
        )
    if batch_execution_record_id and evidence_id:
        package_key = (
            "metadata.bpr_package.executed_instruction_evidence_ids"
            if source_record_type == "BPR"
            else "metadata.bmr_package.executed_instruction_evidence_ids"
        )
        await db.batch_production_execution_records.update_one(
            {"batch_execution_record_id": batch_execution_record_id},
            {
                "$addToSet": {
                    "metadata.batch_record_package.executed_instruction_evidence_ids": evidence_id,
                    package_key: evidence_id,
                    "evidence_document_ids": {"$each": evidence_document_ids},
                }
            },
        )


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    batch_step_execution_id: Optional[str] = None,
    batch_execution_record_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if batch_step_execution_id:
        query["batch_step_execution_id"] = batch_step_execution_id
    if batch_execution_record_id:
        query["batch_execution_record_id"] = batch_execution_record_id
    if batch_id:
        query["batch_id"] = batch_id
    if status:
        query["status"] = status

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
    db: AsyncIOMotorDatabase, executed_instruction_evidence_id: str
) -> Optional[dict]:
    doc = await db[COLLECTION].find_one(
        {"executed_instruction_evidence_id": executed_instruction_evidence_id}
    )
    return _serialize(doc) if doc else None


async def create(
    db: AsyncIOMotorDatabase, data: ExecutedInstructionEvidenceCreate
) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    await _attach_to_batch_step_and_record(db, doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    executed_instruction_evidence_id: str,
    data: ExecutedInstructionEvidenceUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, executed_instruction_evidence_id)

    existing = await db[COLLECTION].find_one(
        {"executed_instruction_evidence_id": executed_instruction_evidence_id}
    )
    if not existing:
        return None
    merged = _serialize(existing)
    merged.update(fields)
    validated = ExecutedInstructionEvidence(**merged)
    fields = _prepare(validated.model_dump(exclude_unset=True))
    result = await db[COLLECTION].find_one_and_update(
        {"executed_instruction_evidence_id": executed_instruction_evidence_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    if result:
        serialized = _serialize(result)
        await _attach_to_batch_step_and_record(db, serialized)
        return serialized
    return None


async def delete(
    db: AsyncIOMotorDatabase, executed_instruction_evidence_id: str
) -> bool:
    result = await db[COLLECTION].delete_one(
        {"executed_instruction_evidence_id": executed_instruction_evidence_id}
    )
    return result.deleted_count == 1
