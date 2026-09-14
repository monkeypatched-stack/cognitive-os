"""Router for buildings — queries plant_locations where type=building."""

from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.facilities.helpers.locations import (
    get_all_locations,
    get_location_by_id,
    create_location,
    update_location,
    delete_location,
)
from services.facilities.models.locations import (
    LocationCreate,
    LocationUpdate,
    LocationResponse,
)

router = APIRouter()

_plant_loc_to_id_cache: dict[str, str] = {}


def _to_building(doc: dict, plant_loc_to_id: dict[str, str] | None = None) -> dict:
    parent_id = doc.get("parent_id")
    plant_id = (
        plant_loc_to_id.get(parent_id, parent_id) if plant_loc_to_id else parent_id
    )
    return {
        "building_id": doc.get("location_id") or doc.get("id"),
        "plant_id": plant_id,
        "name": doc.get("name", ""),
    }


@router.get("/")
async def list_buildings(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-buildings")),
):
    locs, total = await get_all_locations(
        db, page=page, page_size=page_size, location_type="building"
    )

    plant_loc_ids = {loc.get("parent_id") for loc in locs if loc.get("parent_id")}
    plant_loc_to_id: dict[str, str] = {}
    for loc_id in plant_loc_ids:
        plant_doc = await db["industrial_plants"].find_one(
            {"$or": [{"id": loc_id}, {"_id": loc_id}]}, {"id": 1}
        )
        if plant_doc:
            plant_loc_to_id[loc_id] = plant_doc.get("id", loc_id)
        else:
            plant_loc = await get_location_by_id(db, loc_id)
            if plant_loc:
                plant_loc_to_id[loc_id] = plant_loc.get("location_id", loc_id)

    return [_to_building(loc, plant_loc_to_id) for loc in locs]


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_building(
    data: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-buildings")),
):
    loc_data = LocationCreate(
        name=data["name"],
        location_id=data.get("building_id")
        or data.get("name", "").upper().replace(" ", "-"),
        type="building",
        level=2,
        path=data.get("path", data["name"]),
        description=data.get("description", ""),
        parent_id=data.get("plant_id"),
    )
    created = await create_location(db, loc_data)
    return _to_building(created)


@router.patch("/{building_id}")
async def update_building(
    building_id: str,
    data: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-buildings")),
):
    update = LocationUpdate(name=data.get("name"))
    updated = await update_location(db, building_id, update)
    if not updated:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Building not found")
    return _to_building(updated)


@router.delete("/{building_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_building(
    building_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-buildings")),
):
    await delete_location(db, building_id)
