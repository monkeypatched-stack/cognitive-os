from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.iot.helpers import uwb_device as crud
from services.iot.models.uwb_device import (
    PaginatedUWBDeviceResponse,
    UWBDeviceCreate,
    UWBDeviceResponse,
    UWBDeviceUpdate,
)
from services.common.auth import require_permission
from services.common.db import get_database

router = APIRouter()


@router.get("/", response_model=PaginatedUWBDeviceResponse)
async def list_uwb_devices(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedUWBDeviceResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-mac/{mac_address}", response_model=UWBDeviceResponse)
async def get_uwb_device_by_mac_address(
    mac_address: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    record = await crud.get_by_mac_address(db, mac_address)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"UWB device '{mac_address}' not found"
        )
    return record


@router.get("/by-serial/{serial_number}", response_model=UWBDeviceResponse)
async def get_uwb_device_by_serial_number(
    serial_number: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    record = await crud.get_by_serial_number(db, serial_number)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"UWB device serial '{serial_number}' not found",
        )
    return record


@router.get("/by-anchor/{anchor_id}", response_model=UWBDeviceResponse)
async def get_uwb_device_by_anchor_id(
    anchor_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    record = await crud.get_by_anchor_id(db, anchor_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"UWB anchor '{anchor_id}' not found"
        )
    return record


@router.get("/by-active/{active}", response_model=list[UWBDeviceResponse])
async def list_uwb_devices_by_active(
    active: bool,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    return await crud.get_by_active(db, active)


@router.get("/by-tag/{tag}", response_model=list[UWBDeviceResponse])
async def list_uwb_devices_by_tag(
    tag: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    return await crud.get_by_tag(db, tag)


@router.get("/{device_id}", response_model=UWBDeviceResponse)
async def get_uwb_device(
    device_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-devices")),
):
    record = await crud.get_by_id(db, device_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"UWB device '{device_id}' not found"
        )
    return record


@router.post("/", response_model=UWBDeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_uwb_device(
    data: UWBDeviceCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-devices")),
):
    if not data.anchor_config:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="anchor_config is required"
        )
    device_id = str(data.id)
    if await crud.get_by_id(db, device_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"UWB device '{device_id}' already exists"
        )
    if data.mac_address and await crud.get_by_mac_address(db, data.mac_address):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"UWB device '{data.mac_address}' already exists",
        )
    if data.serial_number and await crud.get_by_serial_number(db, data.serial_number):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"UWB device serial '{data.serial_number}' already exists",
        )
    if await crud.get_by_anchor_id(db, data.anchor_config.anchor_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"UWB anchor '{data.anchor_config.anchor_id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{device_id}", response_model=UWBDeviceResponse)
async def update_uwb_device(
    device_id: str,
    data: UWBDeviceUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-devices")),
):
    if data.mac_address:
        existing = await crud.get_by_mac_address(db, data.mac_address)
        if existing and existing.get("id") != device_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"UWB device '{data.mac_address}' already exists",
            )
    if data.serial_number:
        existing = await crud.get_by_serial_number(db, data.serial_number)
        if existing and existing.get("id") != device_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"UWB device serial '{data.serial_number}' already exists",
            )
    if data.anchor_config:
        existing = await crud.get_by_anchor_id(db, data.anchor_config.anchor_id)
        if existing and existing.get("id") != device_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"UWB anchor '{data.anchor_config.anchor_id}' already exists",
            )

    updated = await crud.update(db, device_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"UWB device '{device_id}' not found"
        )
    return updated


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_uwb_device(
    device_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-devices")),
):
    if not await crud.delete(db, device_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"UWB device '{device_id}' not found"
        )
