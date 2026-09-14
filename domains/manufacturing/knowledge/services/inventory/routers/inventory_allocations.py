from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.inventory.helpers import inventory_allocations as crud
from services.inventory.models.inventory_allocations import (
    AllocationRequestType,
    AllocationStatus,
    InventoryAllocationCreate,
    InventoryAllocationResponse,
    InventoryAllocationUpdate,
    PaginatedInventoryAllocationResponse,
)

router = APIRouter()


@router.get("/")
async def list_inventory_allocations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return {"total": total, "page": page, "page_size": page_size, "results": records}


@router.get(
    "/by-workstation/{workstation_id}", response_model=list[InventoryAllocationResponse]
)
async def list_inventory_allocations_by_workstation(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_workstation_id(db, workstation_id)


@router.get(
    "/by-warehouse/{warehouse_id}", response_model=list[InventoryAllocationResponse]
)
async def list_inventory_allocations_by_warehouse(
    warehouse_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_warehouse_id(db, warehouse_id)


@router.get("/by-sku/{sku}", response_model=list[InventoryAllocationResponse])
async def list_inventory_allocations_by_sku(
    sku: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_sku(db, sku)


@router.get(
    "/by-status/{allocation_status}", response_model=list[InventoryAllocationResponse]
)
async def list_inventory_allocations_by_status(
    allocation_status: AllocationStatus,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_status(db, allocation_status.value)


@router.get(
    "/by-request-type/{request_type}", response_model=list[InventoryAllocationResponse]
)
async def list_inventory_allocations_by_request_type(
    request_type: AllocationRequestType,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_request_type(db, request_type.value)


@router.get("/{allocation_id}", response_model=InventoryAllocationResponse)
async def get_inventory_allocation(
    allocation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_allocation_id(db, allocation_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory allocation '{allocation_id}' not found",
        )
    return record


@router.post(
    "/", response_model=InventoryAllocationResponse, status_code=status.HTTP_201_CREATED
)
async def create_inventory_allocation(
    data: InventoryAllocationCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    return await crud.create(db, data)


@router.patch("/{allocation_id}", response_model=InventoryAllocationResponse)
async def update_inventory_allocation(
    allocation_id: str,
    data: InventoryAllocationUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    updated = await crud.update(db, allocation_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory allocation '{allocation_id}' not found",
        )
    return updated


@router.delete("/{allocation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inventory_allocation(
    allocation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, allocation_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory allocation '{allocation_id}' not found",
        )
