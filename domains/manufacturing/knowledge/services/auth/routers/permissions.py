from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.db import get_database
from services.common.auth import require_permission
from services.auth.models.permissions import (
    PermissionCreate,
    PermissionUpdate,
    PermissionResponse,
    PaginatedPermissionResponse,
)
from services.auth.helpers import permissions as crud

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedPermissionResponse)
async def list_permissions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-permissions")),
):
    permissions, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedPermissionResponse(
        total=total, page=page, page_size=page_size, results=permissions
    )


# ── Get by resource ───────────────────────────────────────────────────────────


@router.get("/by-resource/{resource}", response_model=list[PermissionResponse])
async def list_permissions_by_resource(
    resource: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-permissions")),
):
    return await crud.get_by_resource(db, resource)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{permission_id}", response_model=PermissionResponse)
async def get_permission(
    permission_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-permissions")),
):
    record = await crud.get_by_id(db, permission_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Permission '{permission_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=PermissionResponse, status_code=status.HTTP_201_CREATED
)
async def create_permission(
    data: PermissionCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-manage-permissions")),
):
    if await crud.get_by_id(db, data.permission_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Permission '{data.permission_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{permission_id}", response_model=PermissionResponse)
async def update_permission(
    permission_id: str,
    data: PermissionUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-manage-permissions")),
):
    updated = await crud.update(db, permission_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Permission '{permission_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_permission(
    permission_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-manage-permissions")),
):
    if not await crud.delete(db, permission_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Permission '{permission_id}' not found",
        )
