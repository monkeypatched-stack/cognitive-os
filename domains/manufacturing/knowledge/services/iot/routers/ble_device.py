from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.iot.helpers import ble_device as crud
from services.iot.models.ble_device import (
    BLEDeviceCreate,
    BLEDeviceResponse,
    BLEDeviceUpdate,
    PaginatedBLEDeviceResponse,
)
from services.common.auth import require_permission
from services.common.db import get_database

router = APIRouter()


@router.get("/", response_model=PaginatedBLEDeviceResponse)
async def list_ble_devices(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedBLEDeviceResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-mac/{mac_address}", response_model=BLEDeviceResponse)
async def get_ble_device_by_mac_address(
    mac_address: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    record = await crud.get_by_mac_address(db, mac_address)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"BLE device '{mac_address}' not found"
        )
    return record


@router.get("/by-active/{active}", response_model=list[BLEDeviceResponse])
async def list_ble_devices_by_active(
    active: bool,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    return await crud.get_by_active(db, active)


@router.get("/by-tag/{tag}", response_model=list[BLEDeviceResponse])
async def list_ble_devices_by_tag(
    tag: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    return await crud.get_by_tag(db, tag)


@router.get("/{device_id}", response_model=BLEDeviceResponse)
async def get_ble_device(
    device_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    record = await crud.get_by_id(db, device_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"BLE device '{device_id}' not found"
        )
    return record


@router.post("/", response_model=BLEDeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_ble_device(
    data: BLEDeviceCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-devices")),
):
    device_id = str(data.id)
    if await crud.get_by_id(db, device_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"BLE device '{device_id}' already exists"
        )
    if await crud.get_by_mac_address(db, data.mac_address):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"BLE device '{data.mac_address}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{device_id}", response_model=BLEDeviceResponse)
async def update_ble_device(
    device_id: str,
    data: BLEDeviceUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-devices")),
):
    if data.mac_address:
        existing = await crud.get_by_mac_address(db, data.mac_address)
        if existing and existing.get("id") != device_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"BLE device '{data.mac_address}' already exists",
            )

    updated = await crud.update(db, device_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"BLE device '{device_id}' not found"
        )
    return updated


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ble_device(
    device_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-devices")),
):
    if not await crud.delete(db, device_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"BLE device '{device_id}' not found"
        )
