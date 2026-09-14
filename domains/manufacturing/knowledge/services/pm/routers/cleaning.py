from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.db import get_database
from services.common.auth import require_permission
from services.pm.models.cleaning import (
    CleaningRecordCreate,
    CleaningRecordUpdate,
    CleaningRecordResponse,
    PaginatedCleaningRecordResponse,
)
from services.pm.helpers import cleaning as crud

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedCleaningRecordResponse)
async def list_cleaning_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-cleaning")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedCleaningRecordResponse(
        total=total, page=page, page_size=page_size, results=records
    )


# ── Get by machine ────────────────────────────────────────────────────────────


@router.get("/by-equipment/{equipment_id}", response_model=list[CleaningRecordResponse])
async def list_cleaning_records_by_machine(
    equipment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-cleaning")),
):
    return await crud.get_by_equipment_id(db, equipment_id)


# ── Get by status ─────────────────────────────────────────────────────────────


@router.get("/by-status/{status_value}", response_model=list[CleaningRecordResponse])
async def list_cleaning_records_by_status(
    status_value: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-cleaning")),
):
    return await crud.get_by_status(db, status_value)


# ── Get by work order ─────────────────────────────────────────────────────────


@router.get(
    "/by-work-order/{work_order_id}", response_model=list[CleaningRecordResponse]
)
async def list_cleaning_records_by_work_order(
    work_order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-cleaning")),
):
    return await crud.get_by_work_order(db, work_order_id)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{record_id}", response_model=CleaningRecordResponse)
async def get_cleaning_record(
    record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-cleaning")),
):
    record = await crud.get_by_id(db, record_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cleaning record '{record_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=CleaningRecordResponse, status_code=status.HTTP_201_CREATED
)
async def create_cleaning_record(
    data: CleaningRecordCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-cleaning")),
):
    if await crud.get_by_id(db, data.record_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cleaning record '{data.record_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{record_id}", response_model=CleaningRecordResponse)
async def update_cleaning_record(
    record_id: str,
    data: CleaningRecordUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-cleaning")),
):
    updated = await crud.update(db, record_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cleaning record '{record_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cleaning_record(
    record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-cleaning")),
):
    if not await crud.delete(db, record_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cleaning record '{record_id}' not found",
        )
