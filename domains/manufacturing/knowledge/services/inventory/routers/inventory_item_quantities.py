from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.inventory.helpers import inventory_item_quantities as crud
from services.inventory.models.inventory_item_quantity import (
    InventoryItemQuantityCreate,
    InventoryItemQuantityResponse,
    InventoryItemQuantityUpdate,
    PaginatedInventoryItemQuantityResponse,
)

router = APIRouter()


@router.get("/")
async def list_inventory_item_quantities(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return {"total": total, "page": page, "page_size": page_size, "results": records}


@router.get("/by-sku/{sku}", response_model=list[InventoryItemQuantityResponse])
async def list_inventory_item_quantities_by_sku(
    sku: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_sku(db, sku)


@router.get("/by-srn/{srn}", response_model=list[InventoryItemQuantityResponse])
async def list_inventory_item_quantities_by_srn(
    srn: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_srn(db, srn)


@router.get(
    "/by-warehouse/{warehouse_id}", response_model=list[InventoryItemQuantityResponse]
)
async def list_inventory_item_quantities_by_warehouse(
    warehouse_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_warehouse_id(db, warehouse_id)


@router.get("/{record_id}", response_model=InventoryItemQuantityResponse)
async def get_inventory_item_quantity(
    record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_id(db, record_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory item quantity '{record_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=InventoryItemQuantityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_inventory_item_quantity(
    data: InventoryItemQuantityCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    return await crud.create(db, data)


@router.patch("/{record_id}", response_model=InventoryItemQuantityResponse)
async def update_inventory_item_quantity(
    record_id: str,
    data: InventoryItemQuantityUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    updated = await crud.update(db, record_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory item quantity '{record_id}' not found",
        )
    return updated


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inventory_item_quantity(
    record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, record_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory item quantity '{record_id}' not found",
        )
