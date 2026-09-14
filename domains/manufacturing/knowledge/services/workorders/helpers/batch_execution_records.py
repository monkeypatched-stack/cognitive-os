from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.workorders.models.batch_execution_records import (
    BatchProductionExecutionRecord,
    BatchProductionExecutionRecordCreate,
    BatchProductionExecutionRecordUpdate,
)

COLLECTION = "batch_production_execution_records"

EXECUTION_STATUS_TO_BATCH_STATUS = {
    "Draft": "Created",
    "Released": "Released",
    "In Progress": "In Production",
    "On Hold": "On Hold",
    "Completed": "Completed",
    "Under QA Review": "Under QA Review",
    "Approved": "Approved",
    "Rejected": "Rejected",
    "Cancelled": "Cancelled",
}


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _ordered_union(*values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for items in values:
        for item in items or []:
            if item and item not in seen:
                seen.add(item)
                result.append(item)
    return result


async def _with_batch_record_package(db: AsyncIOMotorDatabase, doc: dict) -> dict:
    record = _serialize(doc)
    batch_execution_record_id = record.get("batch_execution_record_id")
    if not batch_execution_record_id:
        return record

    linked_cleaning_records = await db.cleaning_records.find(
        {"batch_execution_record_id": batch_execution_record_id},
        {"_id": 0, "record_id": 1, "attachments": 1},
    ).to_list(None)
    cleaning_record_ids = _ordered_union(
        record.get("cleaning_record_ids") or [],
        [
            item["record_id"]
            for item in linked_cleaning_records
            if item.get("record_id")
        ],
    )
    cleaning_attachment_ids = _ordered_union(
        *[item.get("attachments") or [] for item in linked_cleaning_records],
    )

    metadata = dict(record.get("metadata") or {})
    batch_record_package = dict(metadata.get("batch_record_package") or {})
    bmr_package = dict(metadata.get("bmr_package") or {})
    bpr_package = dict(metadata.get("bpr_package") or {})
    dispense_weighing_records = record.get("dispense_weighing_records") or []
    dispense_weighing_record_ids = [
        item.get("dispense_weighing_id")
        for item in dispense_weighing_records
        if isinstance(item, dict) and item.get("dispense_weighing_id")
    ]
    dispense_weighing_evidence_ids = _ordered_union(
        [
            item.get("bmr_document_id")
            for item in dispense_weighing_records
            if isinstance(item, dict) and item.get("bmr_document_id")
        ],
        *[
            item.get("evidence_document_ids") or []
            for item in dispense_weighing_records
            if isinstance(item, dict)
        ],
    )

    for package in (batch_record_package, bmr_package, bpr_package):
        package["cleaning_record_ids"] = _ordered_union(
            package.get("cleaning_record_ids") or [], cleaning_record_ids
        )
        package["cleaning_attachment_ids"] = _ordered_union(
            package.get("cleaning_attachment_ids") or [],
            cleaning_attachment_ids,
        )

    if dispense_weighing_record_ids:
        batch_record_package["dispense_weighing_record_ids"] = _ordered_union(
            batch_record_package.get("dispense_weighing_record_ids") or [],
            dispense_weighing_record_ids,
        )
        bmr_package["dispense_weighing_record_ids"] = _ordered_union(
            bmr_package.get("dispense_weighing_record_ids") or [],
            dispense_weighing_record_ids,
        )
    if dispense_weighing_evidence_ids:
        batch_record_package["dispense_weighing_evidence_document_ids"] = (
            _ordered_union(
                batch_record_package.get("dispense_weighing_evidence_document_ids")
                or [],
                dispense_weighing_evidence_ids,
            )
        )
        bmr_package["dispense_weighing_evidence_document_ids"] = _ordered_union(
            bmr_package.get("dispense_weighing_evidence_document_ids") or [],
            dispense_weighing_evidence_ids,
        )

    metadata["batch_record_package"] = batch_record_package
    metadata["bmr_package"] = bmr_package
    metadata["bpr_package"] = bpr_package
    record["metadata"] = metadata
    record["cleaning_record_ids"] = cleaning_record_ids
    if cleaning_attachment_ids:
        record["evidence_document_ids"] = _ordered_union(
            record.get("evidence_document_ids") or [],
            cleaning_attachment_ids,
        )
    if dispense_weighing_evidence_ids:
        record["evidence_document_ids"] = _ordered_union(
            record.get("evidence_document_ids") or [],
            dispense_weighing_evidence_ids,
        )
    return record


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


async def _attach_to_production_batch(db: AsyncIOMotorDatabase, record: dict) -> None:
    batch_id = record.get("batch_id")
    batch_execution_record_id = record.get("batch_execution_record_id")
    if not batch_id or not batch_execution_record_id:
        return
    set_fields = {
        "active_batch_execution_record_id": batch_execution_record_id,
        "updated_at": datetime.now(timezone.utc),
    }
    mapped_status = EXECUTION_STATUS_TO_BATCH_STATUS.get(record.get("status"))
    if mapped_status:
        set_fields["status"] = mapped_status
    for source, target in (
        ("product_id", "product_id"),
        ("bom_id", "bom_id"),
        ("process_definition_id", "process_definition_id"),
        ("process_route_id", "process_route_id"),
        ("work_order_id", "work_order_id"),
        ("plant_id", "plant_id"),
        ("line_id", "line_id"),
        ("stage_id", "current_stage_id"),
        ("workstation_id", "current_workstation_id"),
        ("qa_reviewer_id", "qa_reviewer_id"),
    ):
        if record.get(source):
            set_fields[target] = record[source]
    await db.production_batches.update_one(
        {"batch_id": batch_id},
        {
            "$set": set_fields,
            "$addToSet": {"batch_execution_record_ids": batch_execution_record_id},
        },
    )


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    batch_id: Optional[str] = None,
    process_definition_id: Optional[str] = None,
    work_order_id: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if batch_id:
        query["batch_id"] = batch_id
    if process_definition_id:
        query["process_definition_id"] = process_definition_id
    if work_order_id:
        query["work_order_id"] = work_order_id
    if status:
        query["status"] = status

    total = await db[COLLECTION].count_documents(query)
    cursor = (
        db[COLLECTION]
        .find(query)
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [await _with_batch_record_package(db, doc) async for doc in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase, batch_execution_record_id: str
) -> Optional[dict]:
    doc = await db[COLLECTION].find_one(
        {"batch_execution_record_id": batch_execution_record_id}
    )
    return await _with_batch_record_package(db, doc) if doc else None


async def create(
    db: AsyncIOMotorDatabase, data: BatchProductionExecutionRecordCreate
) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    await _attach_to_production_batch(db, doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    batch_execution_record_id: str,
    data: BatchProductionExecutionRecordUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, batch_execution_record_id)

    existing = await db[COLLECTION].find_one(
        {"batch_execution_record_id": batch_execution_record_id}
    )
    if not existing:
        return None

    merged = _serialize(existing)
    merged.update(fields)
    validated = BatchProductionExecutionRecord(**merged)
    fields = _prepare(validated.model_dump(exclude_unset=True))

    result = await db[COLLECTION].find_one_and_update(
        {"batch_execution_record_id": batch_execution_record_id},
        {"$set": fields},
        return_document=True,
    )
    if result:
        serialized = _serialize(result)
        await _attach_to_production_batch(db, serialized)
        return serialized
    return None


async def delete(db: AsyncIOMotorDatabase, batch_execution_record_id: str) -> bool:
    result = await db[COLLECTION].delete_one(
        {"batch_execution_record_id": batch_execution_record_id}
    )
    return result.deleted_count == 1
