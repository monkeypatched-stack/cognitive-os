"""Router for bays — queries plant_locations where type=bay."""

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


def _to_bay(doc: dict, parent_loc_id_map: dict[str, str] | None = None) -> dict:
    parent_id = doc.get("parent_id")
    room_id = (
        parent_loc_id_map.get(parent_id, parent_id) if parent_loc_id_map else parent_id
    )
    return {
        "bay_id": doc.get("location_id") or doc.get("id"),
        "room_id": room_id,
        "name": doc.get("name", ""),
    }


@router.get("/")
async def list_bays(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-bays")),
):
    locs, _ = await get_all_locations(
        db, page=page, page_size=page_size, location_type="bay"
    )

    parent_ids = {loc.get("parent_id") for loc in locs if loc.get("parent_id")}
    parent_loc_id_map: dict[str, str] = {}
    for pid in parent_ids:
        parent_loc = await get_location_by_id(db, pid)
        if parent_loc:
            parent_loc_id_map[pid] = parent_loc.get("location_id", pid)

    return [_to_bay(loc, parent_loc_id_map) for loc in locs]


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_bay(
    data: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-bays")),
):
    from services.facilities.helpers.locations import create_location

    loc_data = LocationCreate(
        name=data["name"],
        location_id=data.get("bay_id")
        or data.get("name", "").upper().replace(" ", "-"),
        type="bay",
        level=5,
        path=data.get("path", data["name"]),
        parent_id=data.get("room_id"),
    )
    created = await create_location(db, loc_data)
    return _to_bay(created)


@router.patch("/{bay_id}")
async def update_bay(
    bay_id: str,
    data: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-bays")),
):
    updated = await update_location(db, bay_id, LocationUpdate(name=data.get("name")))
    if not updated:
        raise HTTPException(status_code=404, detail="Bay not found")
    return _to_bay(updated)


@router.delete("/{bay_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bay(
    bay_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-bays")),
):
    await delete_location(db, bay_id)
