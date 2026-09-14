from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.orders.helpers import sales_orders as crud
from services.orders.models.sales_orders import (
    PaginatedSalesOrderResponse,
    SalesOrderCreate,
    SalesOrderResponse,
    SalesOrderStatus,
    SalesOrderUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedSalesOrderResponse)
async def list_sales_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedSalesOrderResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get("/by-customer/{customer_id}", response_model=list[SalesOrderResponse])
async def list_sales_orders_by_customer(
    customer_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    return await crud.get_by_customer_id(db, customer_id)


@router.get("/by-status/{sales_status}", response_model=list[SalesOrderResponse])
async def list_sales_orders_by_status(
    sales_status: SalesOrderStatus,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    return await crud.get_by_status(db, sales_status.value)


@router.get("/by-sku/{sku}", response_model=list[SalesOrderResponse])
async def list_sales_orders_by_sku(
    sku: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    return await crud.get_by_sku(db, sku)


@router.get("/{sales_order_id}", response_model=SalesOrderResponse)
async def get_sales_order(
    sales_order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    record = await crud.get_by_sales_order_id(db, sales_order_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sales order '{sales_order_id}' not found",
        )
    return record


@router.post(
    "/", response_model=SalesOrderResponse, status_code=status.HTTP_201_CREATED
)
async def create_sales_order(
    data: SalesOrderCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-order")),
):
    return await crud.create(db, data)


@router.patch("/{sales_order_id}", response_model=SalesOrderResponse)
async def update_sales_order(
    sales_order_id: str,
    data: SalesOrderUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-order")),
):
    updated = await crud.update(db, sales_order_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sales order '{sales_order_id}' not found",
        )
    return updated


@router.delete("/{sales_order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sales_order(
    sales_order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-order")),
):
    if not await crud.delete(db, sales_order_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sales order '{sales_order_id}' not found",
        )
