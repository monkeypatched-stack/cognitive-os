from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.auth.models.roles import RoleCreate, RoleUpdate

COLLECTION = "roles"


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


async def get_by_id(db: AsyncIOMotorDatabase, role_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"role_id": role_id})
    return _serialize(doc) if doc else None


async def get_by_name(db: AsyncIOMotorDatabase, role_name: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"name": role_name})
    return _serialize(doc) if doc else None


async def get_by_permission(db: AsyncIOMotorDatabase, permission_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"permissions": permission_id})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: RoleCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    role_id: str,
    data: RoleUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, role_id)
    result = await db[COLLECTION].find_one_and_update(
        {"role_id": role_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, role_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"role_id": role_id})
    return result.deleted_count == 1


async def add_permission(
    db: AsyncIOMotorDatabase,
    role_id: str,
    permission_id: str,
) -> Optional[dict]:
    result = await db[COLLECTION].find_one_and_update(
        {"role_id": role_id},
        {"$addToSet": {"permissions": permission_id}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def remove_permission(
    db: AsyncIOMotorDatabase,
    role_id: str,
    permission_id: str,
) -> Optional[dict]:
    result = await db[COLLECTION].find_one_and_update(
        {"role_id": role_id},
        {"$pull": {"permissions": permission_id}},
        return_document=True,
    )
    return _serialize(result) if result else None
