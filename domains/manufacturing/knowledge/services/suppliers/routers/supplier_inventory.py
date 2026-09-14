from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.suppliers.helpers import supplier_inventory as crud
from services.common.auth import require_permission
from services.suppliers.models.supplier_inventory import (
    PaginatedSupplierInventoryResponse,
    SupplierInventoryCreate,
    SupplierInventoryResponse,
    SupplierInventoryUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedSupplierInventoryResponse)
async def list_supplier_inventory(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedSupplierInventoryResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get(
    "/by-supplier/{supplier_id}", response_model=list[SupplierInventoryResponse]
)
async def list_supplier_inventory_by_supplier(
    supplier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_supplier(db, supplier_id)


@router.get("/by-item/{item_id}", response_model=list[SupplierInventoryResponse])
async def list_supplier_inventory_by_item(
    item_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_item(db, item_id)


@router.get(
    "/by-location/{location_id}", response_model=list[SupplierInventoryResponse]
)
async def list_supplier_inventory_by_location(
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_location(db, location_id)


@router.get("/by-sku/{sku}", response_model=list[SupplierInventoryResponse])
async def list_supplier_inventory_by_sku(
    sku: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_sku(db, sku)


@router.get("/{supplier_inventory_id}", response_model=SupplierInventoryResponse)
async def get_supplier_inventory(
    supplier_inventory_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, supplier_inventory_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Supplier inventory '{supplier_inventory_id}' not found",
        )
    return record


@router.post(
    "/", response_model=SupplierInventoryResponse, status_code=status.HTTP_201_CREATED
)
async def create_supplier_inventory(
    data: SupplierInventoryCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    return await crud.create(db, data)


@router.patch("/{supplier_inventory_id}", response_model=SupplierInventoryResponse)
async def update_supplier_inventory(
    supplier_inventory_id: str,
    data: SupplierInventoryUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update(db, supplier_inventory_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Supplier inventory '{supplier_inventory_id}' not found",
        )
    return updated


@router.delete("/{supplier_inventory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier_inventory(
    supplier_inventory_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, supplier_inventory_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Supplier inventory '{supplier_inventory_id}' not found",
        )
