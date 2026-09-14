from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.assets.models.machines import (
    PharmaceuticalMachineCreate,
    PharmaceuticalMachineUpdate,
    PharmaceuticalMachineResponse,
    PaginatedMachineResponse,
)
from services.assets.helpers import machines as crud

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedMachineResponse)
async def list_machines(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-machines")),
):
    machines, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedMachineResponse(
        total=total, page=page, page_size=page_size, results=machines
    )


# ── Get by facility ───────────────────────────────────────────────────────────


@router.get(
    "/by-facility/{facility}", response_model=list[PharmaceuticalMachineResponse]
)
async def list_machines_by_facility(
    facility: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-machines")),
):
    return await crud.get_by_facility(db, facility)


# ── Get by status ─────────────────────────────────────────────────────────────


@router.get(
    "/by-status/{status_value}", response_model=list[PharmaceuticalMachineResponse]
)
async def list_machines_by_status(
    status_value: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-machines")),
):
    return await crud.get_by_status(db, status_value)


# ── Get by type ───────────────────────────────────────────────────────────────


@router.get(
    "/by-type/{type_category}", response_model=list[PharmaceuticalMachineResponse]
)
async def list_machines_by_type(
    type_category: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-machines")),
):
    return await crud.get_by_type(db, type_category)


@router.get(
    "/by-workstation/{workstation_id}",
    response_model=list[PharmaceuticalMachineResponse],
)
async def list_machines_by_workstation(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-machines")),
):
    return await crud.get_by_workstation(db, workstation_id)


@router.get("/by-plant/{plant_id}", response_model=list[PharmaceuticalMachineResponse])
async def list_machines_by_plant(
    plant_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-machines")),
):
    return await crud.get_by_plant(db, plant_id)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{machine_id}", response_model=PharmaceuticalMachineResponse)
async def get_machine(
    machine_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-machines")),
):
    record = await crud.get_by_id(db, machine_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Machine '{machine_id}' not found"
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=PharmaceuticalMachineResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_machine(
    data: PharmaceuticalMachineCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-machines")),
):
    if await crud.get_by_id(db, data.machine_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Machine '{data.machine_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{machine_id}", response_model=PharmaceuticalMachineResponse)
async def update_machine(
    machine_id: str,
    data: PharmaceuticalMachineUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-machines")),
):
    updated = await crud.update(db, machine_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Machine '{machine_id}' not found"
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{machine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_machine(
    machine_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-machines")),
):
    if not await crud.delete(db, machine_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Machine '{machine_id}' not found"
        )


# TODO_ENDPOINT: GET /api/v1/machines/{machine_id}/maintenance — list all maintenance records for a machine
# TODO_ENDPOINT: GET /api/v1/machines/{machine_id}/calibrations — list all calibration records for a machine
