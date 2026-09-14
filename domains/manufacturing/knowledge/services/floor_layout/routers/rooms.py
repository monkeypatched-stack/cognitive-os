from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.floor_layout.helpers import rooms as crud

from services.floor_layout.models.rooms import (
    RoomCreate,
    RoomUpdate,
    RoomResponse,
    PaginatedRoomResponse,
)

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedRoomResponse)
async def list_rooms(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-rooms")),
):
    rooms, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedRoomResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=rooms,
    )


# ── Hierarchy Queries ─────────────────────────────────────────────────────────


@router.get("/by-floor/{floor_id}", response_model=list[RoomResponse])
async def list_rooms_by_floor(
    floor_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-rooms")),
):
    return await crud.get_by_floor(db, floor_id)


@router.get("/by-building/{building_id}", response_model=list[RoomResponse])
async def list_rooms_by_building(
    building_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-rooms")),
):
    return await crud.get_by_building(db, building_id)


@router.get("/by-facility/{facility_id}", response_model=list[RoomResponse])
async def list_rooms_by_facility(
    facility_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-rooms")),
):
    return await crud.get_by_facility(db, facility_id)


# ── Get One ───────────────────────────────────────────────────────────────────


@router.get("/{room_id}", response_model=RoomResponse)
async def get_room(
    room_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-rooms")),
):
    record = await crud.get_by_id(db, room_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Room '{room_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("/", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
async def create_room(
    data: RoomCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-rooms")),
):
    if await crud.get_by_id(db, data.room_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Room '{data.room_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{room_id}", response_model=RoomResponse)
async def update_room(
    room_id: str,
    data: RoomUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-rooms")),
):
    updated = await crud.update(db, room_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Room '{room_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_room(
    room_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-rooms")),
):
    if not await crud.delete(db, room_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Room '{room_id}' not found",
        )


# ── Future Extensions ─────────────────────────────────────────────────────────

# TODO_ENDPOINT: GET /api/v1/rooms/{room_id}/bays — list bays in a room
# TODO_ENDPOINT: GET /api/v1/rooms/{room_id}/devices — list devices in a room
