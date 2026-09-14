from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.inventory.helpers import warehouse_cycle_counts as crud
from services.inventory.models.warehouse_cycle_counts import (
    CycleCountStatus,
    PaginatedWarehouseCycleCountResponse,
    WarehouseCycleCountCreate,
    WarehouseCycleCountResponse,
    WarehouseCycleCountUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedWarehouseCycleCountResponse)
async def list_warehouse_cycle_counts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedWarehouseCycleCountResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get(
    "/by-warehouse/{warehouse_id}", response_model=list[WarehouseCycleCountResponse]
)
async def list_warehouse_cycle_counts_by_warehouse(
    warehouse_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_warehouse_id(db, warehouse_id)


@router.get(
    "/by-location/{location_id}", response_model=list[WarehouseCycleCountResponse]
)
async def list_warehouse_cycle_counts_by_location(
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_location_id(db, location_id)


@router.get("/by-sku/{sku}", response_model=list[WarehouseCycleCountResponse])
async def list_warehouse_cycle_counts_by_sku(
    sku: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_sku(db, sku)


@router.get(
    "/by-status/{cycle_status}", response_model=list[WarehouseCycleCountResponse]
)
async def list_warehouse_cycle_counts_by_status(
    cycle_status: CycleCountStatus,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_status(db, cycle_status.value)


@router.get("/{cycle_count_id}", response_model=WarehouseCycleCountResponse)
async def get_warehouse_cycle_count(
    cycle_count_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_cycle_count_id(db, cycle_count_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Warehouse cycle count '{cycle_count_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=WarehouseCycleCountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_warehouse_cycle_count(
    data: WarehouseCycleCountCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    return await crud.create(db, data)


@router.patch("/{cycle_count_id}", response_model=WarehouseCycleCountResponse)
async def update_warehouse_cycle_count(
    cycle_count_id: str,
    data: WarehouseCycleCountUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    updated = await crud.update(db, cycle_count_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Warehouse cycle count '{cycle_count_id}' not found",
        )
    return updated


@router.delete("/{cycle_count_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_warehouse_cycle_count(
    cycle_count_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, cycle_count_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Warehouse cycle count '{cycle_count_id}' not found",
        )
