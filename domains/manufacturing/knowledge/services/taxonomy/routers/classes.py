from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.taxonomy.models.classes import (
    EquipmentClassResponse,
    PaginatedEquipmentClassResponse,
)
from services.common.db import get_database
from services.taxonomy.helpers.classes import EquipmentClassCreate, EquipmentClassUpdate
from services.taxonomy.helpers import classes as crud
from services.common.auth import require_permission

router = APIRouter()


def get_db() -> AsyncIOMotorDatabase:
    return get_database()


@router.get("/", response_model=PaginatedEquipmentClassResponse)
async def list_classes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-classes")),
):
    classes, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedEquipmentClassResponse(
        total=total, page=page, page_size=page_size, results=classes
    )


@router.get("/{class_id}", response_model=EquipmentClassResponse)
async def get_class(
    class_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-classes")),
):
    record = await crud.get_by_id(db, class_id)
    if not record:
        raise HTTPException(404, detail=f"EquipmentClass '{class_id}' not found")
    return record


@router.post(
    "/", response_model=EquipmentClassResponse, status_code=status.HTTP_201_CREATED
)
async def create_class(
    data: EquipmentClassCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-create-classes")),
):
    if await crud.get_by_id(db, data.id):
        raise HTTPException(409, detail=f"EquipmentClass '{data.id}' already exists")
    return await crud.create(db, data)


@router.patch("/{class_id}", response_model=EquipmentClassResponse)
async def update_class(
    class_id: str,
    data: EquipmentClassUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-update-classes")),
):
    updated = await crud.update(db, class_id, data)
    if not updated:
        raise HTTPException(404, detail=f"EquipmentClass '{class_id}' not found")
    return updated


@router.delete("/{class_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_class(
    class_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-delete-classes")),
):
    if not await crud.delete(db, class_id):
        raise HTTPException(404, detail=f"EquipmentClass '{class_id}' not found")


# TODO_ENDPOINT: GET /api/v1/families/{family_id}/classes — list all classes in a family
