from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.auth.models.teams import TeamCreate, TeamUpdate

COLLECTION = "teams"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


async def get_all(
    db,
    page: int,
    page_size: int,
    department_id: str | None = None,
    search: str | None = None,
    sort_desc: bool = True,
):
    query = {}

    # ✅ Department filter
    if department_id:
        query["department_id"] = department_id

    # ✅ Search filter
    if search:
        query["$or"] = [
            {"team_id": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
            {"description": {"$regex": search, "$options": "i"}},
            {"lead_user_id": {"$regex": search, "$options": "i"}},
        ]

    sort_order = -1 if sort_desc else 1

    cursor = (
        db["teams"]
        .find(query)
        .sort("team_id", sort_order)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    items = [doc async for doc in cursor]
    total = await db["teams"].count_documents(query)

    return items, total


async def get_by_id(db: AsyncIOMotorDatabase, team_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"team_id": team_id})
    return _serialize(doc) if doc else None


async def get_by_department(db: AsyncIOMotorDatabase, department_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"department_id": department_id})
    return [_serialize(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: TeamCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    team_id: str,
    data: TeamUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, team_id)
    result = await db[COLLECTION].find_one_and_update(
        {"team_id": team_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, team_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"team_id": team_id})
    return result.deleted_count == 1
