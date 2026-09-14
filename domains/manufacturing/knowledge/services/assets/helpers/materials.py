from datetime import date, datetime
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.assets.models.material import MaterialUsedCreate, MaterialUsedUpdate

COLLECTION = "materials_used"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _prepare(doc: dict) -> dict:
    """Convert date → datetime so BSON can encode them."""
    return {
        k: (
            datetime(v.year, v.month, v.day)
            if isinstance(v, date) and not isinstance(v, datetime)
            else v
        )
        for k, v in doc.items()
    }


# ── Read ──────────────────────────────────────────────────────────────────────


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    results = [_serialize(d) async for d in cursor]
    return results, total


async def get_by_id(db: AsyncIOMotorDatabase, material_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"material_id": material_id})
    return _serialize(doc) if doc else None


async def get_by_name(db: AsyncIOMotorDatabase, name: str) -> list[dict]:
    cursor = db[COLLECTION].find({"name": name})
    return [_serialize(d) async for d in cursor]


# ── Create ────────────────────────────────────────────────────────────────────


async def create(db: AsyncIOMotorDatabase, data: MaterialUsedCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


# ── Update ────────────────────────────────────────────────────────────────────


async def update(
    db: AsyncIOMotorDatabase,
    material_id: str,
    data: MaterialUsedUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_by_id(db, material_id)
    result = await db[COLLECTION].find_one_and_update(
        {"material_id": material_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


# ── Delete ────────────────────────────────────────────────────────────────────


async def delete(db: AsyncIOMotorDatabase, material_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"material_id": material_id})
    return result.deleted_count == 1
