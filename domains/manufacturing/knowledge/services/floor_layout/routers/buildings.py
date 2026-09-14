from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.floor_layout.helpers import buildings as crud

from services.floor_layout.models.buildings import (
    BuildingCreate,
    BuildingUpdate,
    BuildingResponse,
    PaginatedBuildingResponse,
)

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedBuildingResponse)
async def list_buildings(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-buildings")),
):
    buildings, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedBuildingResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=buildings,
    )


# ── Get by facility ───────────────────────────────────────────────────────────


@router.get("/by-facility/{facility_id}", response_model=list[BuildingResponse])
async def list_buildings_by_facility(
    facility_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-buildings")),
):
    return await crud.get_by_facility(db, facility_id)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{building_id}", response_model=BuildingResponse)
async def get_building(
    building_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-buildings")),
):
    record = await crud.get_by_id(db, building_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Building '{building_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("/", response_model=BuildingResponse, status_code=status.HTTP_201_CREATED)
async def create_building(
    data: BuildingCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-buildings")),
):
    if await crud.get_by_id(db, data.building_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Building '{data.building_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{building_id}", response_model=BuildingResponse)
async def update_building(
    building_id: str,
    data: BuildingUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-buildings")),
):
    updated = await crud.update(db, building_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Building '{building_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{building_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_building(
    building_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-buildings")),
):
    if not await crud.delete(db, building_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Building '{building_id}' not found",
        )


# ── Future Extensions ─────────────────────────────────────────────────────────

# TODO_ENDPOINT: GET /api/v1/buildings/{building_id}/floors — list floors in a building
# TODO_ENDPOINT: GET /api/v1/buildings/{building_id}/rooms — list rooms in a building
