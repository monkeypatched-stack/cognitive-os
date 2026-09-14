from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.assets.models.parts import (
    MachinePartCreate,
    MachinePartResponse,
    MachinePartUpdate,
)

COLLECTION = "machine_parts"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)  # shallow copy — never mutate Motor's result
    doc.pop("_id", None)
    return doc


def _normalize_category(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "mechanical": "Mechanical",
            "electrical": "Electrical",
            "hydraulic": "Hydraulic",
            "pneumatic": "Pneumatic",
            "electronic": "Electronic",
            "electronics": "Electronic",
            "consumable": "Consumable",
            "consumables": "Consumable",
        }
        if normalized in aliases:
            return aliases[normalized]
    return "Mechanical"


def _normalize_status(value: object, is_low_stock: object = None) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("_", " ").replace("-", " ")
        aliases = {
            "in stock": "In Stock",
            "available": "In Stock",
            "low stock": "Low Stock",
            "out of stock": "Out of Stock",
            "on order": "On Order",
            "ordered": "On Order",
            "discontinued": "Discontinued",
        }
        if normalized in aliases:
            return aliases[normalized]
    return "Low Stock" if is_low_stock else "In Stock"


def _normalize_part_record(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None

    record = _serialize(doc)
    part_id = str(
        record.get("part_id") or record.get("id") or record.get("part_number") or "PART"
    )
    record["part_id"] = part_id
    record.setdefault("id", part_id)
    record.setdefault("part_number", part_id)
    record.setdefault(
        "part_name",
        record.get("name") or record.get("description") or record["part_number"],
    )
    record["category"] = _normalize_category(record.get("category"))
    record["status"] = _normalize_status(
        record.get("status"), record.get("is_low_stock")
    )
    if record.get("gstin") is None and record.get("gtin") is not None:
        record["gstin"] = str(record["gtin"])

    return MachinePartResponse.model_validate(record).model_dump()


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_normalize_part_record(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, part_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": part_id})
    return _normalize_part_record(doc) if doc else None


async def get_by_machine(db: AsyncIOMotorDatabase, machine_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"machine_id": machine_id})
    return [_normalize_part_record(d) async for d in cursor]


async def get_by_equipment(db: AsyncIOMotorDatabase, equipment_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"equipment_id": equipment_id})
    return [_normalize_part_record(d) async for d in cursor]


async def get_low_stock(
    db: AsyncIOMotorDatabase, machine: Optional[str] = None
) -> list[dict]:
    query: dict = {
        "$or": [
            {"is_low_stock": True},
            {"status": {"$in": ["Low Stock", "low_stock", "low stock"]}},
        ]
    }
    if machine:
        query = {"$and": [query, {"machine_id": machine}]}
    cursor = db[COLLECTION].find(query)
    return [_normalize_part_record(d) async for d in cursor]


async def create(db: AsyncIOMotorDatabase, data: MachinePartCreate) -> dict:
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _normalize_part_record(doc)


async def update(
    db: AsyncIOMotorDatabase, part_id: str, data: MachinePartUpdate
) -> Optional[dict]:
    # exclude_unset so we never overwrite existing fields with None
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, part_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": part_id},
        {"$set": fields},
        return_document=True,
    )
    return _normalize_part_record(result) if result else None


async def delete(db: AsyncIOMotorDatabase, part_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"id": part_id})
    return result.deleted_count == 1
