from datetime import date, datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from pymongo import ReturnDocument

from services.inventory.models.warehouse_location_models import (
    Aisle,
    AisleCreate,
    AisleTree,
    AisleUpdate,
    Area,
    AreaCreate,
    AreaTree,
    AreaUpdate,
    Bin,
    BinCreate,
    BinUpdate,
    LocationTree,
    Lot,
    LotCreate,
    LotUpdate,
    Rack,
    RackCreate,
    RackTree,
    RackUpdate,
    WarehouseLocation,
    Zone,
    ZoneCreate,
    ZoneTree,
    ZoneUpdate,
)

LEVELS = {
    "lots": ("warehouse_lots", "lot_id", Lot, LotCreate, LotUpdate),
    "zones": ("warehouse_zones", "zone_id", Zone, ZoneCreate, ZoneUpdate),
    "areas": ("warehouse_areas", "area_id", Area, AreaCreate, AreaUpdate),
    "aisles": ("warehouse_aisles", "aisle_id", Aisle, AisleCreate, AisleUpdate),
    "racks": ("warehouse_racks", "rack_id", Rack, RackCreate, RackUpdate),
    "bins": ("warehouse_bins", "bin_id", Bin, BinCreate, BinUpdate),
}


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _prepare(value):
    if isinstance(value, BaseModel):
        return _prepare(value.model_dump())
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, dict):
        return {key: _prepare(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare(item) for item in value]
    return value


def _level_config(level: str):
    try:
        return LEVELS[level]
    except KeyError as exc:
        raise ValueError(f"Unknown warehouse location level '{level}'") from exc


async def get_all(
    db: AsyncIOMotorDatabase,
    level: str,
    *,
    page: int = 1,
    page_size: int = 20,
    query: Optional[dict] = None,
) -> tuple[list[dict], int]:
    collection, _, _, _, _ = _level_config(level)
    query = query or {}
    total = await db[collection].count_documents(query)
    cursor = db[collection].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase, level: str, record_id: str
) -> Optional[dict]:
    collection, id_field, _, _, _ = _level_config(level)
    return _serialize(await db[collection].find_one({id_field: record_id}))


async def create(db: AsyncIOMotorDatabase, level: str, data: BaseModel) -> dict:
    collection, _, model_cls, _, _ = _level_config(level)
    doc = _prepare(model_cls(**data.model_dump()))
    await db[collection].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    level: str,
    record_id: str,
    data: BaseModel,
) -> Optional[dict]:
    collection, id_field, model_cls, _, _ = _level_config(level)
    existing = await get_by_id(db, level, record_id)
    if not existing:
        return None

    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return existing

    merged = {**existing, **fields, "updated_at": datetime.now(timezone.utc)}
    model_cls(**merged)
    result = await db[collection].find_one_and_update(
        {id_field: record_id},
        {"$set": fields | {"updated_at": merged["updated_at"]}},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, level: str, record_id: str) -> bool:
    collection, id_field, _, _, _ = _level_config(level)
    result = await db[collection].delete_one({id_field: record_id})
    return result.deleted_count == 1


async def get_location_for_bin(
    db: AsyncIOMotorDatabase,
    bin_id: str,
) -> Optional[WarehouseLocation]:
    bin_doc = await get_by_id(db, "bins", bin_id)
    if not bin_doc:
        return None

    rack_doc = await get_by_id(db, "racks", bin_doc["rack_id"])
    if not rack_doc:
        return None
    aisle_doc = await get_by_id(db, "aisles", rack_doc["aisle_id"])
    if not aisle_doc:
        return None
    area_doc = await get_by_id(db, "areas", aisle_doc["area_id"])
    if not area_doc:
        return None
    zone_doc = await get_by_id(db, "zones", area_doc["zone_id"])
    if not zone_doc:
        return None
    lot_doc = await get_by_id(db, "lots", zone_doc["lot_id"])
    if not lot_doc:
        return None

    return WarehouseLocation(
        bin=Bin(**bin_doc),
        rack=Rack(**rack_doc),
        aisle=Aisle(**aisle_doc),
        area=Area(**area_doc),
        zone=Zone(**zone_doc),
        lot=Lot(**lot_doc),
    )


async def get_tree_for_lot(
    db: AsyncIOMotorDatabase, lot_id: str
) -> Optional[LocationTree]:
    lot_doc = await get_by_id(db, "lots", lot_id)
    if not lot_doc:
        return None

    zone_docs, _ = await get_all(
        db, "zones", page=1, page_size=10_000, query={"lot_id": lot_id}
    )
    area_docs, _ = await get_all(db, "areas", page=1, page_size=10_000)
    aisle_docs, _ = await get_all(db, "aisles", page=1, page_size=10_000)
    rack_docs, _ = await get_all(db, "racks", page=1, page_size=10_000)
    bin_docs, _ = await get_all(db, "bins", page=1, page_size=10_000)

    areas_by_zone: dict[str, list[dict]] = {}
    for doc in area_docs:
        areas_by_zone.setdefault(doc["zone_id"], []).append(doc)

    aisles_by_area: dict[str, list[dict]] = {}
    for doc in aisle_docs:
        aisles_by_area.setdefault(doc["area_id"], []).append(doc)

    racks_by_aisle: dict[str, list[dict]] = {}
    for doc in rack_docs:
        racks_by_aisle.setdefault(doc["aisle_id"], []).append(doc)

    bins_by_rack: dict[str, list[dict]] = {}
    for doc in bin_docs:
        bins_by_rack.setdefault(doc["rack_id"], []).append(doc)

    zones = []
    for zone_doc in zone_docs:
        areas = []
        for area_doc in areas_by_zone.get(zone_doc["zone_id"], []):
            aisles = []
            for aisle_doc in aisles_by_area.get(area_doc["area_id"], []):
                racks = [
                    RackTree(
                        rack=Rack(**rack_doc),
                        bins=[
                            Bin(**bin_doc)
                            for bin_doc in bins_by_rack.get(rack_doc["rack_id"], [])
                        ],
                    )
                    for rack_doc in racks_by_aisle.get(aisle_doc["aisle_id"], [])
                ]
                aisles.append(AisleTree(aisle=Aisle(**aisle_doc), racks=racks))
            areas.append(AreaTree(area=Area(**area_doc), aisles=aisles))
        zones.append(ZoneTree(zone=Zone(**zone_doc), areas=areas))

    return LocationTree(lot=Lot(**lot_doc), zones=zones)
