from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.auth.models.departments import DepartmentCreate, DepartmentUpdate

COLLECTION = "departments"


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


async def get_by_id(db: AsyncIOMotorDatabase, department_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"department_id": department_id})
    return _serialize(doc) if doc else None


async def create(db: AsyncIOMotorDatabase, data: DepartmentCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    department_id: str,
    data: DepartmentUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, department_id)
    result = await db[COLLECTION].find_one_and_update(
        {"department_id": department_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, department_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"department_id": department_id})
    return result.deleted_count == 1
