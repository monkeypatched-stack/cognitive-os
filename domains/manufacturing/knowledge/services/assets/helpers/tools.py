from typing import Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.utils import _prepare, _serialize, _utc_now
from services.assets.models.tools import ToolCreate, ToolRecord, ToolUpdate

TOOLS_COLLECTION = "tools"


def _compute_tool(doc: dict) -> dict:
    record = ToolRecord(**doc)
    return _prepare(record.model_dump())


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    category: Optional[str] = None,
    status: Optional[str] = None,
    assignment_scope: Optional[str] = None,
    assigned_to_id: Optional[str] = None,
    q: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if category:
        query["category"] = category
    if status:
        query["status"] = status
    if assignment_scope:
        query["assignment_scope"] = assignment_scope
    if assigned_to_id:
        query["assigned_to_id"] = assigned_to_id
    if q:
        query["$or"] = [
            {"tool_code": {"$regex": q, "$options": "i"}},
            {"name": {"$regex": q, "$options": "i"}},
            {"manufacturer": {"$regex": q, "$options": "i"}},
            {"model": {"$regex": q, "$options": "i"}},
            {"serial_number": {"$regex": q, "$options": "i"}},
        ]

    total = await db[TOOLS_COLLECTION].count_documents(query)
    cursor = (
        db[TOOLS_COLLECTION]
        .find(query)
        .sort("tool_code", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, tool_id: str) -> Optional[dict]:
    return _serialize(await db[TOOLS_COLLECTION].find_one({"tool_id": tool_id}))


async def get_by_code(db: AsyncIOMotorDatabase, tool_code: str) -> Optional[dict]:
    return _serialize(await db[TOOLS_COLLECTION].find_one({"tool_code": tool_code}))


async def create(db: AsyncIOMotorDatabase, data: ToolCreate) -> dict:
    now = _utc_now()
    payload = data.model_dump()
    tool_id = payload.pop("tool_id", None) or f"tool-{uuid4().hex[:12]}"
    doc = _compute_tool(
        {
            **payload,
            "tool_id": tool_id,
            "checked_out_quantity": 0,
            "created_at": now,
            "updated_at": now,
        }
    )

    await db[TOOLS_COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    tool_id: str,
    data: ToolUpdate,
) -> Optional[dict]:
    existing = await get_by_id(db, tool_id)
    if not existing:
        return None

    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return existing

    fields["updated_at"] = _utc_now()
    computed = _compute_tool({**existing, **fields})
    result = await db[TOOLS_COLLECTION].find_one_and_update(
        {"tool_id": tool_id},
        {"$set": computed},
        return_document=True,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, tool_id: str) -> bool:
    result = await db[TOOLS_COLLECTION].delete_one({"tool_id": tool_id})
    return result.deleted_count == 1
