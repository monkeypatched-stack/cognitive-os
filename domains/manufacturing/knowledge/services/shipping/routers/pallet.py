from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.shipping.helpers import pallet as crud
from services.shipping.models.pallet import (
    PaginatedPalletResponse,
    PalletCreate,
    PalletResponse,
    PalletUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedPalletResponse)
async def list_pallets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedPalletResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-label/{pallet_label}", response_model=PalletResponse)
async def get_pallet_by_label(
    pallet_label: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_label(db, pallet_label)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Pallet with label '{pallet_label}' not found",
        )
    return record


@router.get("/by-condition/{condition}", response_model=list[PalletResponse])
async def list_pallets_by_condition(
    condition: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_condition(db, condition)


@router.get("/by-product/{product_code}", response_model=list[PalletResponse])
async def list_pallets_by_product(
    product_code: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_product_code(db, product_code)


@router.get("/{pallet_id}", response_model=PalletResponse)
async def get_pallet(
    pallet_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, pallet_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Pallet '{pallet_id}' not found"
        )
    return record


@router.post("/", response_model=PalletResponse, status_code=status.HTTP_201_CREATED)
async def create_pallet(
    data: PalletCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    pallet_id = str(data.id)
    if await crud.get_by_id(db, pallet_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Pallet '{pallet_id}' already exists"
        )
    if await crud.get_by_label(db, data.pallet_label):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Pallet with label '{data.pallet_label}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{pallet_id}", response_model=PalletResponse)
async def update_pallet(
    pallet_id: str,
    data: PalletUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    if data.pallet_label:
        existing = await crud.get_by_label(db, data.pallet_label)
        if existing and existing.get("id") != pallet_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Pallet with label '{data.pallet_label}' already exists",
            )
    updated = await crud.update(db, pallet_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Pallet '{pallet_id}' not found"
        )
    return updated


@router.delete("/{pallet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pallet(
    pallet_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, pallet_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Pallet '{pallet_id}' not found"
        )
