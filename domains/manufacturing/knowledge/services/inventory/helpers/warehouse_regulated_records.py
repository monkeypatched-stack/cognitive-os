from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from pymongo import ReturnDocument

from services.inventory.models.warehouse_regulated_records import (
    InventoryDispositionRecord,
    InventoryDispositionRecordCreate,
    InventoryDispositionRecordUpdate,
    MaterialInwardLog,
    MaterialInwardLogCreate,
    MaterialInwardLogUpdate,
    SpecialStorageRequirement,
    SpecialStorageRequirementCreate,
    SpecialStorageRequirementUpdate,
    WarehouseDispensingRecord,
    WarehouseDispensingRecordCreate,
    WarehouseDispensingRecordUpdate,
    WarehouseStatusLabel,
    WarehouseStatusLabelCreate,
    WarehouseStatusLabelUpdate,
)
from services.products.models.product_common import utc_now

RECORDS = {
    "special_storage_requirements": (
        "special_storage_requirements",
        "requirement_id",
        SpecialStorageRequirement,
        SpecialStorageRequirementCreate,
        SpecialStorageRequirementUpdate,
    ),
    "material_inward_logs": (
        "material_inward_logs",
        "inward_log_id",
        MaterialInwardLog,
        MaterialInwardLogCreate,
        MaterialInwardLogUpdate,
    ),
    "warehouse_status_labels": (
        "warehouse_status_labels",
        "label_id",
        WarehouseStatusLabel,
        WarehouseStatusLabelCreate,
        WarehouseStatusLabelUpdate,
    ),
    "warehouse_dispensing_records": (
        "warehouse_dispensing_records",
        "dispensing_record_id",
        WarehouseDispensingRecord,
        WarehouseDispensingRecordCreate,
        WarehouseDispensingRecordUpdate,
    ),
    "inventory_disposition_records": (
        "inventory_disposition_records",
        "disposition_id",
        InventoryDispositionRecord,
        InventoryDispositionRecordCreate,
        InventoryDispositionRecordUpdate,
    ),
}


def _record_config(record_type: str):
    try:
        return RECORDS[record_type]
    except KeyError as exc:
        raise ValueError(
            f"Unknown warehouse regulated record type '{record_type}'"
        ) from exc


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _prepare(value):
    if isinstance(value, BaseModel):
        return _prepare(value.model_dump())
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


async def get_all(
    db: AsyncIOMotorDatabase,
    record_type: str,
    *,
    page: int = 1,
    page_size: int = 20,
    query: Optional[dict] = None,
) -> tuple[list[dict], int]:
    collection, _, _, _, _ = _record_config(record_type)
    query = query or {}
    total = await db[collection].count_documents(query)
    cursor = db[collection].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase, record_type: str, record_id: str
) -> Optional[dict]:
    collection, id_field, _, _, _ = _record_config(record_type)
    return _serialize(await db[collection].find_one({id_field: record_id}))


async def create(db: AsyncIOMotorDatabase, record_type: str, data: BaseModel) -> dict:
    collection, _, model_cls, _, _ = _record_config(record_type)
    doc = _prepare(model_cls(**data.model_dump()))
    await db[collection].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    record_type: str,
    record_id: str,
    data: BaseModel,
) -> Optional[dict]:
    collection, id_field, model_cls, _, _ = _record_config(record_type)
    existing = await get_by_id(db, record_type, record_id)
    if not existing:
        return None
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return existing
    merged = {**existing, **fields, id_field: record_id, "updated_at": utc_now()}
    doc = _prepare(model_cls(**merged))
    result = await db[collection].find_one_and_update(
        {id_field: record_id},
        {"$set": doc},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, record_type: str, record_id: str) -> bool:
    collection, id_field, _, _, _ = _record_config(record_type)
    result = await db[collection].delete_one({id_field: record_id})
    return result.deleted_count == 1
