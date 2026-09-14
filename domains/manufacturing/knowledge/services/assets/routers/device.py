from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.assets.models.device import DeviceResponse, PaginatedDeviceResponse
from services.common.auth import require_permission
from services.common.db import get_database
from services.assets.helpers.device import DeviceCreate, DeviceUpdate
from services.assets.helpers import device as crud

router = APIRouter()


def get_db() -> AsyncIOMotorDatabase:
    return get_database()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/", response_model=PaginatedDeviceResponse)
async def list_devices(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    devices, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedDeviceResponse(
        total=total, page=page, page_size=page_size, results=devices
    )


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    record = await crud.get_by_id(db, device_id)
    if not record:
        raise HTTPException(404, detail=f"Device '{device_id}' not found")
    return record


@router.post("/", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_device(
    data: DeviceCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-create-devices")),
):
    if await crud.get_by_id(db, data.id):
        raise HTTPException(409, detail=f"Device '{data.id}' already exists")
    return await crud.create(db, data)


@router.patch("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: str,
    data: DeviceUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-update-devices")),
):
    updated = await crud.update(db, device_id, data)
    if not updated:
        raise HTTPException(404, detail=f"Device '{device_id}' not found")
    return updated


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-delete-devices")),
):
    if not await crud.delete(db, device_id):
        raise HTTPException(404, detail=f"Device '{device_id}' not found")


@router.get("/by-user/{user_id}", response_model=list[DeviceResponse])
async def get_devices_by_user(
    user_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    records = await crud.get_by_user(db, user_id)

    return records
