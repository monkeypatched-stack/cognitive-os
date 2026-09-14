from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.taxonomy.helpers.subclass import (
    EquipmentSubClassCreate,
    EquipmentSubClassUpdate,
)
from services.taxonomy.models.subclass import (
    EquipmentSubClassResponse,
    PaginatedEquipmentSubClassResponse,
)
from services.taxonomy.helpers import subclass as crud
from services.common.auth import require_permission

router = APIRouter()


def get_db() -> AsyncIOMotorDatabase:
    return get_database()


@router.get("/", response_model=PaginatedEquipmentSubClassResponse)
async def list_subclasses(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    family_id: Optional[str] = Query(None, description="Filter by family ID"),
    class_id: Optional[str] = Query(None, description="Filter by class ID"),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-subclasses")),
):
    subclasses, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        family_id=family_id,
        class_id=class_id,
        tag=tag,
    )
    return PaginatedEquipmentSubClassResponse(
        total=total, page=page, page_size=page_size, results=subclasses
    )


@router.get("/{subclass_id}", response_model=EquipmentSubClassResponse)
async def get_subclass(
    subclass_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-subclasses")),
):
    record = await crud.get_by_id(db, subclass_id)
    if not record:
        raise HTTPException(404, detail=f"EquipmentSubClass '{subclass_id}' not found")
    return record


@router.post(
    "/", response_model=EquipmentSubClassResponse, status_code=status.HTTP_201_CREATED
)
async def create_subclass(
    data: EquipmentSubClassCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-create-subclasses")),
):
    if await crud.get_by_id(db, data.id):
        raise HTTPException(409, detail=f"EquipmentSubClass '{data.id}' already exists")
    return await crud.create(db, data)


@router.patch("/{subclass_id}", response_model=EquipmentSubClassResponse)
async def update_subclass(
    subclass_id: str,
    data: EquipmentSubClassUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-update-subclasses")),
):
    updated = await crud.update(db, subclass_id, data)
    if not updated:
        raise HTTPException(404, detail=f"EquipmentSubClass '{subclass_id}' not found")
    return updated


@router.delete("/{subclass_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subclass(
    subclass_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-delete-subclasses")),
):
    if not await crud.delete(db, subclass_id):
        raise HTTPException(404, detail=f"EquipmentSubClass '{subclass_id}' not found")


# TODO_ENDPOINT: GET /api/v1/classes/{class_id}/subclasses — list all subclasses in a class
