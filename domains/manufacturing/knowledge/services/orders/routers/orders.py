from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.orders.helpers import orders as crud
from services.orders.models.orders import (
    OrderCreate,
    OrderResponse,
    OrderUpdate,
    PaginatedOrderResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedOrderResponse)
async def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedOrderResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    record = await crud.get_by_order_id(db, order_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found",
        )
    return record


@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    data: OrderCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-order")),
):
    if await crud.get_by_order_id(db, data.details.order_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order '{data.details.order_id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{order_id}", response_model=OrderResponse)
async def update_order(
    order_id: str,
    data: OrderUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-order")),
):
    record = await crud.update(db, order_id, data)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found",
        )
    return record


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_order(
    order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-order")),
):
    if not await crud.delete(db, order_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found",
        )
