from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.suppliers.helpers import supplier_quality as crud
from services.common.auth import require_permission
from services.suppliers.models.supplier_quality import (
    PaginatedSupplierQualityResponse,
    SupplierQualityCreate,
    SupplierQualityResponse,
    SupplierQualityUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedSupplierQualityResponse)
async def list_supplier_quality(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedSupplierQualityResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-supplier/{supplier_id}", response_model=list[SupplierQualityResponse])
async def list_supplier_quality_by_supplier(
    supplier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_supplier(db, supplier_id)


@router.get("/by-item/{item_id}", response_model=list[SupplierQualityResponse])
async def list_supplier_quality_by_item(
    item_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_item(db, item_id)


@router.get("/by-location/{location_id}", response_model=list[SupplierQualityResponse])
async def list_supplier_quality_by_location(
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_location(db, location_id)


@router.get("/{supplier_quality_id}", response_model=SupplierQualityResponse)
async def get_supplier_quality(
    supplier_quality_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, supplier_quality_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Supplier quality '{supplier_quality_id}' not found",
        )
    return record


@router.post(
    "/", response_model=SupplierQualityResponse, status_code=status.HTTP_201_CREATED
)
async def create_supplier_quality(
    data: SupplierQualityCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    return await crud.create(db, data)


@router.patch("/{supplier_quality_id}", response_model=SupplierQualityResponse)
async def update_supplier_quality(
    supplier_quality_id: str,
    data: SupplierQualityUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update(db, supplier_quality_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Supplier quality '{supplier_quality_id}' not found",
        )
    return updated


@router.delete("/{supplier_quality_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier_quality(
    supplier_quality_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, supplier_quality_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Supplier quality '{supplier_quality_id}' not found",
        )
