from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.products.helpers import product_inventory as crud

from services.inventory.models.inventory_responses import InventorySummary
from services.products.models.product_inventory import (
    ProductInventoryCreate,
    ProductInventoryUpdate,
    ProductInventoryRecord,
    PaginatedProductInventoryResponse,
)

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedProductInventoryResponse)
async def list_inventory(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedProductInventoryResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


# ── Filtered Queries ──────────────────────────────────────────────────────────


@router.get("/alerts/below-reorder", response_model=list[ProductInventoryRecord])
async def list_below_reorder(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_below_reorder(db)


@router.get("/alerts/overstocked", response_model=list[ProductInventoryRecord])
async def list_overstocked(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_overstocked(db)


@router.get("/alerts/expired", response_model=list[ProductInventoryRecord])
async def list_expired(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_expired(db)


@router.get("/by-status/{status_value}", response_model=list[ProductInventoryRecord])
async def list_by_status(
    status_value: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_status(db, status_value)


@router.get("/by-product/{product_id}", response_model=list[ProductInventoryRecord])
async def list_by_product(
    product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_product(db, product_id)


@router.get("/by-location/{location_id}", response_model=list[ProductInventoryRecord])
async def list_by_location(
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_location(db, location_id)


@router.get(
    "/by-product/{product_id}/location/{location_id}",
    response_model=ProductInventoryRecord,
)
async def get_by_product_and_location(
    product_id: str,
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_product_and_location(db, product_id, location_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No inventory record for product '{product_id}' at location '{location_id}'",
        )
    return record


@router.get("/summary/{product_id}", response_model=InventorySummary)
async def get_inventory_summary(
    product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    summary = await crud.get_inventory_summary(db, product_id)
    if not summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No inventory summary for product '{product_id}'",
        )
    return summary


# ── Get One ───────────────────────────────────────────────────────────────────


@router.get("/{inventory_id}", response_model=ProductInventoryRecord)
async def get_inventory(
    inventory_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_id(db, inventory_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory record '{inventory_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=ProductInventoryRecord, status_code=status.HTTP_201_CREATED
)
async def create_inventory(
    data: ProductInventoryCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    existing = await crud.get_by_product_and_location(
        db, data.product_id, data.location_id
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Inventory record for product '{data.product_id}' at location '{data.location_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{inventory_id}", response_model=ProductInventoryRecord)
async def update_inventory(
    inventory_id: str,
    data: ProductInventoryUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    updated = await crud.update(db, inventory_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory record '{inventory_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{inventory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inventory(
    inventory_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, inventory_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory record '{inventory_id}' not found",
        )
