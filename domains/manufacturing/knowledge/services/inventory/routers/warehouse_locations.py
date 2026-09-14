from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.inventory.helpers import warehouse_locations as crud
from services.inventory.models.warehouse_location_models import (
    Aisle,
    AisleCreate,
    AisleUpdate,
    Area,
    AreaCreate,
    AreaUpdate,
    Bin,
    BinCreate,
    BinUpdate,
    LocationTree,
    Lot,
    LotCreate,
    LotUpdate,
    PaginatedAisleResponse,
    PaginatedAreaResponse,
    PaginatedBinResponse,
    PaginatedLotResponse,
    PaginatedRackResponse,
    PaginatedZoneResponse,
    Rack,
    RackCreate,
    RackUpdate,
    WarehouseLocation,
    Zone,
    ZoneCreate,
    ZoneUpdate,
)

router = APIRouter()


def _page(total: int, page: int, page_size: int, records: list[dict], response_cls):
    return response_cls(total=total, page=page, page_size=page_size, results=records)


@router.get("/lots", response_model=PaginatedLotResponse)
async def list_lots(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    warehouse_id: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    query = {"warehouse_id": warehouse_id} if warehouse_id else None
    records, total = await crud.get_all(
        db, "lots", page=page, page_size=page_size, query=query
    )
    return _page(total, page, page_size, records, PaginatedLotResponse)


@router.post("/lots", response_model=Lot, status_code=status.HTTP_201_CREATED)
async def create_lot(
    data: LotCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    if await crud.get_by_id(db, "lots", data.lot_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Lot '{data.lot_id}' already exists",
        )
    return await crud.create(db, "lots", data)


@router.get("/lots/{lot_id}", response_model=Lot)
async def get_lot(
    lot_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_id(db, "lots", lot_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Lot '{lot_id}' not found"
        )
    return record


@router.patch("/lots/{lot_id}", response_model=Lot)
async def update_lot(
    lot_id: str,
    data: LotUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    record = await crud.update(db, "lots", lot_id, data)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Lot '{lot_id}' not found"
        )
    return record


@router.delete("/lots/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lot(
    lot_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, "lots", lot_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Lot '{lot_id}' not found"
        )


@router.get("/lots/{lot_id}/tree", response_model=LocationTree)
async def get_lot_tree(
    lot_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    tree = await crud.get_tree_for_lot(db, lot_id)
    if not tree:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Lot '{lot_id}' not found"
        )
    return tree


@router.get("/zones", response_model=PaginatedZoneResponse)
async def list_zones(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    lot_id: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    query = {"lot_id": lot_id} if lot_id else None
    records, total = await crud.get_all(
        db, "zones", page=page, page_size=page_size, query=query
    )
    return _page(total, page, page_size, records, PaginatedZoneResponse)


@router.post("/zones", response_model=Zone, status_code=status.HTTP_201_CREATED)
async def create_zone(
    data: ZoneCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    if await crud.get_by_id(db, "zones", data.zone_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Zone '{data.zone_id}' already exists",
        )
    return await crud.create(db, "zones", data)


@router.get("/zones/{zone_id}", response_model=Zone)
async def get_zone(
    zone_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_id(db, "zones", zone_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Zone '{zone_id}' not found"
        )
    return record


@router.patch("/zones/{zone_id}", response_model=Zone)
async def update_zone(
    zone_id: str,
    data: ZoneUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    record = await crud.update(db, "zones", zone_id, data)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Zone '{zone_id}' not found"
        )
    return record


@router.delete("/zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone(
    zone_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, "zones", zone_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Zone '{zone_id}' not found"
        )


@router.get("/areas", response_model=PaginatedAreaResponse)
async def list_areas(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    zone_id: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    query = {"zone_id": zone_id} if zone_id else None
    records, total = await crud.get_all(
        db, "areas", page=page, page_size=page_size, query=query
    )
    return _page(total, page, page_size, records, PaginatedAreaResponse)


@router.post("/areas", response_model=Area, status_code=status.HTTP_201_CREATED)
async def create_area(
    data: AreaCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    if await crud.get_by_id(db, "areas", data.area_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Area '{data.area_id}' already exists",
        )
    return await crud.create(db, "areas", data)


@router.get("/areas/{area_id}", response_model=Area)
async def get_area(
    area_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_id(db, "areas", area_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Area '{area_id}' not found"
        )
    return record


@router.patch("/areas/{area_id}", response_model=Area)
async def update_area(
    area_id: str,
    data: AreaUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    record = await crud.update(db, "areas", area_id, data)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Area '{area_id}' not found"
        )
    return record


@router.delete("/areas/{area_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_area(
    area_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, "areas", area_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Area '{area_id}' not found"
        )


@router.get("/aisles", response_model=PaginatedAisleResponse)
async def list_aisles(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    area_id: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    query = {"area_id": area_id} if area_id else None
    records, total = await crud.get_all(
        db, "aisles", page=page, page_size=page_size, query=query
    )
    return _page(total, page, page_size, records, PaginatedAisleResponse)


@router.post("/aisles", response_model=Aisle, status_code=status.HTTP_201_CREATED)
async def create_aisle(
    data: AisleCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    if await crud.get_by_id(db, "aisles", data.aisle_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Aisle '{data.aisle_id}' already exists",
        )
    return await crud.create(db, "aisles", data)


@router.get("/aisles/{aisle_id}", response_model=Aisle)
async def get_aisle(
    aisle_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_id(db, "aisles", aisle_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aisle '{aisle_id}' not found",
        )
    return record


@router.patch("/aisles/{aisle_id}", response_model=Aisle)
async def update_aisle(
    aisle_id: str,
    data: AisleUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    record = await crud.update(db, "aisles", aisle_id, data)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aisle '{aisle_id}' not found",
        )
    return record


@router.delete("/aisles/{aisle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_aisle(
    aisle_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, "aisles", aisle_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aisle '{aisle_id}' not found",
        )


@router.get("/racks", response_model=PaginatedRackResponse)
async def list_racks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    aisle_id: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    query = {"aisle_id": aisle_id} if aisle_id else None
    records, total = await crud.get_all(
        db, "racks", page=page, page_size=page_size, query=query
    )
    return _page(total, page, page_size, records, PaginatedRackResponse)


@router.post("/racks", response_model=Rack, status_code=status.HTTP_201_CREATED)
async def create_rack(
    data: RackCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    if await crud.get_by_id(db, "racks", data.rack_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Rack '{data.rack_id}' already exists",
        )
    return await crud.create(db, "racks", data)


@router.get("/racks/{rack_id}", response_model=Rack)
async def get_rack(
    rack_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_id(db, "racks", rack_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Rack '{rack_id}' not found"
        )
    return record


@router.patch("/racks/{rack_id}", response_model=Rack)
async def update_rack(
    rack_id: str,
    data: RackUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    record = await crud.update(db, "racks", rack_id, data)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Rack '{rack_id}' not found"
        )
    return record


@router.delete("/racks/{rack_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rack(
    rack_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, "racks", rack_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Rack '{rack_id}' not found"
        )


@router.get("/bins", response_model=PaginatedBinResponse)
async def list_bins(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    rack_id: str | None = None,
    status_value: str | None = Query(None, alias="status"),
    is_occupied: bool | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    query = {}
    if rack_id:
        query["rack_id"] = rack_id
    if status_value:
        query["status"] = status_value
    if is_occupied is not None:
        query["is_occupied"] = is_occupied
    records, total = await crud.get_all(
        db, "bins", page=page, page_size=page_size, query=query
    )
    return _page(total, page, page_size, records, PaginatedBinResponse)


@router.post("/bins", response_model=Bin, status_code=status.HTTP_201_CREATED)
async def create_bin(
    data: BinCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    if await crud.get_by_id(db, "bins", data.bin_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Bin '{data.bin_id}' already exists",
        )
    return await crud.create(db, "bins", data)


@router.get("/bins/{bin_id}", response_model=Bin)
async def get_bin(
    bin_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_id(db, "bins", bin_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Bin '{bin_id}' not found"
        )
    return record


@router.patch("/bins/{bin_id}", response_model=Bin)
async def update_bin(
    bin_id: str,
    data: BinUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    record = await crud.update(db, "bins", bin_id, data)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Bin '{bin_id}' not found"
        )
    return record


@router.delete("/bins/{bin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bin(
    bin_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, "bins", bin_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Bin '{bin_id}' not found"
        )


@router.get("/bins/{bin_id}/location", response_model=WarehouseLocation)
async def get_bin_location(
    bin_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    location = await crud.get_location_for_bin(db, bin_id)
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Location for bin '{bin_id}' not found",
        )
    return location
