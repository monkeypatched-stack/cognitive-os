from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.procurement.helpers import purchase_order_shipping_information as crud
from services.common.auth import require_permission
from services.procurement.models.shipping_information import (
    PaginatedPurchaseOrderShippingInformationResponse,
    PurchaseOrderShippingInformationCreate,
    PurchaseOrderShippingInformationResponse,
    PurchaseOrderShippingInformationUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedPurchaseOrderShippingInformationResponse)
async def list_purchase_order_shipping_information(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedPurchaseOrderShippingInformationResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get(
    "/by-po/{po_number}", response_model=list[PurchaseOrderShippingInformationResponse]
)
async def list_purchase_order_shipping_information_by_po(
    po_number: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_po_number(db, po_number)


@router.get("/{shipping_id}", response_model=PurchaseOrderShippingInformationResponse)
async def get_purchase_order_shipping_information(
    shipping_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_shipping_id(db, shipping_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Purchase order shipping information '{shipping_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=PurchaseOrderShippingInformationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_purchase_order_shipping_information(
    data: PurchaseOrderShippingInformationCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if await crud.get_by_shipping_id(db, data.shipping_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Purchase order shipping information '{data.shipping_id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{shipping_id}", response_model=PurchaseOrderShippingInformationResponse)
async def update_purchase_order_shipping_information(
    shipping_id: str,
    data: PurchaseOrderShippingInformationUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update(db, shipping_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Purchase order shipping information '{shipping_id}' not found",
        )
    return updated


@router.delete("/{shipping_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_purchase_order_shipping_information(
    shipping_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, shipping_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Purchase order shipping information '{shipping_id}' not found",
        )
