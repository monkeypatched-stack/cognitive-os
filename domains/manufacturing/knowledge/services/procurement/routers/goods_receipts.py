from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.procurement.helpers import goods_receipts as crud
from services.procurement.models.goods_receipts import (
    GoodsReceiptCreate,
    GoodsReceiptResponse,
    GoodsReceiptStatus,
    GoodsReceiptUpdate,
    PaginatedGoodsReceiptResponse,
    PaginatedQuarantineHoldResponse,
    QuarantineHoldCreate,
    QuarantineHoldResponse,
    QuarantineHoldUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedGoodsReceiptResponse)
async def list_goods_receipts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all_goods_receipts(
        db, page=page, page_size=page_size
    )
    return PaginatedGoodsReceiptResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get("/by-po/{po_number}", response_model=list[GoodsReceiptResponse])
async def list_goods_receipts_by_po(
    po_number: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_goods_receipts_by_po_number(db, po_number)


@router.get("/by-status/{receipt_status}", response_model=list[GoodsReceiptResponse])
async def list_goods_receipts_by_status(
    receipt_status: GoodsReceiptStatus,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_goods_receipts_by_status(db, receipt_status.value)


@router.post(
    "/", response_model=GoodsReceiptResponse, status_code=status.HTTP_201_CREATED
)
async def create_goods_receipt(
    data: GoodsReceiptCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if await crud.get_goods_receipt_by_number(db, data.gr_number):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Goods receipt '{data.gr_number}' already exists",
        )
    return await crud.create_goods_receipt(db, data)


@router.get("/quarantine-holds/", response_model=PaginatedQuarantineHoldResponse)
async def list_quarantine_holds(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all_quarantine_holds(
        db, page=page, page_size=page_size
    )
    return PaginatedQuarantineHoldResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get("/quarantine-holds/active", response_model=list[QuarantineHoldResponse])
async def list_active_quarantine_holds(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_active_quarantine_holds(db)


@router.get(
    "/quarantine-holds/by-gr/{gr_id}", response_model=list[QuarantineHoldResponse]
)
async def list_quarantine_holds_by_gr(
    gr_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_quarantine_holds_by_gr_id(db, gr_id)


@router.get("/quarantine-holds/{hold_id}", response_model=QuarantineHoldResponse)
async def get_quarantine_hold(
    hold_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_quarantine_hold_by_id(db, hold_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quarantine hold '{hold_id}' not found",
        )
    return record


@router.post(
    "/quarantine-holds/",
    response_model=QuarantineHoldResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_quarantine_hold(
    data: QuarantineHoldCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    return await crud.create_quarantine_hold(db, data)


@router.patch("/quarantine-holds/{hold_id}", response_model=QuarantineHoldResponse)
async def update_quarantine_hold(
    hold_id: str,
    data: QuarantineHoldUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update_quarantine_hold(db, hold_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quarantine hold '{hold_id}' not found",
        )
    return updated


@router.delete("/quarantine-holds/{hold_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quarantine_hold(
    hold_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete_quarantine_hold(db, hold_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quarantine hold '{hold_id}' not found",
        )


@router.get("/{gr_id}", response_model=GoodsReceiptResponse)
async def get_goods_receipt(
    gr_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_goods_receipt_by_id(db, gr_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Goods receipt '{gr_id}' not found",
        )
    return record


@router.patch("/{gr_id}", response_model=GoodsReceiptResponse)
async def update_goods_receipt(
    gr_id: str,
    data: GoodsReceiptUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update_goods_receipt(db, gr_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Goods receipt '{gr_id}' not found",
        )
    return updated


@router.delete("/{gr_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goods_receipt(
    gr_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete_goods_receipt(db, gr_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Goods receipt '{gr_id}' not found",
        )
