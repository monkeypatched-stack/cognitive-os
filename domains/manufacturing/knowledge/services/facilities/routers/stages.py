import re
from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import get_current_user, require_permission
from services.facilities.helpers import stages as crud
from services.facilities.helpers import workstation as workstation_crud

from services.facilities.models.stages import (
    IndustrialStageCreate,
    IndustrialStageUpdate,
    IndustrialStageResponse,
    PaginatedStageResponse,
)
from services.facilities.models.workstation import WorkstationResponse

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedStageResponse)
async def list_stages(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(
        require_permission("perm-view-stages")
    ),  # ⚠️ stages tied to stages
):
    stages, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
    )
    return PaginatedStageResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=stages,
    )


# ── Get by circuit ────────────────────────────────────────────────────────────


@router.get("/by-circuit/{circuit_id}", response_model=list[IndustrialStageResponse])
async def list_stages_by_circuit(
    circuit_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-stages")),
):
    return await crud.get_by_circuit(db, circuit_id)


@router.get("/by-line/{line_id}", response_model=list[IndustrialStageResponse])
async def list_stages_by_line(
    line_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-lines")),
):
    return await crud.get_by_line(db, line_id)


@router.get("/by-plant/{plant_id}", response_model=list[IndustrialStageResponse])
async def list_stages_by_plant(
    plant_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-stages")),
):
    return await crud.get_by_plant(db, plant_id)


@router.get("/{stage_id}/workstations", response_model=list[WorkstationResponse])
async def list_stage_workstations(
    stage_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await crud.get_by_id(db, stage_id)
    if not record:
        record = await db["industrial_stages"].find_one(
            {"name": {"$regex": f"^{re.escape(stage_id)}$", "$options": "i"}}
        )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stage '{stage_id}' not found",
        )
    return await workstation_crud.get_by_stage_id(db, str(record.get("id") or stage_id))


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{stage_id}", response_model=IndustrialStageResponse)
async def get_stage(
    stage_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-stages")),
):
    record = await crud.get_by_id(db, stage_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stage '{stage_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=IndustrialStageResponse, status_code=status.HTTP_201_CREATED
)
async def create_stage(
    data: IndustrialStageCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-stages")),
):
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Stage '{data.id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{stage_id}", response_model=IndustrialStageResponse)
async def update_stage(
    stage_id: str,
    data: IndustrialStageUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-stages")),
):
    updated = await crud.update(db, stage_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stage '{stage_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{stage_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stage(
    stage_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-stages")),
):
    if not await crud.delete(db, stage_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stage '{stage_id}' not found",
        )
