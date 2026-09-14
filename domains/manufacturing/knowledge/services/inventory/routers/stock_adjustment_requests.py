from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.inventory.helpers import stock_adjustment_requests as crud
from services.common.auth import require_permission
from services.inventory.models.stock_adjustment_requests import (
    PaginatedStockAdjustmentRequestResponse,
    StockAdjustmentRequestCreate,
    StockAdjustmentRequestResponse,
    StockAdjustmentRequestUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedStockAdjustmentRequestResponse)
async def list_stock_adjustment_requests(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedStockAdjustmentRequestResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get(
    "/by-inventory/{inventory_id}", response_model=list[StockAdjustmentRequestResponse]
)
async def list_stock_adjustment_requests_by_inventory(
    inventory_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_inventory_id(db, inventory_id)


@router.get(
    "/by-product/{product_id}", response_model=list[StockAdjustmentRequestResponse]
)
async def list_stock_adjustment_requests_by_product(
    product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_product_id(db, product_id)


@router.get(
    "/by-location/{location_id}", response_model=list[StockAdjustmentRequestResponse]
)
async def list_stock_adjustment_requests_by_location(
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_location_id(db, location_id)


@router.get(
    "/by-status/{status_value}", response_model=list[StockAdjustmentRequestResponse]
)
async def list_stock_adjustment_requests_by_status(
    status_value: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_status(db, status_value)


@router.get("/{request_id}", response_model=StockAdjustmentRequestResponse)
async def get_stock_adjustment_request(
    request_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_request_id(db, request_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock adjustment request '{request_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=StockAdjustmentRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_stock_adjustment_request(
    data: StockAdjustmentRequestCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    if await crud.get_by_request_id(db, data.request_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Stock adjustment request '{data.request_id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{request_id}", response_model=StockAdjustmentRequestResponse)
async def update_stock_adjustment_request(
    request_id: str,
    data: StockAdjustmentRequestUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    updated = await crud.update(db, request_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock adjustment request '{request_id}' not found",
        )
    return updated


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stock_adjustment_request(
    request_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, request_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock adjustment request '{request_id}' not found",
        )
