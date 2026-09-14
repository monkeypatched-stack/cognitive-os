from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.db import get_database
from services.common.auth import require_permission
from services.assets.models.material import (
    MaterialUsedCreate,
    MaterialUsedUpdate,
    MaterialUsedResponse,
    PaginatedMaterialUsedResponse,
)
from . import materials as crud

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedMaterialUsedResponse)
async def list_materials(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-materials")),
):
    materials, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedMaterialUsedResponse(
        total=total, page=page, page_size=page_size, results=materials
    )


# ── Get by name ───────────────────────────────────────────────────────────────


@router.get("/by-name/{name}", response_model=list[MaterialUsedResponse])
async def list_materials_by_name(
    name: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-materials")),
):
    return await crud.get_by_name(db, name)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{material_id}", response_model=MaterialUsedResponse)
async def get_material(
    material_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-materials")),
):
    record = await crud.get_by_id(db, material_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Material '{material_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=MaterialUsedResponse, status_code=status.HTTP_201_CREATED
)
async def create_material(
    data: MaterialUsedCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-materials")),
):
    if await crud.get_by_id(db, data.material_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Material '{data.material_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{material_id}", response_model=MaterialUsedResponse)
async def update_material(
    material_id: str,
    data: MaterialUsedUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-materials")),
):
    updated = await crud.update(db, material_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Material '{material_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material(
    material_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-materials")),
):
    if not await crud.delete(db, material_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Material '{material_id}' not found",
        )
