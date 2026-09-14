from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.products.helpers import products as crud

from services.products.models.products import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    PaginatedProductResponse,
)

router = APIRouter()

# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedProductResponse)
async def list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedProductResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


# ── Filtered Queries ──────────────────────────────────────────────────────────


@router.get("/by-sku/{sku}", response_model=ProductResponse)
async def get_product_by_sku(
    sku: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_sku(db, sku)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with SKU '{sku}' not found",
        )
    return record


@router.get("/by-status/{status_value}", response_model=list[ProductResponse])
async def list_products_by_status(
    status_value: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_status(db, status_value)


@router.get("/by-type/{product_type}", response_model=list[ProductResponse])
async def list_products_by_type(
    product_type: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_type(db, product_type)


@router.get("/by-category/{category}", response_model=list[ProductResponse])
async def list_products_by_category(
    category: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_category(db, category)


@router.get("/by-supplier/{supplier_id}", response_model=list[ProductResponse])
async def list_products_by_supplier(
    supplier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_supplier(db, supplier_id)


@router.get("/by-brand/{brand}", response_model=list[ProductResponse])
async def list_products_by_brand(
    brand: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_brand(db, brand)


# ── Get One ───────────────────────────────────────────────────────────────────


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, product_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product '{product_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    data: ProductCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if await crud.get_by_id(db, data.product_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Product '{data.product_id}' already exists",
        )
    if await crud.get_by_sku(db, data.sku):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Product with SKU '{data.sku}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: str,
    data: ProductUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update(db, product_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product '{product_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, product_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product '{product_id}' not found",
        )
