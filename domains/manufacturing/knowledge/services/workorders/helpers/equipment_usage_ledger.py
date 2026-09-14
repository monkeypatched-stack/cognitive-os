from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.workorders.models.equipment_usage_ledger import (
    EquipmentUsageLedgerCreate,
    EquipmentUsageLedgerEntry,
    EquipmentUsageLedgerUpdate,
)

COLLECTION = "equipment_usage_ledger"


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


def _package_updates(record: dict) -> dict:
    usage_id = record.get("usage_id")
    evidence_document_ids = [
        item for item in record.get("evidence_document_ids") or [] if item
    ]
    add_to_set: dict = {}
    if usage_id:
        add_to_set["equipment_usage_ids"] = usage_id
        add_to_set["metadata.required_equipment_usage_ids"] = usage_id
        add_to_set["metadata.batch_record_package.equipment_usage_ids"] = usage_id
        add_to_set["metadata.batch_record_package.required_equipment_usage_ids"] = (
            usage_id
        )
        add_to_set["metadata.bmr_package.equipment_usage_ids"] = usage_id
        add_to_set["metadata.bmr_package.required_equipment_usage_ids"] = usage_id
        add_to_set["metadata.bpr_package.equipment_usage_ids"] = usage_id
        add_to_set["metadata.bpr_package.required_equipment_usage_ids"] = usage_id
    if evidence_document_ids:
        add_to_set["evidence_document_ids"] = {"$each": evidence_document_ids}
        add_to_set[
            "metadata.batch_record_package.equipment_usage_evidence_document_ids"
        ] = {"$each": evidence_document_ids}
        add_to_set["metadata.bmr_package.equipment_usage_evidence_document_ids"] = {
            "$each": evidence_document_ids
        }
        add_to_set["metadata.bpr_package.equipment_usage_evidence_document_ids"] = {
            "$each": evidence_document_ids
        }
    return {"$addToSet": add_to_set} if add_to_set else {}


async def _attach_to_batch_record(db: AsyncIOMotorDatabase, record: dict) -> None:
    batch_execution_record_id = record.get("batch_execution_record_id")
    if not batch_execution_record_id:
        return
    updates = _package_updates(record)
    if updates:
        await db.batch_production_execution_records.update_one(
            {"batch_execution_record_id": batch_execution_record_id},
            updates,
        )


def _package_removals(record: dict) -> dict:
    usage_id = record.get("usage_id")
    evidence_document_ids = [
        item for item in record.get("evidence_document_ids") or [] if item
    ]
    pull: dict = {}
    if usage_id:
        pull["equipment_usage_ids"] = usage_id
        pull["metadata.required_equipment_usage_ids"] = usage_id
        pull["metadata.batch_record_package.equipment_usage_ids"] = usage_id
        pull["metadata.batch_record_package.required_equipment_usage_ids"] = usage_id
        pull["metadata.bmr_package.equipment_usage_ids"] = usage_id
        pull["metadata.bmr_package.required_equipment_usage_ids"] = usage_id
        pull["metadata.bpr_package.equipment_usage_ids"] = usage_id
        pull["metadata.bpr_package.required_equipment_usage_ids"] = usage_id
    if evidence_document_ids:
        pull["evidence_document_ids"] = {"$in": evidence_document_ids}
        pull["metadata.batch_record_package.equipment_usage_evidence_document_ids"] = {
            "$in": evidence_document_ids
        }
        pull["metadata.bmr_package.equipment_usage_evidence_document_ids"] = {
            "$in": evidence_document_ids
        }
        pull["metadata.bpr_package.equipment_usage_evidence_document_ids"] = {
            "$in": evidence_document_ids
        }
    return {"$pull": pull} if pull else {}


async def _detach_from_batch_record(db: AsyncIOMotorDatabase, record: dict) -> None:
    batch_execution_record_id = record.get("batch_execution_record_id")
    if not batch_execution_record_id:
        return
    removals = _package_removals(record)
    if removals:
        await db.batch_production_execution_records.update_one(
            {"batch_execution_record_id": batch_execution_record_id},
            removals,
        )


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    batch_execution_record_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    process_step_id: Optional[str] = None,
    operator_id: Optional[str] = None,
    equipment_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    started_from: Optional[datetime] = None,
    started_to: Optional[datetime] = None,
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
    if equipment_id:
        query["equipment_id"] = equipment_id
    if machine_id:
        query["machine_id"] = machine_id
    if status:
        query["status"] = status
    time_filter: dict = {}
    if started_from:
        time_filter["$gte"] = _to_utc(started_from)
    if started_to:
        time_filter["$lte"] = _to_utc(started_to)
    if time_filter:
        query["started_at"] = time_filter

    total = await db[COLLECTION].count_documents(query)
    cursor = (
        db[COLLECTION]
        .find(query)
        .sort("started_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, usage_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"usage_id": usage_id})
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: EquipmentUsageLedgerCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    await _attach_to_batch_record(db, doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    usage_id: str,
    data: EquipmentUsageLedgerUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, usage_id)

    existing = await db[COLLECTION].find_one({"usage_id": usage_id})
    if not existing:
        return None

    merged = _serialize(existing)
    merged.update(fields)
    validated = EquipmentUsageLedgerEntry(**merged)
    fields = _prepare(validated.model_dump(exclude_unset=True))

    result = await db[COLLECTION].find_one_and_update(
        {"usage_id": usage_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, usage_id: str) -> bool:
    existing = await db[COLLECTION].find_one({"usage_id": usage_id})
    if not existing:
        return False
    result = await db[COLLECTION].delete_one({"usage_id": usage_id})
    if result.deleted_count == 1:
        await _detach_from_batch_record(db, existing)
        return True
    return False
