from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.db import get_database
from services.floor_layout.models.floors import (
    FloorCreate,
    FloorUpdate,
    FloorResponse,
    PaginatedFloorResponse,
)
from services.floor_layout.helpers import floors as crud
from services.common.auth import require_permission

router = APIRouter()


@router.get("/", response_model=PaginatedFloorResponse)
async def list_floors(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-floors")),
):
    floors, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedFloorResponse(
        total=total, page=page, page_size=page_size, results=floors
    )


@router.get("/by-building/{building_id}", response_model=list[FloorResponse])
async def list_floors_by_building(
    building_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-floors")),
):
    return await crud.get_by_building(db, building_id)


@router.get("/by-facility/{facility_id}", response_model=list[FloorResponse])
async def list_floors_by_facility(
    facility_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    return await crud.get_by_facility(db, facility_id)


@router.get("/{floor_id}", response_model=FloorResponse)
async def get_floor(
    floor_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-floors")),
):
    record = await crud.get_by_id(db, floor_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Floor '{floor_id}' not found",
        )
    return record


@router.post("/", response_model=FloorResponse, status_code=status.HTTP_201_CREATED)
async def create_floor(
    data: FloorCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-floors")),
):
    if await crud.get_by_id(db, data.floor_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Floor '{data.floor_id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{floor_id}", response_model=FloorResponse)
async def update_floor(
    floor_id: str,
    data: FloorUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-floors")),
):
    updated = await crud.update(db, floor_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Floor '{floor_id}' not found",
        )
    return updated


@router.delete("/{floor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_floor(
    floor_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-floors")),
):
    if not await crud.delete(db, floor_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Floor '{floor_id}' not found",
        )


# TODO_ENDPOINT: GET /api/v1/floors/{floor_id}/rooms — list rooms on a floor
