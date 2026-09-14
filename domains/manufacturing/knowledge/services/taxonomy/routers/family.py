from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.taxonomy.models.family import (
    FamilyCreate,
    FamilyUpdate,
    FamilyResponse,
    PaginatedFamilyResponse,
)
from services.taxonomy.helpers import family as crud
from services.common.auth import require_permission

router = APIRouter()


def get_db() -> AsyncIOMotorDatabase:
    return get_database()


@router.get("/", response_model=PaginatedFamilyResponse)
async def list_families(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-families")),
):
    families, total = await crud.get_all(db, page=page, page_size=page_size, tag=tag)
    return PaginatedFamilyResponse(
        total=total, page=page, page_size=page_size, results=families
    )


@router.get("/{family_id}", response_model=FamilyResponse)
async def get_family(
    family_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-families")),
):
    family = await crud.get_by_id(db, family_id)
    if not family:
        raise HTTPException(404, detail=f"Family '{family_id}' not found")
    return family


@router.post("/", response_model=FamilyResponse, status_code=status.HTTP_201_CREATED)
async def create_family(
    data: FamilyCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-create-families")),
):
    if await crud.get_by_id(db, data.id):
        raise HTTPException(409, detail=f"Family '{data.id}' already exists")
    return await crud.create(db, data)


@router.patch("/{family_id}", response_model=FamilyResponse)
async def update_family(
    family_id: str,
    data: FamilyUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-update-families")),
):
    updated = await crud.update(db, family_id, data)
    if not updated:
        raise HTTPException(404, detail=f"Family '{family_id}' not found")
    return updated


@router.delete("/{family_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_family(
    family_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-delete-families")),
):
    if not await crud.delete(db, family_id):
        raise HTTPException(404, detail=f"Family '{family_id}' not found")


# TODO_ENDPOINT: GET /api/v1/families/by-equipment/{equipment_id} — get family by equipment ID
