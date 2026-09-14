from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from services.common.db import get_database
from services.common.auth import require_permission

from services.floor_layout.helpers import bays as bays_crud
from services.floor_layout.helpers import rooms as room_crud
from services.floor_layout.helpers import floors as floor_crud
from services.floor_layout.helpers import buildings as building_crud
from services.facilities.helpers import plants as plant_crud

router = APIRouter()


class LocationsResponse(BaseModel):
    plant_name: str
    building_name: str
    floor_name: str
    room_name: str
    bay_name: str


@router.get("/", response_model=LocationsResponse)
async def get_location(
    machine_id: str = Query(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    user: dict = Depends(require_permission("perm-view-locations")),
):
    # ── Step 1: Get Bay ─────────────────────────────────────────────
    bays = await bays_crud.get_by_machine(db, machine_id)

    if not bays:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No bay found for machine '{machine_id}'",
        )

    bay = bays[0]
    bay_name = bay.get("name")
    room_id = bay.get("room_id")

    # ── Step 2: Room ────────────────────────────────────────────────
    room = await room_crud.get_by_id(db, room_id)
    if not room:
        raise HTTPException(404, f"Room '{room_id}' not found")

    room_name = room.get("name")
    floor_id = room.get("floor_id")

    # ── Step 3: Floor ───────────────────────────────────────────────
    floor = await floor_crud.get_by_id(db, floor_id)
    if not floor:
        raise HTTPException(404, f"Floor '{floor_id}' not found")

    floor_name = floor.get("name")
    building_id = floor.get("building_id")

    # ── Step 4: Building ────────────────────────────────────────────
    building = await building_crud.get_by_id(db, building_id)
    if not building:
        raise HTTPException(404, f"Building '{building_id}' not found")

    building_name = building.get("name")
    plant_id = building.get("plant_id")

    # ── Step 5: Plant ───────────────────────────────────────────────
    plant = await plant_crud.get_by_id(db, plant_id)
    if not plant:
        raise HTTPException(404, f"Plant '{plant_id}' not found")

    # ── Final Response ──────────────────────────────────────────────
    return LocationsResponse(
        plant_name=plant.get("name"),
        building_name=building_name,
        floor_name=floor_name,
        room_name=room_name,
        bay_name=bay_name,
    )
