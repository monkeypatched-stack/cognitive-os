from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.inventory.helpers import inventory_items as crud
from services.inventory.models.inventory_items import (
    InventoryItemCreate,
    InventoryItemResponse,
    InventoryItemUpdate,
    PaginatedInventoryResponse,
)

router = APIRouter()


@router.get("/")
async def list_inventory_items(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return {"total": total, "page": page, "page_size": page_size, "results": records}


@router.get("/low-stock", response_model=list[InventoryItemResponse])
async def list_low_stock_inventory_items(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_low_stock(db)


@router.get("/by-warehouse/{warehouse_id}", response_model=list[InventoryItemResponse])
async def list_inventory_items_by_warehouse(
    warehouse_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_warehouse_id(db, warehouse_id)


@router.get("/by-supplier/{supplier_id}", response_model=list[InventoryItemResponse])
async def list_inventory_items_by_supplier(
    supplier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_supplier_id(db, supplier_id)


@router.get("/{sku}", response_model=InventoryItemResponse)
async def get_inventory_item(
    sku: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_sku(db, sku)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory item '{sku}' not found",
        )
    return record


@router.post(
    "/", response_model=InventoryItemResponse, status_code=status.HTTP_201_CREATED
)
async def create_inventory_item(
    data: InventoryItemCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    if await crud.get_by_sku(db, data.sku):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Inventory item '{data.sku}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{sku}", response_model=InventoryItemResponse)
async def update_inventory_item(
    sku: str,
    data: InventoryItemUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    updated = await crud.update(db, sku, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory item '{sku}' not found",
        )
    return updated


@router.delete("/{sku}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inventory_item(
    sku: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, sku):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory item '{sku}' not found",
        )
