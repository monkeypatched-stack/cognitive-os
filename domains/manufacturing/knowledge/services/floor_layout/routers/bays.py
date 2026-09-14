from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.floor_layout.helpers import bays as crud

from services.floor_layout.models.bays import (
    BayCreate,
    BayUpdate,
    BayResponse,
    PaginatedBayResponse,
)

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedBayResponse)
async def list_bays(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-bays")),
):
    bays, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedBayResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=bays,
    )


# ── Hierarchy Queries ─────────────────────────────────────────────────────────


@router.get("/by-room/{room_id}", response_model=list[BayResponse])
async def list_bays_by_room(
    room_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-bays")),
):
    return await crud.get_by_room(db, room_id)


@router.get("/by-floor/{floor_id}", response_model=list[BayResponse])
async def list_bays_by_floor(
    floor_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-bays")),
):
    return await crud.get_by_floor(db, floor_id)


@router.get("/by-building/{building_id}", response_model=list[BayResponse])
async def list_bays_by_building(
    building_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-bays")),
):
    return await crud.get_by_building(db, building_id)


@router.get("/by-facility/{facility_id}", response_model=list[BayResponse])
async def list_bays_by_facility(
    facility_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-bays")),
):
    return await crud.get_by_facility(db, facility_id)


# ── Get One ───────────────────────────────────────────────────────────────────


@router.get("/{bay_id}", response_model=BayResponse)
async def get_bay(
    bay_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-bays")),
):
    record = await crud.get_by_id(db, bay_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bay '{bay_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("/", response_model=BayResponse, status_code=status.HTTP_201_CREATED)
async def create_bay(
    data: BayCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-bays")),
):
    if await crud.get_by_id(db, data.bay_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Bay '{data.bay_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{bay_id}", response_model=BayResponse)
async def update_bay(
    bay_id: str,
    data: BayUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-bays")),
):
    updated = await crud.update(db, bay_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bay '{bay_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{bay_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bay(
    bay_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-bays")),
):
    if not await crud.delete(db, bay_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bay '{bay_id}' not found",
        )


# ── Future Extensions ─────────────────────────────────────────────────────────

# TODO_ENDPOINT: GET /api/v1/bays/{bay_id}/machines — list machines in a bay
# TODO_ENDPOINT: GET /api/v1/bays/{bay_id}/sensors — list sensors in a bay
