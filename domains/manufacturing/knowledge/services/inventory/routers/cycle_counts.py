from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.inventory.helpers import cycle_counts as crud
from services.inventory.models.cycle_counts import (
    CycleCountCreate,
    CycleCountResponse,
    CycleCountStatus,
    CycleCountUpdate,
    PaginatedCycleCountResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedCycleCountResponse)
async def list_cycle_counts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedCycleCountResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-warehouse/{warehouse_id}", response_model=list[CycleCountResponse])
async def list_cycle_counts_by_warehouse(
    warehouse_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_warehouse_id(db, warehouse_id)


@router.get("/by-status/{cycle_count_status}", response_model=list[CycleCountResponse])
async def list_cycle_counts_by_status(
    cycle_count_status: CycleCountStatus,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_status(db, cycle_count_status.value)


@router.get("/by-sku/{sku}", response_model=list[CycleCountResponse])
async def list_cycle_counts_by_sku(
    sku: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_sku(db, sku)


@router.get("/{cycle_count_id}", response_model=CycleCountResponse)
async def get_cycle_count(
    cycle_count_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_cycle_count_id(db, cycle_count_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cycle count '{cycle_count_id}' not found",
        )
    return record


@router.post(
    "/", response_model=CycleCountResponse, status_code=status.HTTP_201_CREATED
)
async def create_cycle_count(
    data: CycleCountCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    return await crud.create(db, data)


@router.patch("/{cycle_count_id}", response_model=CycleCountResponse)
async def update_cycle_count(
    cycle_count_id: str,
    data: CycleCountUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    updated = await crud.update(db, cycle_count_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cycle count '{cycle_count_id}' not found",
        )
    return updated


@router.delete("/{cycle_count_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cycle_count(
    cycle_count_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, cycle_count_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cycle count '{cycle_count_id}' not found",
        )
