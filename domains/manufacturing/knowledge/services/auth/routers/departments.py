from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.auth.models.departments import (
    DepartmentCreate,
    DepartmentUpdate,
    DepartmentResponse,
    PaginatedDepartmentResponse,
)
from services.auth.helpers import departments as crud

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedDepartmentResponse)
async def list_departments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-departments")),
):
    departments, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedDepartmentResponse(
        total=total, page=page, page_size=page_size, results=departments
    )


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{department_id}", response_model=DepartmentResponse)
async def get_department(
    department_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-departments")),
):
    record = await crud.get_by_id(db, department_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Department '{department_id}' not found"
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED
)
async def create_department(
    data: DepartmentCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-department")),
):
    if await crud.get_by_id(db, data.department_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Department '{data.department_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{department_id}", response_model=DepartmentResponse)
async def update_department(
    department_id: str,
    data: DepartmentUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-department")),
):
    updated = await crud.update(db, department_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Department '{department_id}' not found"
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_department(
    department_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-department")),
):
    if not await crud.delete(db, department_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Department '{department_id}' not found"
        )


# TODO_ENDPOINT: GET /api/v1/departments/{department_id}/teams — list all teams in a department
# TODO_ENDPOINT: GET /api/v1/departments/{department_id}/users — list all users in a department
