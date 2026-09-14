from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.procurement.helpers import purchase_orders as crud
from services.common.auth import require_permission
from services.procurement.models.purchase_orders import (
    PaginatedPurchaseOrderResponse,
    PurchaseOrderCreate,
    PurchaseOrderResponse,
    PurchaseOrderUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedPurchaseOrderResponse)
async def list_purchase_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedPurchaseOrderResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get("/by-supplier/{supplier_id}", response_model=list[PurchaseOrderResponse])
async def list_purchase_orders_by_supplier(
    supplier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_supplier_id(db, supplier_id)


@router.get("/by-buyer/{buyer_id}", response_model=list[PurchaseOrderResponse])
async def list_purchase_orders_by_buyer(
    buyer_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_buyer_id(db, buyer_id)


@router.get("/{po_number}", response_model=PurchaseOrderResponse)
async def get_purchase_order(
    po_number: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_po_number(db, po_number)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Purchase order '{po_number}' not found",
        )
    return record


@router.post(
    "/", response_model=PurchaseOrderResponse, status_code=status.HTTP_201_CREATED
)
async def create_purchase_order(
    data: PurchaseOrderCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if await crud.get_by_po_number(db, data.po_number):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Purchase order '{data.po_number}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{po_number}", response_model=PurchaseOrderResponse)
async def update_purchase_order(
    po_number: str,
    data: PurchaseOrderUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update(db, po_number, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Purchase order '{po_number}' not found",
        )
    return updated


@router.delete("/{po_number}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_purchase_order(
    po_number: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, po_number):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Purchase order '{po_number}' not found",
        )
