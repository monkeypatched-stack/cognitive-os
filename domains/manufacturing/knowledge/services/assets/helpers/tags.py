from typing import Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.assets.models.tags import RFIDTagCreate, RFIDTagRecord, RFIDTagUpdate
from services.common.utils import _prepare, _serialize, _utc_now

RFID_TAGS_COLLECTION = "rfid_tags"


def _compute_rfid_tag(doc: dict) -> dict:
    record = RFIDTagRecord(**doc)
    return _prepare(record.model_dump())


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    assigned_type: Optional[str] = None,
    assigned_to: Optional[str] = None,
    q: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if status:
        query["status"] = status
    if assigned_type:
        query["assigned_type"] = assigned_type
    if assigned_to:
        query["assigned_to"] = assigned_to
    if q:
        query["$or"] = [
            {"tag_code": {"$regex": q, "$options": "i"}},
            {"name": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
            {"tag_type": {"$regex": q, "$options": "i"}},
            {"barcode": {"$regex": q, "$options": "i"}},
            {"manufacturer": {"$regex": q, "$options": "i"}},
            {"serial_number": {"$regex": q, "$options": "i"}},
        ]

    total = await db[RFID_TAGS_COLLECTION].count_documents(query)
    cursor = (
        db[RFID_TAGS_COLLECTION]
        .find(query)
        .sort("tag_code", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, rfid_tag_id: str) -> Optional[dict]:
    return _serialize(
        await db[RFID_TAGS_COLLECTION].find_one({"rfid_tag_id": rfid_tag_id})
    )


async def get_by_code(db: AsyncIOMotorDatabase, tag_code: str) -> Optional[dict]:
    return _serialize(await db[RFID_TAGS_COLLECTION].find_one({"tag_code": tag_code}))


async def get_by_barcode(db: AsyncIOMotorDatabase, barcode: str) -> Optional[dict]:
    return _serialize(await db[RFID_TAGS_COLLECTION].find_one({"barcode": barcode}))


async def create(db: AsyncIOMotorDatabase, data: RFIDTagCreate) -> dict:
    now = _utc_now()
    doc = _compute_rfid_tag(
        {
            **data.model_dump(),
            "rfid_tag_id": f"rfid-tag-{uuid4().hex[:12]}",
            "created_at": now,
            "updated_at": now,
        }
    )

    await db[RFID_TAGS_COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    rfid_tag_id: str,
    data: RFIDTagUpdate,
) -> Optional[dict]:
    existing = await get_by_id(db, rfid_tag_id)
    if not existing:
        return None

    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return existing

    fields["updated_at"] = fields.get("updated_at") or _utc_now()
    computed = _compute_rfid_tag({**existing, **fields})
    result = await db[RFID_TAGS_COLLECTION].find_one_and_update(
        {"rfid_tag_id": rfid_tag_id},
        {"$set": computed},
        return_document=True,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, rfid_tag_id: str) -> bool:
    result = await db[RFID_TAGS_COLLECTION].delete_one({"rfid_tag_id": rfid_tag_id})
    return result.deleted_count == 1
