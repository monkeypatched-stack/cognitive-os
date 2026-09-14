from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.auth.models.users import UserEntryResponse, PaginatedUserEntryResponse
from services.common.db import get_database
from services.common.auth import require_permission
from services.auth.helpers.users import UserEntryCreate, UserEntryUpdate
from services.auth.helpers import users as crud

router = APIRouter()


def get_db() -> AsyncIOMotorDatabase:
    return get_database()


@router.get("/", response_model=PaginatedUserEntryResponse)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-users")),
):
    users, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedUserEntryResponse(
        total=total, page=page, page_size=page_size, results=users
    )


@router.get("/by-employee/{employee_id}", response_model=UserEntryResponse)
async def get_user_by_employee_id(
    employee_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-users")),
):
    record = await crud.get_by_employee_id(db, employee_id)
    if not record:
        raise HTTPException(
            404, detail=f"User with employee_id '{employee_id}' not found"
        )
    return record


@router.get("/by-department/{department}", response_model=list[UserEntryResponse])
async def get_users_by_department(
    department: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-users")),
):
    return await crud.get_by_department(db, department)


@router.get("/by-team/{team}", response_model=list[UserEntryResponse])
async def get_users_by_team(
    team: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-users")),
):
    return await crud.get_by_team(db, team)


@router.get("/{user_id}", response_model=UserEntryResponse)
async def get_user(
    user_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-users")),
):
    record = await crud.get_by_id(db, user_id)
    if not record:
        raise HTTPException(404, detail=f"User '{user_id}' not found")
    return record


@router.post("/", response_model=UserEntryResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserEntryCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-create-user")),
):
    if await crud.get_by_id(db, data.user_id):
        raise HTTPException(409, detail=f"User '{data.user_id}' already exists")
    try:
        return await crud.create(db, data)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.patch("/{user_id}", response_model=UserEntryResponse)
async def update_user(
    user_id: str,
    data: UserEntryUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-update-user")),
):
    try:
        updated = await crud.update(db, user_id, data)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if not updated:
        raise HTTPException(404, detail=f"User '{user_id}' not found")
    return updated


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-delete-user")),
):
    if not await crud.delete(db, user_id):
        raise HTTPException(404, detail=f"User '{user_id}' not found")


@router.get("/by-email/{email}", response_model=UserEntryResponse)
async def get_user_by_email(
    email: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-users")),
):
    record = await crud.get_by_email(db, email)
    if not record:
        raise HTTPException(404, detail=f"User with email '{email}' not found")
    return record


@router.get("/by-username/{username}", response_model=UserEntryResponse)
async def get_user_by_username(
    username: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-users")),
):
    record = await crud.get_by_username(db, username)
    if not record:
        raise HTTPException(404, detail=f"User with username '{username}' not found")
    return record
