from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.inventory.helpers import inventory_transactions as crud
from services.common.auth import require_permission
from services.inventory.models.inventory_responses import (
    PaginatedInventoryTransactionResponse,
)
from services.inventory.models.inventory_transactions import (
    InventoryTransactionCreate,
    InventoryTransactionRecord,
    InventoryTransactionUpdate,
)

router = APIRouter()


@router.get("/")
async def list_inventory_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return {"total": total, "page": page, "page_size": page_size, "results": records}


@router.get(
    "/by-inventory/{inventory_id}", response_model=list[InventoryTransactionRecord]
)
async def list_inventory_transactions_by_inventory(
    inventory_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_inventory_id(db, inventory_id)


@router.get("/by-product/{product_id}", response_model=list[InventoryTransactionRecord])
async def list_inventory_transactions_by_product(
    product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_product_id(db, product_id)


@router.get("/{transaction_id}", response_model=InventoryTransactionRecord)
async def get_inventory_transaction(
    transaction_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_transaction_id(db, transaction_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory transaction '{transaction_id}' not found",
        )
    return record


@router.post(
    "/", response_model=InventoryTransactionRecord, status_code=status.HTTP_201_CREATED
)
async def create_inventory_transaction(
    data: InventoryTransactionCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    return await crud.create(db, data)


@router.patch("/{transaction_id}", response_model=InventoryTransactionRecord)
async def update_inventory_transaction(
    transaction_id: str,
    data: InventoryTransactionUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    updated = await crud.update(db, transaction_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory transaction '{transaction_id}' not found",
        )
    return updated


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inventory_transaction(
    transaction_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, transaction_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory transaction '{transaction_id}' not found",
        )
