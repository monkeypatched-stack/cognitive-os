from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.shipping.models.shipping_provider_metadata import (
    ShippingProviderMetadataCreate,
    ShippingProviderMetadataUpdate,
)

COLLECTION = "shipping_provider_metadata"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


async def get_all(
    db: AsyncIOMotorDatabase, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_provider_id(
    db: AsyncIOMotorDatabase, provider_id: str
) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"provider_id": provider_id}))


async def get_by_category(db: AsyncIOMotorDatabase, category: str) -> list[dict]:
    cursor = db[COLLECTION].find({"category": category})
    return [_serialize(d) async for d in cursor]


async def create(
    db: AsyncIOMotorDatabase, data: ShippingProviderMetadataCreate
) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    provider_id: str,
    data: ShippingProviderMetadataUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_provider_id(db, provider_id)
    result = await db[COLLECTION].find_one_and_update(
        {"provider_id": provider_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, provider_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"provider_id": provider_id})
    return result.deleted_count == 1
