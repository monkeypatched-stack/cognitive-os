from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.db import get_database
from services.assets.models.equipment import (
    PharmaceuticalEquipmentCreate,
    PharmaceuticalEquipmentUpdate,
    PharmaceuticalEquipmentResponse,
    PaginatedEquipmentResponse,
)
from services.assets.helpers import equipment as crud
from fastapi import Depends
from services.common.auth import get_current_user

# Every route here was UNAUTHENTICATED — anyone could read, create, modify and DELETE
# pharmaceutical equipment records (incl. POST/PATCH/DELETE). Sibling routers in this service already require auth; this was an oversight.
router = APIRouter(dependencies=[Depends(get_current_user)])


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedEquipmentResponse)
async def list_equipment(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    equipment, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedEquipmentResponse(
        total=total, page=page, page_size=page_size, results=equipment
    )


# ── Get by location ───────────────────────────────────────────────────────────


@router.get(
    "/by-location/{location}", response_model=list[PharmaceuticalEquipmentResponse]
)
async def list_equipment_by_location(
    location: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    return await crud.get_by_location(db, location)


# ── Get by status ─────────────────────────────────────────────────────────────


@router.get(
    "/by-status/{status_value}", response_model=list[PharmaceuticalEquipmentResponse]
)
async def list_equipment_by_status(
    status_value: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    return await crud.get_by_status(db, status_value)


@router.get(
    "/by-workstation/{workstation_id}",
    response_model=list[PharmaceuticalEquipmentResponse],
)
async def list_equipment_by_workstation(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    return await crud.get_by_workstation_id(db, workstation_id)


@router.get(
    "/by-plant/{plant_id}", response_model=list[PharmaceuticalEquipmentResponse]
)
async def list_equipment_by_plant(
    plant_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    return await crud.get_by_plant(db, plant_id)


# ── Get by category ───────────────────────────────────────────────────────────


@router.get(
    "/by-category/{category}", response_model=list[PharmaceuticalEquipmentResponse]
)
async def list_equipment_by_category(
    category: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    return await crud.get_by_category(db, category)


# ── Get by assigned to ────────────────────────────────────────────────────────


@router.get(
    "/by-assigned/{assigned_to}", response_model=list[PharmaceuticalEquipmentResponse]
)
async def list_equipment_by_assigned(
    assigned_to: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    return await crud.get_by_assigned_to(db, assigned_to)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{equipment_id}", response_model=PharmaceuticalEquipmentResponse)
async def get_equipment(
    equipment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    record = await crud.get_by_id(db, equipment_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Equipment '{equipment_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=PharmaceuticalEquipmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_equipment(
    data: PharmaceuticalEquipmentCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    if await crud.get_by_id(db, data.equipment_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Equipment '{data.equipment_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{equipment_id}", response_model=PharmaceuticalEquipmentResponse)
async def update_equipment(
    equipment_id: str,
    data: PharmaceuticalEquipmentUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    updated = await crud.update(db, equipment_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Equipment '{equipment_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{equipment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_equipment(
    equipment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    if not await crud.delete(db, equipment_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Equipment '{equipment_id}' not found",
        )


# TODO_ENDPOINT: GET /api/v1/equipment/{equipment_id}/maintenance — list maintenance records for equipment
# TODO_ENDPOINT: GET /api/v1/equipment/{equipment_id}/calibrations — list calibration records for equipment
