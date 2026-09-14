from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.shipping.helpers import shipping_provider_details as crud
from services.common.auth import require_permission
from services.shipping.models.shipping_provider_details import (
    PaginatedShippingProviderDetailsResponse,
    ShippingProviderDetailsCreate,
    ShippingProviderDetailsResponse,
    ShippingProviderDetailsUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedShippingProviderDetailsResponse)
async def list_shipping_providers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedShippingProviderDetailsResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get(
    "/by-name/{provider_name}", response_model=list[ShippingProviderDetailsResponse]
)
async def list_shipping_providers_by_name(
    provider_name: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_name(db, provider_name)


@router.get("/{provider_id}", response_model=ShippingProviderDetailsResponse)
async def get_shipping_provider(
    provider_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, provider_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Shipping provider '{provider_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=ShippingProviderDetailsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_shipping_provider(
    data: ShippingProviderDetailsCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if await crud.get_by_id(db, data.provider_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Shipping provider '{data.provider_id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{provider_id}", response_model=ShippingProviderDetailsResponse)
async def update_shipping_provider(
    provider_id: str,
    data: ShippingProviderDetailsUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update(db, provider_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Shipping provider '{provider_id}' not found",
        )
    return updated


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shipping_provider(
    provider_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, provider_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Shipping provider '{provider_id}' not found",
        )
