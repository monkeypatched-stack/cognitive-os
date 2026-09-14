from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.customers.helpers import customer_details as crud
from services.common.auth import require_permission
from services.customers.models.customer_details import (
    CustomerDetailsCreate,
    CustomerDetailsResponse,
    CustomerDetailsUpdate,
    PaginatedCustomerDetailsResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedCustomerDetailsResponse)
async def list_customer_details(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedCustomerDetailsResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get("/{customer_id}", response_model=CustomerDetailsResponse)
async def get_customer_details(
    customer_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_customer_id(db, customer_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer details for customer '{customer_id}' not found",
        )
    return record


@router.post(
    "/", response_model=CustomerDetailsResponse, status_code=status.HTTP_201_CREATED
)
async def create_customer_details(
    data: CustomerDetailsCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if data.customer_id and await crud.get_by_customer_id(db, data.customer_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Customer details for customer '{data.customer_id}' already exist",
        )
    return await crud.create(db, data)


@router.patch("/{customer_id}", response_model=CustomerDetailsResponse)
async def update_customer_details(
    customer_id: str,
    data: CustomerDetailsUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update(db, customer_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer details for customer '{customer_id}' not found",
        )
    return updated


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer_details(
    customer_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, customer_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer details for customer '{customer_id}' not found",
        )
