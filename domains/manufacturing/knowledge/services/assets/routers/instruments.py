from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import get_current_user, require_permission
from services.assets.helpers import instruments as crud
from services.assets.models.instruments import (
    InstrumentCreate,
    InstrumentUpdate,
    InstrumentResponse,
    PaginatedInstrumentResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedInstrumentResponse)
async def list_instruments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-instruments")),
):
    from typing import Optional

    instruments, total = await crud.get_all(db, page, page_size, category, status, q)
    return PaginatedInstrumentResponse(
        total=total, page=page, page_size=page_size, results=instruments
    )


@router.get("/{instrument_id}", response_model=InstrumentResponse)
async def get_instrument(
    instrument_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-instruments")),
):
    doc = await crud.get_by_id(db, instrument_id)
    if not doc:
        raise HTTPException(
            status_code=404, detail=f"Instrument '{instrument_id}' not found"
        )
    return doc


@router.post(
    "/", response_model=InstrumentResponse, status_code=status.HTTP_201_CREATED
)
async def create_instrument(
    data: InstrumentCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-instruments")),
):
    return await crud.create(db, data)


@router.patch("/{instrument_id}", response_model=InstrumentResponse)
async def update_instrument(
    instrument_id: str,
    data: InstrumentUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-instruments")),
):
    updated = await crud.update(db, instrument_id, data)
    if not updated:
        raise HTTPException(
            status_code=404, detail=f"Instrument '{instrument_id}' not found"
        )
    return updated


@router.delete("/{instrument_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_instrument(
    instrument_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-instruments")),
):
    if not await crud.delete(db, instrument_id):
        raise HTTPException(
            status_code=404, detail=f"Instrument '{instrument_id}' not found"
        )
