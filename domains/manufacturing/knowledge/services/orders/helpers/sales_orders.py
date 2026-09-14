from datetime import date, datetime, timezone
from typing import Optional
from uuid import uuid4

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.orders.models.sales_orders import (
    SalesOrderCreate,
    SalesOrderResponse,
    SalesOrderUpdate,
)
from services.products.models.product_common import utc_now

COLLECTION = "sales_orders"


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
    return _prepare(SalesOrderResponse(**data).model_dump())


def _serialize_response(doc: Optional[dict]) -> Optional[dict]:
    serialized = _serialize(doc)
    if serialized is None:
        return None
    return _as_response_doc(serialized)


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_as_response_doc(_serialize(doc)) async for doc in cursor], total


async def get_by_sales_order_id(
    db: AsyncIOMotorDatabase,
    sales_order_id: str,
) -> Optional[dict]:
    return _serialize_response(
        await db[COLLECTION].find_one({"sales_order_id": sales_order_id})
    )


async def get_by_customer_id(db: AsyncIOMotorDatabase, customer_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"customer_id": customer_id})
    return [_as_response_doc(_serialize(doc)) async for doc in cursor]


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    cursor = db[COLLECTION].find({"status": status})
    return [_as_response_doc(_serialize(doc)) async for doc in cursor]


async def get_by_sku(db: AsyncIOMotorDatabase, sku: str) -> list[dict]:
    cursor = db[COLLECTION].find({"lines.sku": sku})
    return [_as_response_doc(_serialize(doc)) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: SalesOrderCreate) -> dict:
    now = utc_now()
    sales_order_id = f"sales-order-{uuid4().hex[:12]}"
    base = data.model_dump()
    if not base.get("sales_order_number"):
        base["sales_order_number"] = f"SO-{uuid4().hex[:8].upper()}"
    doc = _as_response_doc(
        {
            **base,
            "sales_order_id": sales_order_id,
            "updated_at": now,
            "id": sales_order_id,
        }
    )
    result = await db[COLLECTION].insert_one(
        {key: value for key, value in doc.items() if key != "id"}
    )
    doc["_id"] = result.inserted_id
    return _serialize_response(doc)


async def update(
    db: AsyncIOMotorDatabase,
    sales_order_id: str,
    data: SalesOrderUpdate,
) -> Optional[dict]:
    existing = await get_by_sales_order_id(db, sales_order_id)
    if not existing:
        return None
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return existing
    merged = {
        **existing,
        **fields,
        "sales_order_id": sales_order_id,
        "updated_at": utc_now(),
    }
    doc = _as_response_doc(merged)
    result = await db[COLLECTION].find_one_and_update(
        {"sales_order_id": sales_order_id},
        {"$set": {key: value for key, value in doc.items() if key != "id"}},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize_response(result)


async def delete(db: AsyncIOMotorDatabase, sales_order_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"sales_order_id": sales_order_id})
    return result.deleted_count == 1
