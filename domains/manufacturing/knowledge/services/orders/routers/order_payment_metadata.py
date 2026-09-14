from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.orders.helpers import order_payment_metadata as crud
from services.common.auth import require_permission
from services.orders.models.order_payment_metadata import (
    OrderPaymentMetadataCreate,
    OrderPaymentMetadataResponse,
    OrderPaymentMetadataUpdate,
    PaginatedOrderPaymentMetadataResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedOrderPaymentMetadataResponse)
async def list_order_payment_metadata(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedOrderPaymentMetadataResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get("/{order_id}", response_model=OrderPaymentMetadataResponse)
async def get_order_payment_metadata(
    order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    record = await crud.get_by_order_id(db, order_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order payment metadata for order '{order_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=OrderPaymentMetadataResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_order_payment_metadata(
    data: OrderPaymentMetadataCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-order")),
):
    if await crud.get_by_order_id(db, data.order_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order payment metadata for order '{data.order_id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{order_id}", response_model=OrderPaymentMetadataResponse)
async def update_order_payment_metadata(
    order_id: str,
    data: OrderPaymentMetadataUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-order")),
):
    updated = await crud.update(db, order_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order payment metadata for order '{order_id}' not found",
        )
    return updated


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_order_payment_metadata(
    order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-order")),
):
    if not await crud.delete(db, order_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order payment metadata for order '{order_id}' not found",
        )
