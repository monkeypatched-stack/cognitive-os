import re
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import get_current_user, require_permission
from services.facilities.helpers import workstation as crud
from services.assets.helpers import machines as machine_crud
from services.assets.helpers import equipment as equipment_crud

from services.facilities.models.workstation import (
    WorkstationCreate,
    WorkstationUpdate,
    WorkstationResponse,
    PaginatedWorkstationResponse,
)
from services.assets.models.machines import PharmaceuticalMachineResponse
from services.assets.models.equipment import PharmaceuticalEquipmentResponse

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedWorkstationResponse)
async def list_workstations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    workstations, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
    )
    return PaginatedWorkstationResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=workstations,
    )


# ── Get by line ───────────────────────────────────────────────────────────────


@router.get("/by-line/{line_id}", response_model=list[WorkstationResponse])
async def list_workstations_by_line(
    line_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await crud.get_by_line(db, line_id)


@router.get("/by-stage/{stage_id}", response_model=list[WorkstationResponse])
async def list_workstations_by_stage(
    stage_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await crud.get_by_stage_id(db, stage_id)


@router.get(
    "/{workstation_id}/machines", response_model=list[PharmaceuticalMachineResponse]
)
async def list_workstation_machines(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await crud.get_by_id(db, workstation_id)
    if not record:
        record = await db["workstations"].find_one(
            {"name": {"$regex": f"^{re.escape(workstation_id)}$", "$options": "i"}}
        )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workstation '{workstation_id}' not found",
        )
    return await machine_crud.get_by_workstation(
        db, str(record.get("id") or workstation_id)
    )


@router.get(
    "/{workstation_id}/equipment", response_model=list[PharmaceuticalEquipmentResponse]
)
async def list_workstation_equipment(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await crud.get_by_id(db, workstation_id)
    if not record:
        record = await db["workstations"].find_one(
            {"name": {"$regex": f"^{re.escape(workstation_id)}$", "$options": "i"}}
        )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workstation '{workstation_id}' not found",
        )
    return await equipment_crud.get_by_workstation_id(
        db, str(record.get("id") or workstation_id)
    )


# ── Get bottlenecks ───────────────────────────────────────────────────────────


@router.get("/bottlenecks", response_model=list[WorkstationResponse])
async def list_bottlenecks(
    line_id: Optional[str] = Query(
        None, description="Optionally scope to a specific line"
    ),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await crud.get_bottlenecks(db, line_id=line_id)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{workstation_id}", response_model=WorkstationResponse)
async def get_workstation(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await crud.get_by_id(db, workstation_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workstation '{workstation_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=WorkstationResponse, status_code=status.HTTP_201_CREATED
)
async def create_workstation(
    data: WorkstationCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-workstation")),
):
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workstation '{data.id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{workstation_id}", response_model=WorkstationResponse)
async def update_workstation(
    workstation_id: str,
    data: WorkstationUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-workstation")),
):
    updated = await crud.update(db, workstation_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workstation '{workstation_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{workstation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workstation(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-workstation")),
):
    if not await crud.delete(db, workstation_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workstation '{workstation_id}' not found",
        )
