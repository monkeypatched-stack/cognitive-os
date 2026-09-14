import re
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import get_current_user, require_permission
from services.facilities.helpers import lines as crud
from services.facilities.helpers import stages as stage_crud
from services.facilities.helpers import workstation as workstation_crud

from services.facilities.models.industrialLine import (
    IndustrialLineCreate,
    IndustrialLineUpdate,
    IndustrialLineResponse,
    PaginatedLineResponse,
)
from services.facilities.models.stages import IndustrialStageResponse
from services.facilities.models.workstation import WorkstationResponse

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedLineResponse)
async def list_lines(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-lines")),
):
    lines, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
    )
    return PaginatedLineResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=lines,
    )


# ── Get by plant ──────────────────────────────────────────────────────────────


@router.get("/by-plant/{plant_id}", response_model=list[IndustrialLineResponse])
async def list_lines_by_plant(
    plant_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-lines")),
):
    return await crud.get_by_plant(db, plant_id)


@router.get("/{line_id}/stages", response_model=list[IndustrialStageResponse])
async def list_line_stages(
    line_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-stages")),
):
    record = await crud.get_by_id(db, line_id)
    if not record:
        record = await db["industrial_lines"].find_one(
            {"name": {"$regex": f"^{re.escape(line_id)}$", "$options": "i"}}
        )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Line '{line_id}' not found",
        )
    return await stage_crud.get_by_line(db, str(record.get("id") or line_id))


@router.get("/{line_id}/workstations", response_model=list[WorkstationResponse])
async def list_line_workstations(
    line_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await crud.get_by_id(db, line_id)
    if not record:
        record = await db["industrial_lines"].find_one(
            {"name": {"$regex": f"^{re.escape(line_id)}$", "$options": "i"}}
        )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Line '{line_id}' not found",
        )
    return await workstation_crud.get_by_line(db, str(record.get("id") or line_id))


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{line_id}", response_model=IndustrialLineResponse)
async def get_line(
    line_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-lines")),
):
    record = await crud.get_by_id(db, line_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Line '{line_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=IndustrialLineResponse, status_code=status.HTTP_201_CREATED
)
async def create_line(
    data: IndustrialLineCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-lines")),
):
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Line '{data.id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{line_id}", response_model=IndustrialLineResponse)
async def update_line(
    line_id: str,
    data: IndustrialLineUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-lines")),
):
    updated = await crud.update(db, line_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Line '{line_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{line_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_line(
    line_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-lines")),
):
    if not await crud.delete(db, line_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Line '{line_id}' not found",
        )
