from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.suppliers.helpers import supplier_locations as crud
from services.common.auth import require_permission
from services.suppliers.models.supplier_locations import (
    PaginatedSupplierLocationResponse,
    SupplierLocationCreate,
    SupplierLocationResponse,
    SupplierLocationUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedSupplierLocationResponse)
async def list_supplier_locations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedSupplierLocationResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-supplier/{supplier_id}", response_model=list[SupplierLocationResponse])
async def list_supplier_locations_by_supplier(
    supplier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_supplier(db, supplier_id)


@router.get("/by-item/{item_id}", response_model=list[SupplierLocationResponse])
async def list_supplier_locations_by_item(
    item_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_item(db, item_id)


@router.get("/{location_id}", response_model=SupplierLocationResponse)
async def get_supplier_location(
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, location_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Supplier location '{location_id}' not found",
        )
    return record


@router.post(
    "/", response_model=SupplierLocationResponse, status_code=status.HTTP_201_CREATED
)
async def create_supplier_location(
    data: SupplierLocationCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if await crud.get_by_id(db, data.location_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Supplier location '{data.location_id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{location_id}", response_model=SupplierLocationResponse)
async def update_supplier_location(
    location_id: str,
    data: SupplierLocationUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update(db, location_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Supplier location '{location_id}' not found",
        )
    return updated


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier_location(
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, location_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Supplier location '{location_id}' not found",
        )
