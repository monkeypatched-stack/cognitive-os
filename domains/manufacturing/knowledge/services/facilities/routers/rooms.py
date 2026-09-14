"""Router for rooms — queries plant_locations where type=room."""

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


def _to_room(doc: dict, parent_loc_id_map: dict[str, str] | None = None) -> dict:
    parent_id = doc.get("parent_id")
    floor_id = (
        parent_loc_id_map.get(parent_id, parent_id) if parent_loc_id_map else parent_id
    )
    return {
        "room_id": doc.get("location_id") or doc.get("id"),
        "floor_id": floor_id,
        "name": doc.get("name", ""),
    }


@router.get("/")
async def list_rooms(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-rooms")),
):
    locs, _ = await get_all_locations(
        db, page=page, page_size=page_size, location_type="room"
    )

    parent_ids = {loc.get("parent_id") for loc in locs if loc.get("parent_id")}
    parent_loc_id_map: dict[str, str] = {}
    for pid in parent_ids:
        parent_loc = await get_location_by_id(db, pid)
        if parent_loc:
            parent_loc_id_map[pid] = parent_loc.get("location_id", pid)

    return [_to_room(loc, parent_loc_id_map) for loc in locs]


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_room(
    data: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-rooms")),
):
    from services.facilities.helpers.locations import create_location

    loc_data = LocationCreate(
        name=data["name"],
        location_id=data.get("room_id")
        or data.get("name", "").upper().replace(" ", "-"),
        type="room",
        level=4,
        path=data.get("path", data["name"]),
        parent_id=data.get("floor_id"),
    )
    created = await create_location(db, loc_data)
    return _to_room(created)


@router.patch("/{room_id}")
async def update_room(
    room_id: str,
    data: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-rooms")),
):
    updated = await update_location(db, room_id, LocationUpdate(name=data.get("name")))
    if not updated:
        raise HTTPException(status_code=404, detail="Room not found")
    return _to_room(updated)


@router.delete("/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_room(
    room_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-rooms")),
):
    await delete_location(db, room_id)
