from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.products.models.products import ProductCreate, ProductUpdate

COLLECTION = "products"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    total = await db[COLLECTION].count_documents({})
    cursor = db[COLLECTION].find({}).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, product_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"product_id": product_id})
    return _serialize(doc) if doc else None


async def get_by_sku(db: AsyncIOMotorDatabase, sku: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"sku": sku})
    return _serialize(doc) if doc else None


async def get_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    cursor = db[COLLECTION].find({"status": status})
    return [_serialize(d) async for d in cursor]


async def get_by_type(db: AsyncIOMotorDatabase, product_type: str) -> list[dict]:
    cursor = db[COLLECTION].find({"product_type": product_type})
    return [_serialize(d) async for d in cursor]


async def get_by_category(db: AsyncIOMotorDatabase, category: str) -> list[dict]:
    cursor = db[COLLECTION].find({"category": category})
    return [_serialize(d) async for d in cursor]


async def get_by_supplier(db: AsyncIOMotorDatabase, supplier_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"default_supplier_id": supplier_id})
    return [_serialize(d) async for d in cursor]


async def get_by_brand(db: AsyncIOMotorDatabase, brand: str) -> list[dict]:
    cursor = db[COLLECTION].find({"brand": brand})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: ProductCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    product_id: str,
    data: ProductUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, product_id)
    result = await db[COLLECTION].find_one_and_update(
        {"product_id": product_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, product_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"product_id": product_id})
    return result.deleted_count == 1
