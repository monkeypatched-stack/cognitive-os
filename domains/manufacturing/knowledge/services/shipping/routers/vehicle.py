from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.shipping.helpers import vehicle as crud
from services.shipping.models.vehicle import (
    PaginatedVehicleResponse,
    VehicleCreate,
    VehicleResponse,
    VehicleUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedVehicleResponse)
async def list_vehicles(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedVehicleResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-carrier/{carrier_id}", response_model=list[VehicleResponse])
async def list_vehicles_by_carrier(
    carrier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_carrier(db, carrier_id)


@router.get(
    "/by-registration-plate/{registration_plate}", response_model=VehicleResponse
)
async def get_vehicle_by_registration_plate(
    registration_plate: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_registration_plate(db, registration_plate)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Vehicle with registration plate '{registration_plate}' not found",
        )
    return record


@router.get("/by-active/{active}", response_model=list[VehicleResponse])
async def list_vehicles_by_active(
    active: bool,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_active(db, active)


@router.get("/{vehicle_id}", response_model=VehicleResponse)
async def get_vehicle(
    vehicle_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, vehicle_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Vehicle '{vehicle_id}' not found"
        )
    return record


@router.post("/", response_model=VehicleResponse, status_code=status.HTTP_201_CREATED)
async def create_vehicle(
    data: VehicleCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    vehicle_id = str(data.id)
    if await crud.get_by_id(db, vehicle_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Vehicle '{vehicle_id}' already exists"
        )
    if await crud.get_by_registration_plate(db, data.registration_plate):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Vehicle with registration plate '{data.registration_plate}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{vehicle_id}", response_model=VehicleResponse)
async def update_vehicle(
    vehicle_id: str,
    data: VehicleUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    if data.registration_plate:
        existing = await crud.get_by_registration_plate(db, data.registration_plate)
        if existing and existing.get("id") != vehicle_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Vehicle with registration plate '{data.registration_plate}' already exists",
            )
    updated = await crud.update(db, vehicle_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Vehicle '{vehicle_id}' not found"
        )
    return updated


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vehicle(
    vehicle_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, vehicle_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Vehicle '{vehicle_id}' not found"
        )
