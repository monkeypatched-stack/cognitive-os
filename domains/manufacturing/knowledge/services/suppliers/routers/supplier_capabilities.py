from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.suppliers.helpers import supplier_capabilities as crud
from services.common.auth import require_permission
from services.suppliers.models.supplier_capabilities import (
    PaginatedSupplierCapabilitiesResponse,
    SupplierCapabilitiesCreate,
    SupplierCapabilitiesResponse,
    SupplierCapabilitiesUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedSupplierCapabilitiesResponse)
async def list_supplier_capabilities(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedSupplierCapabilitiesResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get("/{supplier_id}", response_model=SupplierCapabilitiesResponse)
async def get_supplier_capabilities(
    supplier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_supplier_id(db, supplier_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Supplier capabilities for supplier '{supplier_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=SupplierCapabilitiesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_supplier_capabilities(
    data: SupplierCapabilitiesCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if await crud.get_by_supplier_id(db, data.supplier_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Supplier capabilities for supplier '{data.supplier_id}' already exist",
        )
    return await crud.create(db, data)


@router.patch("/{supplier_id}", response_model=SupplierCapabilitiesResponse)
async def update_supplier_capabilities(
    supplier_id: str,
    data: SupplierCapabilitiesUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update(db, supplier_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Supplier capabilities for supplier '{supplier_id}' not found",
        )
    return updated


@router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier_capabilities(
    supplier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, supplier_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Supplier capabilities for supplier '{supplier_id}' not found",
        )
