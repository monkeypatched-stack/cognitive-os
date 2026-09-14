from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.changeover.helpers import changeover as crud
from services.changeover.models.changeover import (
    ChangeoverMatrixEntryCreate,
    ChangeoverMatrixEntryResponse,
    ChangeoverMatrixEntryUpdate,
    PaginatedChangeoverMatrixEntryResponse,
)

router = APIRouter()


@router.get("", response_model=PaginatedChangeoverMatrixEntryResponse)
async def list_matrix_entries(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    results, total = await crud.get_all_matrix(db, page=page, page_size=page_size)
    return PaginatedChangeoverMatrixEntryResponse(
        total=total, page=page, page_size=page_size, results=results
    )


@router.get(
    "/by-workstation/{workstation_id}",
    response_model=list[ChangeoverMatrixEntryResponse],
)
async def list_matrix_entries_by_workstation(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    return await crud.get_matrix_by_workstation(db, workstation_id)


@router.get("/{entry_id}", response_model=ChangeoverMatrixEntryResponse)
async def get_matrix_entry(
    entry_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    record = await crud.get_matrix_by_id(db, entry_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Changeover matrix entry '{entry_id}' not found",
        )
    return record


@router.post(
    "",
    response_model=ChangeoverMatrixEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_matrix_entry(
    data: ChangeoverMatrixEntryCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-changeovers")),
):
    if await crud.get_matrix_by_id(db, str(data.id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Changeover matrix entry '{data.id}' already exists",
        )
    return await crud.create_matrix(db, data)


@router.patch("/{entry_id}", response_model=ChangeoverMatrixEntryResponse)
async def update_matrix_entry(
    entry_id: str,
    data: ChangeoverMatrixEntryUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-changeovers")),
):
    updated = await crud.update_matrix(db, entry_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Changeover matrix entry '{entry_id}' not found",
        )
    return updated


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_matrix_entry(
    entry_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-changeovers")),
):
    if not await crud.delete_matrix(db, entry_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Changeover matrix entry '{entry_id}' not found",
        )
