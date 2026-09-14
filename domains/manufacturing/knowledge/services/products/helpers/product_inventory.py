from typing import Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase


from services.products.helpers.product_utils import _prepare, _serialize, _utc_now
from services.inventory.models.inventory_responses import (
    InventorySummary,
    InventorySummaryLocation,
)
from services.products.models.product_inventory import (
    ProductInventoryCreate,
    ProductInventoryRecord,
    ProductInventoryUpdate,
)

INVENTORY_COLLECTION = "product_inventory"


def _compute_inventory(doc: dict) -> dict:
    record = ProductInventoryRecord(**doc)
    return _prepare(record.model_dump())


# ─────────────────────────────
# Inventory CRUD
# ─────────────────────────────


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    product_id: Optional[str] = None,
    location_id: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[list[dict], int]:
    query: dict = {}
    if product_id:
        query["product_id"] = product_id
    if location_id:
        query["location_id"] = location_id
    if status:
        query["status"] = status

    total = await db[INVENTORY_COLLECTION].count_documents(query)
    cursor = (
        db[INVENTORY_COLLECTION]
        .find(query)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(d) async for d in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase,
    inventory_id: str,
) -> Optional[dict]:
    return _serialize(
        await db[INVENTORY_COLLECTION].find_one({"inventory_id": inventory_id})
    )


async def get_by_product(
    db: AsyncIOMotorDatabase,
    product_id: str,
) -> list[dict]:
    cursor = db[INVENTORY_COLLECTION].find({"product_id": product_id})
    return [_serialize(d) async for d in cursor]


async def get_by_location(db: AsyncIOMotorDatabase, location_id: str) -> list[dict]:
    cursor = db[INVENTORY_COLLECTION].find({"location_id": location_id})
    return [_serialize(d) async for d in cursor]


async def get_by_status(db: AsyncIOMotorDatabase, status_value: str) -> list[dict]:
    cursor = db[INVENTORY_COLLECTION].find({"status": status_value})
    return [_serialize(d) async for d in cursor]


async def get_below_reorder(db: AsyncIOMotorDatabase) -> list[dict]:
    records, _ = await get_all(db, page=1, page_size=10000)
    return [
        record
        for record in records
        if ProductInventoryRecord(**record).is_below_reorder
    ]


async def get_overstocked(db: AsyncIOMotorDatabase) -> list[dict]:
    records, _ = await get_all(db, page=1, page_size=10000)
    return [
        record for record in records if ProductInventoryRecord(**record).is_overstocked
    ]


async def get_expired(db: AsyncIOMotorDatabase) -> list[dict]:
    cursor = db[INVENTORY_COLLECTION].find({"status": "Expired"})
    return [_serialize(d) async for d in cursor]


async def get_by_product_location(
    db: AsyncIOMotorDatabase,
    product_id: str,
    location_id: str,
) -> Optional[dict]:
    return _serialize(
        await db[INVENTORY_COLLECTION].find_one(
            {"product_id": product_id, "location_id": location_id}
        )
    )


async def get_by_product_and_location(
    db: AsyncIOMotorDatabase,
    product_id: str,
    location_id: str,
) -> Optional[dict]:
    return await get_by_product_location(db, product_id, location_id)


async def get_inventory_summary(
    db: AsyncIOMotorDatabase,
    product_id: str,
) -> Optional[dict]:
    records = [
        ProductInventoryRecord(**doc) for doc in await get_by_product(db, product_id)
    ]
    if not records:
        return None

    first = records[0]
    locations: list[InventorySummaryLocation] = []
    estimated_reorder_value = 0.0
    has_reorder_value = False

    for record in records:
        location_name = record.location_id
        if isinstance(record.metadata, dict):
            location_name = record.metadata.get("location_name") or location_name

        locations.append(
            InventorySummaryLocation(
                location_id=record.location_id,
                location_name=location_name,
                on_hand=record.quantity_on_hand,
                available=record.quantity_available,
                status=record.status,
            )
        )

        if record.is_below_reorder and record.unit_cost is not None:
            reorder_quantity = record.reorder_quantity
            if reorder_quantity is None and record.reorder_point is not None:
                reorder_quantity = max(
                    record.reorder_point - record.quantity_available, 0
                )
            if reorder_quantity:
                estimated_reorder_value += reorder_quantity * record.unit_cost
                has_reorder_value = True

    summary = InventorySummary(
        product_id=product_id,
        sku=first.sku,
        product_name=first.product_name,
        total_on_hand=sum(record.quantity_on_hand for record in records),
        total_reserved=sum(record.quantity_reserved for record in records),
        total_available=sum(record.quantity_available for record in records),
        total_incoming=sum(record.quantity_incoming for record in records),
        locations_count=len(records),
        locations=locations,
        is_below_reorder=any(record.is_below_reorder for record in records),
        estimated_reorder_value=(
            round(estimated_reorder_value, 2) if has_reorder_value else None
        ),
        currency=first.currency,
    )
    return summary.model_dump()


async def create(
    db: AsyncIOMotorDatabase,
    data: ProductInventoryCreate,
    product_sku: Optional[str] = None,
    product_name: Optional[str] = None,
) -> Optional[dict]:

    now = _utc_now()
    doc = _compute_inventory(
        {
            **data.model_dump(),
            "inventory_id": f"inventory-{uuid4().hex[:12]}",
            "sku": product_sku or data.product_id,
            "product_name": product_name or data.product_id,
            "created_at": now,
            "updated_at": now,
        }
    )

    await db[INVENTORY_COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    inventory_id: str,
    data: ProductInventoryUpdate,
) -> Optional[dict]:
    existing = await get_by_id(db, inventory_id)
    if not existing:
        return None

    fields = _prepare(data.model_dump(exclude_unset=True))
    fields["updated_at"] = _utc_now()

    merged = {**existing, **fields}
    computed = _compute_inventory(merged)

    result = await db[INVENTORY_COLLECTION].find_one_and_update(
        {"inventory_id": inventory_id},
        {"$set": computed},
        return_document=True,
    )
    return _serialize(result)


async def delete(
    db: AsyncIOMotorDatabase,
    inventory_id: str,
) -> bool:
    result = await db[INVENTORY_COLLECTION].delete_one({"inventory_id": inventory_id})
    return result.deleted_count == 1
