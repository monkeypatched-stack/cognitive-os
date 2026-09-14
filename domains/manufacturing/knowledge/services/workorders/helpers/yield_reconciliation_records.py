from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.workorders.models.yield_reconciliation_records import (
    YieldReconciliationRecord,
    YieldReconciliationRecordCreate,
    YieldReconciliationRecordUpdate,
)

COLLECTION = "yield_reconciliation_records"


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
    reconciliation_id = record.get("yield_reconciliation_id")
    if not batch_execution_record_id or not reconciliation_id:
        return
    await db.batch_production_execution_records.update_one(
        {"batch_execution_record_id": batch_execution_record_id},
        {
            "$set": {
                "yield_reconciliation": {
                    "planned_batch_quantity": record.get("planned_batch_quantity"),
                    "theoretical_yield": record.get("theoretical_yield"),
                    "actual_batch_quantity": record.get("actual_batch_quantity"),
                    "actual_yield": record.get("actual_yield"),
                    "rejected_quantity": record.get("rejected_quantity"),
                    "scrap_quantity": record.get("scrap_quantity"),
                    "unit": record.get("unit"),
                    "variance_percent": record.get("variance_percent"),
                    "execution_status": record.get("execution_status"),
                    "executed_by": record.get("executed_by"),
                    "executed_at": record.get("executed_at"),
                    "disposition": record.get("disposition"),
                    "reviewed_by": record.get("reviewed_by"),
                    "reviewed_at": record.get("reviewed_at"),
                    "planned_vs_actual_comparison": record.get(
                        "planned_vs_actual_comparison"
                    ),
                }
            },
            "$addToSet": {
                "metadata.batch_record_package.yield_reconciliation_record_ids": reconciliation_id,
                "metadata.bmr_package.yield_reconciliation_record_ids": reconciliation_id,
                "metadata.bpr_package.yield_reconciliation_record_ids": reconciliation_id,
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
    disposition: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if batch_execution_record_id:
        query["batch_execution_record_id"] = batch_execution_record_id
    if batch_id:
        query["batch_id"] = batch_id
    if status:
        query["status"] = status
    if disposition:
        query["disposition"] = disposition

    total = await db[COLLECTION].count_documents(query)
    cursor = (
        db[COLLECTION]
        .find(query)
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase, yield_reconciliation_id: str
) -> Optional[dict]:
    doc = await db[COLLECTION].find_one(
        {"yield_reconciliation_id": yield_reconciliation_id}
    )
    return _serialize(doc) if doc else None


async def create(
    db: AsyncIOMotorDatabase, data: YieldReconciliationRecordCreate
) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    # await _attach_to_batch_record(db, doc)  # Temporarily disabled
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    yield_reconciliation_id: str,
    data: YieldReconciliationRecordUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, yield_reconciliation_id)

    existing = await db[COLLECTION].find_one(
        {"yield_reconciliation_id": yield_reconciliation_id}
    )
    if not existing:
        return None
    merged = _serialize(existing)
    merged.update(fields)
    validated = YieldReconciliationRecord(**merged)
    fields = _prepare(validated.model_dump(exclude_unset=True))
    result = await db[COLLECTION].find_one_and_update(
        {"yield_reconciliation_id": yield_reconciliation_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    if result:
        serialized = _serialize(result)
        # await _attach_to_batch_record(db, serialized)  # Temporarily disabled
        return serialized
    return None


async def delete(db: AsyncIOMotorDatabase, yield_reconciliation_id: str) -> bool:
    result = await db[COLLECTION].delete_one(
        {"yield_reconciliation_id": yield_reconciliation_id}
    )
    return result.deleted_count == 1
