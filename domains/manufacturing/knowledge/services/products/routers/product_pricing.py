from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.products.helpers import product_pricing as crud

from services.products.models.product_pricing import (
    ProductPricingCreate,
    ProductPricingUpdate,
    ProductPricingRecord,
    PaginatedProductPricingResponse,
)

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/")
async def list_pricing(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-pricing")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return {"total": total, "page": page, "page_size": page_size, "results": records}


# ── Filtered Queries ──────────────────────────────────────────────────────────


@router.get("/by-product/{product_id}", response_model=list[ProductPricingRecord])
async def list_pricing_by_product(
    product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-pricing")),
):
    return await crud.get_by_product(db, product_id)


@router.get(
    "/by-product/{product_id}/active", response_model=list[ProductPricingRecord]
)
async def list_active_pricing_by_product(
    product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-pricing")),
):
    return await crud.get_active_by_product(db, product_id)


@router.get("/by-type/{pricing_type}", response_model=list[ProductPricingRecord])
async def list_pricing_by_type(
    pricing_type: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-pricing")),
):
    return await crud.get_by_pricing_type(db, pricing_type)


@router.get("/by-currency/{currency}", response_model=list[ProductPricingRecord])
async def list_pricing_by_currency(
    currency: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-pricing")),
):
    return await crud.get_by_currency(db, currency)


# ── Get One ───────────────────────────────────────────────────────────────────


@router.get("/{pricing_id}", response_model=ProductPricingRecord)
async def get_pricing(
    pricing_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-pricing")),
):
    record = await crud.get_by_id(db, pricing_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pricing record '{pricing_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=ProductPricingRecord, status_code=status.HTTP_201_CREATED
)
async def create_pricing(
    data: ProductPricingCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-product-pricing")),
):
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{pricing_id}", response_model=ProductPricingRecord)
async def update_pricing(
    pricing_id: str,
    data: ProductPricingUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-product-pricing")),
):
    updated = await crud.update(db, pricing_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pricing record '{pricing_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{pricing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pricing(
    pricing_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-product-pricing")),
):
    if not await crud.delete(db, pricing_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pricing record '{pricing_id}' not found",
        )


@router.delete("/by-product/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pricing_by_product(
    product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-product-pricing")),
):
    await crud.delete_by_product(db, product_id)
