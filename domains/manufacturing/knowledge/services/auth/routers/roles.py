from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.db import get_database
from services.common.auth import require_permission
from services.auth.models.roles import (
    RoleCreate,
    RoleUpdate,
    RoleResponse,
    PaginatedRoleResponse,
)
from services.auth.models.users import UserEntry
from services.auth.helpers import roles as crud
from services.auth.helpers import users as users_crud

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedRoleResponse)
async def list_roles(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-roles")),
):
    roles, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedRoleResponse(
        total=total, page=page, page_size=page_size, results=roles
    )


# ── Get by permission ─────────────────────────────────────────────────────────


@router.get("/by-permission/{permission_id}", response_model=list[RoleResponse])
async def list_roles_by_permission(
    permission_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-roles")),
):
    return await crud.get_by_permission(db, permission_id)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-roles")),
):
    record = await crud.get_by_id(db, role_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{role_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("/", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    data: RoleCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-roles")),
):
    if await crud.get_by_id(db, data.role_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role '{data.role_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: str,
    data: RoleUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-roles")),
):
    updated = await crud.update(db, role_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{role_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-roles")),
):
    if not await crud.delete(db, role_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{role_id}' not found",
        )


# ── Permission management ─────────────────────────────────────────────────────


@router.post("/{role_id}/permissions/{permission_id}", response_model=RoleResponse)
async def add_permission_to_role(
    role_id: str,
    permission_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-roles")),
):
    updated = await crud.add_permission(db, role_id, permission_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{role_id}' not found",
        )
    return updated


@router.delete("/{role_id}/permissions/{permission_id}", response_model=RoleResponse)
async def remove_permission_from_role(
    role_id: str,
    permission_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-roles")),
):
    updated = await crud.remove_permission(db, role_id, permission_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{role_id}' not found",
        )
    return updated


@router.get("/{role_id}/users", response_model=list[UserEntry])
async def list_users_for_role(
    role_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-roles")),
):
    """List all users assigned to a given role."""
    if not await crud.get_by_id(db, role_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{role_id}' not found",
        )
    return await users_crud.get_by_role(db, role_id)
