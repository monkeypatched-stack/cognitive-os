from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.workorders.models.ipc_result_records import (
    IpcResultRecord,
    IpcResultRecordCreate,
    IpcResultRecordUpdate,
)

COLLECTION = "ipc_result_records"


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
    ipc_result_id = record.get("ipc_result_id")
    if not batch_execution_record_id or not ipc_result_id:
        return
    await db.batch_production_execution_records.update_one(
        {"batch_execution_record_id": batch_execution_record_id},
        {
            "$addToSet": {
                "metadata.batch_record_package.ipc_result_record_ids": ipc_result_id,
                "metadata.bmr_package.ipc_result_record_ids": ipc_result_id,
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
    batch_step_execution_id: Optional[str] = None,
    ipc_checkpoint_id: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if batch_execution_record_id:
        query["batch_execution_record_id"] = batch_execution_record_id
    if batch_step_execution_id:
        query["batch_step_execution_id"] = batch_step_execution_id
    if ipc_checkpoint_id:
        query["ipc_checkpoint_id"] = ipc_checkpoint_id
    if status:
        query["status"] = status

    total = await db[COLLECTION].count_documents(query)
    cursor = (
        db[COLLECTION]
        .find(query)
        .sort("tested_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, ipc_result_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"ipc_result_id": ipc_result_id})
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: IpcResultRecordCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    await _attach_to_batch_record(db, doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, ipc_result_id: str, data: IpcResultRecordUpdate
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, ipc_result_id)

    existing = await db[COLLECTION].find_one({"ipc_result_id": ipc_result_id})
    if not existing:
        return None
    merged = _serialize(existing)
    merged.update(fields)
    validated = IpcResultRecord(**merged)
    fields = _prepare(validated.model_dump(exclude_unset=True))
    result = await db[COLLECTION].find_one_and_update(
        {"ipc_result_id": ipc_result_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    if result:
        serialized = _serialize(result)
        await _attach_to_batch_record(db, serialized)
        return serialized
    return None


async def delete(db: AsyncIOMotorDatabase, ipc_result_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"ipc_result_id": ipc_result_id})
    return result.deleted_count == 1
