from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.shipping.helpers import waybill as crud
from services.shipping.models.waybill import (
    PaginatedWaybillResponse,
    WaybillCreate,
    WaybillResponse,
    WaybillStatus,
    WaybillUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedWaybillResponse)
async def list_waybills(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedWaybillResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-number/{waybill_number}", response_model=WaybillResponse)
async def get_waybill_by_number(
    waybill_number: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_number(db, waybill_number)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Waybill '{waybill_number}' not found"
        )
    return record


@router.get("/by-status/{waybill_status}", response_model=list[WaybillResponse])
async def list_waybills_by_status(
    waybill_status: WaybillStatus,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_status(db, waybill_status.value)


@router.get("/by-barcode/{barcode}", response_model=WaybillResponse)
async def get_waybill_by_barcode(
    barcode: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_barcode(db, barcode)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Waybill barcode '{barcode}' not found"
        )
    return record


@router.get("/{waybill_id}", response_model=WaybillResponse)
async def get_waybill(
    waybill_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, waybill_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Waybill '{waybill_id}' not found"
        )
    return record


@router.post("/", response_model=WaybillResponse, status_code=status.HTTP_201_CREATED)
async def create_waybill(
    data: WaybillCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    waybill_id = str(data.id)
    if await crud.get_by_id(db, waybill_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Waybill '{waybill_id}' already exists"
        )
    if await crud.get_by_number(db, data.waybill_number):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Waybill '{data.waybill_number}' already exists",
        )
    if data.barcode and await crud.get_by_barcode(db, data.barcode):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Waybill barcode '{data.barcode}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{waybill_id}", response_model=WaybillResponse)
async def update_waybill(
    waybill_id: str,
    data: WaybillUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    if data.waybill_number:
        existing = await crud.get_by_number(db, data.waybill_number)
        if existing and existing.get("id") != waybill_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Waybill '{data.waybill_number}' already exists",
            )
    if data.barcode:
        existing = await crud.get_by_barcode(db, data.barcode)
        if existing and existing.get("id") != waybill_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Waybill barcode '{data.barcode}' already exists",
            )
    updated = await crud.update(db, waybill_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Waybill '{waybill_id}' not found"
        )
    return updated


@router.delete("/{waybill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_waybill(
    waybill_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, waybill_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Waybill '{waybill_id}' not found"
        )
