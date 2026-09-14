from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.auth.models.teamMembers import TeamMemberCreate, TeamMemberUpdate

COLLECTION = "team_members"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


# ── Get all members (with filters) ────────────────────────────────────────────
async def get_all(
    db: AsyncIOMotorDatabase,
    page: int,
    page_size: int,
    team_id: Optional[str] = None,
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    sort_desc: bool = False,
):
    query = {}

    # ✅ Filter by team
    if team_id:
        query["team_id"] = team_id

    # ✅ Filter by status
    if is_active is not None:
        query["is_active"] = is_active

    # ✅ Search
    if search:
        query["$or"] = [
            {"user_id": {"$regex": search, "$options": "i"}},
            {"employee_id": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
            {"department": {"$regex": search, "$options": "i"}},
            {"role": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
        ]

    sort_order = -1 if sort_desc else 1

    cursor = (
        db[COLLECTION]
        .find(query)
        .sort("name", sort_order)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    items = [_serialize(doc) async for doc in cursor]
    total = await db[COLLECTION].count_documents(query)

    return items, total


# ── Get by team ───────────────────────────────────────────────────────────────
async def get_by_team(
    db: AsyncIOMotorDatabase,
    team_id: str,
) -> list[dict]:
    cursor = db[COLLECTION].find({"team_id": team_id})
    return [_serialize(d) async for d in cursor]


# ── Get one member in a team ──────────────────────────────────────────────────
async def get_member(
    db: AsyncIOMotorDatabase,
    team_id: str,
    user_id: str,
) -> Optional[dict]:
    doc = await db[COLLECTION].find_one(
        {
            "team_id": team_id,
            "user_id": user_id,
        }
    )
    return _serialize(doc) if doc else None


# ── Add member ────────────────────────────────────────────────────────────────
async def create(
    db: AsyncIOMotorDatabase,
    team_id: str,
    data: TeamMemberCreate,
) -> dict:
    doc = data.model_dump()
    doc["team_id"] = team_id  # attach team

    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


# ── Bulk add members (for your dialog) ────────────────────────────────────────
async def bulk_create(
    db: AsyncIOMotorDatabase,
    team_id: str,
    members: list[TeamMemberCreate],
) -> list[dict]:
    docs = []
    for m in members:
        doc = m.model_dump()
        doc["team_id"] = team_id
        docs.append(doc)

    if docs:
        await db[COLLECTION].insert_many(docs)

    return [_serialize(d) for d in docs]


# ── Update member ─────────────────────────────────────────────────────────────
async def update(
    db: AsyncIOMotorDatabase,
    team_id: str,
    user_id: str,
    data: TeamMemberUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)

    if not fields:
        return await get_member(db, team_id, user_id)

    result = await db[COLLECTION].find_one_and_update(
        {"team_id": team_id, "user_id": user_id},
        {"$set": fields},
        return_document=True,
    )

    return _serialize(result) if result else None


# ── Remove member ─────────────────────────────────────────────────────────────
async def delete(
    db: AsyncIOMotorDatabase,
    team_id: str,
    user_id: str,
) -> bool:
    result = await db[COLLECTION].delete_one(
        {
            "team_id": team_id,
            "user_id": user_id,
        }
    )
    return result.deleted_count == 1


# ── Remove all members of a team (optional but useful) ─────────────────────────
async def delete_by_team(
    db: AsyncIOMotorDatabase,
    team_id: str,
) -> int:
    result = await db[COLLECTION].delete_many({"team_id": team_id})
    return result.deleted_count
