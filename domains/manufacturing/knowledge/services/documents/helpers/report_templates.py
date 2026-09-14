from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.documents.models.report_templates import (
    ReportTemplate,
    ReportTemplateCreate,
    ReportTemplateUpdate,
)

COLLECTION = "report_templates"


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


def _prepare(value):
    if isinstance(value, datetime):
        return _to_utc(value)
    if isinstance(value, dict):
        return {key: _prepare(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare(item) for item in value]
    return value


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    template_type: Optional[str] = None,
    process_definition_id: Optional[str] = None,
    batch_execution_record_id: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if template_type:
        query["template_type"] = template_type
    if process_definition_id:
        query["process_definition_id"] = process_definition_id
    if batch_execution_record_id:
        query["batch_execution_record_id"] = batch_execution_record_id
    if status:
        query["status"] = status
    total = await db[COLLECTION].count_documents(query)
    cursor = (
        db[COLLECTION]
        .find(query)
        .sort([("template_type", 1), ("name", 1), ("version", -1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase, report_template_id: str
) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"report_template_id": report_template_id})
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: ReportTemplateCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    report_template_id: str,
    data: ReportTemplateUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, report_template_id)

    existing = await db[COLLECTION].find_one({"report_template_id": report_template_id})
    if not existing:
        return None

    merged = _serialize(existing)
    merged.update(fields)
    validated = ReportTemplate(**merged)
    fields = _prepare(validated.model_dump(exclude_unset=True))

    result = await db[COLLECTION].find_one_and_update(
        {"report_template_id": report_template_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, report_template_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"report_template_id": report_template_id})
    return result.deleted_count == 1
