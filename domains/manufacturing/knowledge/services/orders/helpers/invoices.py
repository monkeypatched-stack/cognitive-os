from datetime import date, datetime, timezone
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.orders.models.invoices import (
    InvoiceCreate,
    InvoiceResponse,
    InvoiceUpdate,
    utc_now,
)

COLLECTION = "invoices"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


def _prepare(value):
    if isinstance(value, ObjectId):
        return str(value)
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


def _as_response_doc(data: dict) -> dict:
    return _prepare(InvoiceResponse(**data).model_dump())


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, invoice_id: str) -> Optional[dict]:
    try:
        query = {"_id": ObjectId(invoice_id)}
    except Exception:
        return None
    return _serialize(await db[COLLECTION].find_one(query))


async def get_by_invoice_number(
    db: AsyncIOMotorDatabase, invoice_no: str
) -> Optional[dict]:
    return _serialize(
        await db[COLLECTION].find_one({"invoice_details.invoice_no": invoice_no})
    )


async def get_by_customer_id(db: AsyncIOMotorDatabase, customer_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"buyer.customer_id": customer_id})
    return [_serialize(doc) async for doc in cursor]


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    cursor = db[COLLECTION].find({"status": status})
    return [_serialize(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: InvoiceCreate) -> dict:
    now = utc_now()
    doc = _prepare(
        {
            **data.model_dump(),
            "created_at": now,
            "updated_at": now,
            "current_version": 1,
            "versions": [],
            "send_logs": [],
            "scheduled_sends": [],
            "share_links": [],
            "tracking_events": [],
        }
    )
    result = await db[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase, invoice_id: str, data: InvoiceUpdate
) -> Optional[dict]:
    existing = await get_by_id(db, invoice_id)
    if not existing:
        return None
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return existing
    merged = {
        **existing,
        **fields,
        "id": invoice_id,
        "updated_at": utc_now(),
    }
    doc = _as_response_doc(merged)
    result = await db[COLLECTION].find_one_and_update(
        {"_id": ObjectId(invoice_id)},
        {"$set": {key: value for key, value in doc.items() if key != "id"}},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, invoice_id: str) -> bool:
    try:
        query = {"_id": ObjectId(invoice_id)}
    except Exception:
        return False
    result = await db[COLLECTION].delete_one(query)
    return result.deleted_count == 1
