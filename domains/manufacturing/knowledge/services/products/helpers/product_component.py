from typing import Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.products.models.product_component import (
    ProductComponentCreate,
    ProductComponentRecord,
    ProductComponentUpdate,
)
from services.products.helpers.product_utils import _utc_now, _serialize, _prepare

COMPONENTS_COLLECTION = "product_components"

_CATEGORY_ALIASES = {
    "api": "raw_material",
    "active pharmaceutical ingredient": "raw_material",
    "excipient": "raw_material",
    "excipients": "raw_material",
    "raw material": "raw_material",
    "raw_material": "raw_material",
    "packaging material": "packaging",
    "packaging": "packaging",
    "component": "component",
    "subassembly": "subassembly",
    "consumable": "consumable",
    "fastener": "fastener",
    "electronic": "electronic",
    "other": "other",
}


def _normalized_category(value: object) -> str:
    key = str(value or "").strip().lower()
    return _CATEGORY_ALIASES.get(key, "other")


def _normalize_component_record(doc: Optional[dict]) -> Optional[dict]:
    record = _serialize(doc)
    if not record:
        return None
    part_number = (
        str(record.get("part_number") or record.get("component_id") or "UNKNOWN-PART")
        .strip()
        .upper()
    )
    record["part_number"] = part_number
    record.setdefault(
        "part_name", record.get("name") or record.get("description") or part_number
    )
    record["category"] = _normalized_category(
        record.get("category") or record.get("component_type")
    )
    record.setdefault("quantity", {"base_unit_of_measure": "piece"})
    record.setdefault(
        "cost", {"unit_cost": 1, "currency": "USD", "minimum_order_quantity": 1}
    )
    record.setdefault(
        "supplier_name",
        record.get("supplier") or record.get("supplier_id") or "Unassigned",
    )
    record.setdefault("material_spec", {})
    return ProductComponentRecord(**record).model_dump()


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    total = await db[COMPONENTS_COLLECTION].count_documents({})
    cursor = (
        db[COMPONENTS_COLLECTION].find({}).skip((page - 1) * page_size).limit(page_size)
    )
    return [_normalize_component_record(d) async for d in cursor], total


async def get_components(db: AsyncIOMotorDatabase, product_id: str) -> list[dict]:
    cursor = db[COMPONENTS_COLLECTION].find({"product_id": product_id})
    return [_normalize_component_record(d) async for d in cursor]


async def get_by_product(db: AsyncIOMotorDatabase, product_id: str) -> list[dict]:
    return await get_components(db, product_id)


async def get_component(db: AsyncIOMotorDatabase, component_id: str) -> Optional[dict]:
    return _normalize_component_record(
        await db[COMPONENTS_COLLECTION].find_one({"component_id": component_id})
    )


async def get_by_id(db: AsyncIOMotorDatabase, component_id: str) -> Optional[dict]:
    return await get_component(db, component_id)


async def get_by_type(db: AsyncIOMotorDatabase, component_type: str) -> list[dict]:
    cursor = db[COMPONENTS_COLLECTION].find({"category": component_type})
    return [_normalize_component_record(d) async for d in cursor]


async def get_by_sku(db: AsyncIOMotorDatabase, sku: str) -> list[dict]:
    cursor = db[COMPONENTS_COLLECTION].find({"part_number": sku.upper()})
    return [_normalize_component_record(d) async for d in cursor]


async def get_substitutable(db: AsyncIOMotorDatabase) -> list[dict]:
    cursor = db[COMPONENTS_COLLECTION].find(
        {
            "$or": [
                {"is_configurable": True},
                {"substitute_part_numbers.0": {"$exists": True}},
            ]
        }
    )
    return [_normalize_component_record(d) async for d in cursor]


async def create_component(
    db: AsyncIOMotorDatabase,
    product_id: str,
    data: ProductComponentCreate,
) -> dict:
    now = _utc_now()
    doc = _prepare(
        {
            **data.model_dump(),
            "product_id": product_id,
            "component_id": f"component-{uuid4().hex[:12]}",
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COMPONENTS_COLLECTION].insert_one(doc)
    return _normalize_component_record(doc)


async def create(db: AsyncIOMotorDatabase, data: ProductComponentCreate) -> dict:
    now = _utc_now()
    doc = data.model_dump()
    if not doc.get("component_id"):
        doc["component_id"] = f"component-{uuid4().hex[:12]}"
    doc["created_at"] = now
    doc["updated_at"] = now
    doc = _prepare(doc)
    await db[COMPONENTS_COLLECTION].insert_one(doc)
    return _normalize_component_record(doc)


async def update_component(
    db: AsyncIOMotorDatabase,
    component_id: str,
    data: ProductComponentUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    fields["updated_at"] = _utc_now()
    result = await db[COMPONENTS_COLLECTION].find_one_and_update(
        {"component_id": component_id},
        {"$set": fields},
        return_document=True,
    )
    return _normalize_component_record(result)


async def update(
    db: AsyncIOMotorDatabase,
    component_id: str,
    data: ProductComponentUpdate,
) -> Optional[dict]:
    return await update_component(db, component_id, data)


async def delete_component(db: AsyncIOMotorDatabase, component_id: str) -> bool:
    result = await db[COMPONENTS_COLLECTION].delete_one({"component_id": component_id})
    return result.deleted_count == 1


async def delete(db: AsyncIOMotorDatabase, component_id: str) -> bool:
    return await delete_component(db, component_id)


async def delete_by_product(db: AsyncIOMotorDatabase, product_id: str) -> int:
    result = await db[COMPONENTS_COLLECTION].delete_many({"product_id": product_id})
    return result.deleted_count
