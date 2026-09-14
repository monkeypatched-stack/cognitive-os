from datetime import date, datetime, timezone
from typing import Optional
from uuid import uuid4

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.products.models.product_pricing import (
    ProductPricingCreate,
    ProductPricingRecord,
    ProductPricingUpdate,
)
from services.products.models.product_common import utc_now

COLLECTION = "product_pricing"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    object_id = doc.pop("_id", None)
    doc.pop("customer_id", None)
    doc.pop("customer_group", None)

    if not doc.get("pricing_id"):
        doc["pricing_id"] = f"product-pricing-{object_id or uuid4().hex[:12]}"
    if "price_per_unit" not in doc and "amount" in doc:
        doc["price_per_unit"] = doc.get("amount")

    return _prepare(doc)


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


def _as_record_doc(data: dict) -> dict:
    return _prepare(ProductPricingRecord(**data).model_dump())


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    total = await db[COLLECTION].count_documents({})
    cursor = db[COLLECTION].find({}).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, pricing_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"pricing_id": pricing_id})
    return _serialize(doc) if doc else None


async def get_by_product(db: AsyncIOMotorDatabase, product_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"product_id": product_id})
    return [_serialize(d) async for d in cursor]


async def get_active_by_product(
    db: AsyncIOMotorDatabase, product_id: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"product_id": product_id, "is_active": True})
    return [_serialize(d) async for d in cursor]


async def get_by_pricing_type(
    db: AsyncIOMotorDatabase, pricing_type: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"pricing_type": pricing_type})
    return [_serialize(d) async for d in cursor]


async def get_by_currency(db: AsyncIOMotorDatabase, currency: str) -> list[dict]:
    cursor = db[COLLECTION].find({"currency": currency})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: ProductPricingCreate) -> dict:
    now = utc_now()
    doc = _as_record_doc(
        {
            **data.model_dump(),
            "pricing_id": f"product-pricing-{uuid4().hex[:12]}",
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    pricing_id: str,
    data: ProductPricingUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, pricing_id)
    result = await db[COLLECTION].find_one_and_update(
        {"pricing_id": pricing_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, pricing_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"pricing_id": pricing_id})
    return result.deleted_count == 1


async def delete_by_product(db: AsyncIOMotorDatabase, product_id: str) -> int:
    result = await db[COLLECTION].delete_many({"product_id": product_id})
    return result.deleted_count
