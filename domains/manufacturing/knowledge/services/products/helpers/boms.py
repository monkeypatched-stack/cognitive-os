from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.products.helpers.product_utils import _prepare, _serialize, _utc_now
from services.products.models.product_component import BOMCreate, BOMRecord, BOMUpdate

BOMS_COLLECTION = "boms"


def _as_record_doc(data: dict) -> dict:
    return _prepare(BOMRecord(**data).model_dump())


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    total = await db[BOMS_COLLECTION].count_documents({})
    cursor = db[BOMS_COLLECTION].find({}).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, bom_id: str) -> Optional[dict]:
    return _serialize(await db[BOMS_COLLECTION].find_one({"bom_id": bom_id}))


async def get_by_code(db: AsyncIOMotorDatabase, bom_code: str) -> list[dict]:
    cursor = db[BOMS_COLLECTION].find({"bom_code": bom_code})
    return [_serialize(doc) async for doc in cursor]


async def get_by_parent_product(
    db: AsyncIOMotorDatabase,
    parent_product_id: str,
) -> list[dict]:
    cursor = db[BOMS_COLLECTION].find({"parent_product_id": parent_product_id})
    return [_serialize(doc) async for doc in cursor]


async def get_current_by_parent_product(
    db: AsyncIOMotorDatabase,
    parent_product_id: str,
) -> Optional[dict]:
    return _serialize(
        await db[BOMS_COLLECTION].find_one(
            {
                "parent_product_id": parent_product_id,
                "is_current_version": True,
            }
        )
    )


async def create(db: AsyncIOMotorDatabase, data: BOMCreate) -> dict:
    now = _utc_now()
    doc = data.model_dump()
    doc["created_at"] = now
    doc["updated_at"] = now
    record = _as_record_doc(doc)
    await db[BOMS_COLLECTION].insert_one(record)
    return _serialize(record)


async def update(
    db: AsyncIOMotorDatabase,
    bom_id: str,
    data: BOMUpdate,
) -> Optional[dict]:
    existing = await get_by_id(db, bom_id)
    if not existing:
        return None

    fields = data.model_dump(exclude_unset=True)
    merged = {
        **existing,
        **fields,
        "bom_id": bom_id,
        "updated_at": _utc_now(),
    }
    record = _as_record_doc(merged)
    return _serialize(
        await db[BOMS_COLLECTION].find_one_and_update(
            {"bom_id": bom_id},
            {"$set": record},
            return_document=ReturnDocument.AFTER,
        )
    )


async def delete(db: AsyncIOMotorDatabase, bom_id: str) -> bool:
    result = await db[BOMS_COLLECTION].delete_one({"bom_id": bom_id})
    return result.deleted_count == 1
