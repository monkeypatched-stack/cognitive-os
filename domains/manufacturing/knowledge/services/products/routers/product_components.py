from fastapi import APIRouter, HTTPException, Depends, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.products.helpers import product_component as crud

from services.products.models.product_component import (
    ProductComponentCreate,
    ProductComponentUpdate,
    ProductComponentRecord,
)

router = APIRouter()


# ── List by Product ───────────────────────────────────────────────────────────


@router.get("/by-product/{product_id}", response_model=list[ProductComponentRecord])
async def list_components_by_product(
    product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-components")),
):
    return await crud.get_by_product(db, product_id)


# ── Filtered Queries ──────────────────────────────────────────────────────────


@router.get("/by-type/{component_type}", response_model=list[ProductComponentRecord])
async def list_components_by_type(
    component_type: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-components")),
):
    return await crud.get_by_type(db, component_type)


@router.get("/by-sku/{sku}", response_model=list[ProductComponentRecord])
async def list_components_by_sku(
    sku: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-components")),
):
    return await crud.get_by_sku(db, sku)


@router.get("/substitutable", response_model=list[ProductComponentRecord])
async def list_substitutable_components(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-components")),
):
    return await crud.get_substitutable(db)


# ── Get One ───────────────────────────────────────────────────────────────────


@router.get("/{component_id}", response_model=ProductComponentRecord)
async def get_component(
    component_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-components")),
):
    record = await crud.get_by_id(db, component_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Component '{component_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=ProductComponentRecord, status_code=status.HTTP_201_CREATED
)
async def create_component(
    data: ProductComponentCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-product-components")),
):
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{component_id}", response_model=ProductComponentRecord)
async def update_component(
    component_id: str,
    data: ProductComponentUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-product-components")),
):
    updated = await crud.update(db, component_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Component '{component_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{component_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_component(
    component_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-product-components")),
):
    if not await crud.delete(db, component_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Component '{component_id}' not found",
        )


@router.delete("/by-product/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_components_by_product(
    product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-product-components")),
):
    await crud.delete_by_product(db, product_id)
