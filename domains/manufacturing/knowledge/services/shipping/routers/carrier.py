from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.shipping.helpers import carrier as crud
from services.shipping.models.carrier import (
    CarrierCreate,
    CarrierResponse,
    CarrierUpdate,
    PaginatedCarrierResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedCarrierResponse)
async def list_carriers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedCarrierResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-code/{code}", response_model=CarrierResponse)
async def get_carrier_by_code(
    code: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_code(db, code)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Carrier with code '{code}' not found"
        )
    return record


@router.get("/by-name/{name}", response_model=list[CarrierResponse])
async def list_carriers_by_name(
    name: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_name(db, name)


@router.get("/{carrier_id}", response_model=CarrierResponse)
async def get_carrier(
    carrier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, carrier_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Carrier '{carrier_id}' not found"
        )
    return record


@router.post("/", response_model=CarrierResponse, status_code=status.HTTP_201_CREATED)
async def create_carrier(
    data: CarrierCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    carrier_id = str(data.id)
    if await crud.get_by_id(db, carrier_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Carrier '{carrier_id}' already exists"
        )
    if await crud.get_by_code(db, data.code):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Carrier with code '{data.code}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{carrier_id}", response_model=CarrierResponse)
async def update_carrier(
    carrier_id: str,
    data: CarrierUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    if data.code:
        existing = await crud.get_by_code(db, data.code)
        if existing and existing.get("id") != carrier_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Carrier with code '{data.code}' already exists",
            )
    updated = await crud.update(db, carrier_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Carrier '{carrier_id}' not found"
        )
    return updated


@router.delete("/{carrier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_carrier(
    carrier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, carrier_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Carrier '{carrier_id}' not found"
        )
