from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.shipping.helpers import delivery_note as crud
from services.shipping.models.delivery_note import (
    DeliveryNoteCreate,
    DeliveryNoteResponse,
    DeliveryNoteUpdate,
    PaginatedDeliveryNoteResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedDeliveryNoteResponse)
async def list_delivery_notes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedDeliveryNoteResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-number/{delivery_note_number}", response_model=DeliveryNoteResponse)
async def get_delivery_note_by_number(
    delivery_note_number: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_number(db, delivery_note_number)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Delivery note '{delivery_note_number}' not found",
        )
    return record


@router.get("/by-status/{status_value}", response_model=list[DeliveryNoteResponse])
async def list_delivery_notes_by_status(
    status_value: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_status(db, status_value)


@router.get("/by-carrier/{carrier_id}", response_model=list[DeliveryNoteResponse])
async def list_delivery_notes_by_carrier(
    carrier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_carrier(db, carrier_id)


@router.get("/by-vehicle/{vehicle_id}", response_model=list[DeliveryNoteResponse])
async def list_delivery_notes_by_vehicle(
    vehicle_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_vehicle(db, vehicle_id)


@router.get("/by-tracking/{tracking_number}", response_model=DeliveryNoteResponse)
async def get_delivery_note_by_tracking_number(
    tracking_number: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_tracking_number(db, tracking_number)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Delivery note with tracking number '{tracking_number}' not found",
        )
    return record


@router.get("/{delivery_note_id}", response_model=DeliveryNoteResponse)
async def get_delivery_note(
    delivery_note_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, delivery_note_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Delivery note '{delivery_note_id}' not found",
        )
    return record


@router.post(
    "/", response_model=DeliveryNoteResponse, status_code=status.HTTP_201_CREATED
)
async def create_delivery_note(
    data: DeliveryNoteCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    delivery_note_id = str(data.id)
    if await crud.get_by_id(db, delivery_note_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Delivery note '{delivery_note_id}' already exists",
        )
    if await crud.get_by_number(db, data.delivery_note_number):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Delivery note '{data.delivery_note_number}' already exists",
        )
    if data.tracking_number and await crud.get_by_tracking_number(
        db, data.tracking_number
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Delivery note with tracking number '{data.tracking_number}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{delivery_note_id}", response_model=DeliveryNoteResponse)
async def update_delivery_note(
    delivery_note_id: str,
    data: DeliveryNoteUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    if data.delivery_note_number:
        existing = await crud.get_by_number(db, data.delivery_note_number)
        if existing and existing.get("id") != delivery_note_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Delivery note '{data.delivery_note_number}' already exists",
            )
    if data.tracking_number:
        existing = await crud.get_by_tracking_number(db, data.tracking_number)
        if existing and existing.get("id") != delivery_note_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Delivery note with tracking number '{data.tracking_number}' already exists",
            )
    updated = await crud.update(db, delivery_note_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Delivery note '{delivery_note_id}' not found",
        )
    return updated


@router.delete("/{delivery_note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_delivery_note(
    delivery_note_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, delivery_note_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Delivery note '{delivery_note_id}' not found",
        )
