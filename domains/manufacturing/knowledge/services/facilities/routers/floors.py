"""Router for floors — queries plant_locations where type=floor."""

from fastapi import APIRouter, Depends, Query, status, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.facilities.helpers.locations import (
    get_all_locations,
    update_location,
    delete_location,
    get_location_by_id,
)
from services.facilities.models.locations import LocationCreate, LocationUpdate

router = APIRouter()


def _to_floor(doc: dict, parent_loc_id_map: dict[str, str] | None = None) -> dict:
    parent_id = doc.get("parent_id")
    building_id = (
        parent_loc_id_map.get(parent_id, parent_id) if parent_loc_id_map else parent_id
    )
    return {
        "floor_id": doc.get("location_id") or doc.get("id"),
        "building_id": building_id,
        "name": doc.get("name", ""),
    }


@router.get("/")
async def list_floors(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-floors")),
):
    locs, _ = await get_all_locations(
        db, page=page, page_size=page_size, location_type="floor"
    )

    parent_ids = {loc.get("parent_id") for loc in locs if loc.get("parent_id")}
    parent_loc_id_map: dict[str, str] = {}
    for pid in parent_ids:
        parent_loc = await get_location_by_id(db, pid)
        if parent_loc:
            parent_loc_id_map[pid] = parent_loc.get("location_id", pid)

    return [_to_floor(loc, parent_loc_id_map) for loc in locs]


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_floor(
    data: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-floors")),
):
    from services.facilities.helpers.locations import create_location

    loc_data = LocationCreate(
        name=data["name"],
        location_id=data.get("floor_id")
        or data.get("name", "").upper().replace(" ", "-"),
        type="floor",
        level=3,
        path=data.get("path", data["name"]),
        parent_id=data.get("building_id"),
    )
    created = await create_location(db, loc_data)
    return _to_floor(created)


@router.patch("/{floor_id}")
async def update_floor(
    floor_id: str,
    data: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-floors")),
):
    updated = await update_location(db, floor_id, LocationUpdate(name=data.get("name")))
    if not updated:
        raise HTTPException(status_code=404, detail="Floor not found")
    return _to_floor(updated)


@router.delete("/{floor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_floor(
    floor_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-floors")),
):
    await delete_location(db, floor_id)
