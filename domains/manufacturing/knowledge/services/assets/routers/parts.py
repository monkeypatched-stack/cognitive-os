from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.db import get_database
from services.common.auth import get_current_user, require_permission
from services.assets.models.parts import (
    MachinePartCreate,
    MachinePartUpdate,
    MachinePartResponse,
    PaginatedMachinePartResponse,
)
from services.assets.helpers import parts as crud

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedMachinePartResponse)
async def list_parts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    parts, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedMachinePartResponse(
        total=total, page=page, page_size=page_size, results=parts
    )


# ── Get by machine ────────────────────────────────────────────────────────────


@router.get("/by-machine/{machine_id}", response_model=list[MachinePartResponse])
async def list_parts_by_machine(
    machine_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await crud.get_by_machine(db, machine_id)


@router.get("/by-equipment/{equipment_id}", response_model=list[MachinePartResponse])
async def list_parts_by_equipment(
    equipment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    print(equipment_id)
    return await crud.get_by_equipment(db, equipment_id)


# ── Get low stock ─────────────────────────────────────────────────────────────


@router.get("/low-stock", response_model=list[MachinePartResponse])
async def list_low_stock(
    machine: Optional[str] = Query(
        None, description="Optionally scope to a specific machine"
    ),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await crud.get_low_stock(db, machine=machine)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{part_id}", response_model=MachinePartResponse)
async def get_part(
    part_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await crud.get_by_id(db, part_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MachinePart '{part_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=MachinePartResponse, status_code=status.HTTP_201_CREATED
)
async def create_part(
    data: MachinePartCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-parts")),
):
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MachinePart '{data.id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{part_id}", response_model=MachinePartResponse)
async def update_part(
    part_id: str,
    data: MachinePartUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-parts")),
):
    updated = await crud.update(db, part_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MachinePart '{part_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{part_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_part(
    part_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-parts")),
):
    if not await crud.delete(db, part_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MachinePart '{part_id}' not found",
        )


# TODO_ENDPOINT: GET /api/v1/parts/{part_id}/history — order and usage history for a specific part
