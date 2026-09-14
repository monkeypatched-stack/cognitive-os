from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.assets.helpers import tags as crud
from services.assets.models.tags import (
    PaginatedRFIDTagResponse,
    RFIDAssignedType,
    RFIDTagCreate,
    RFIDTagRecord,
    RFIDTagStatus,
    RFIDTagUpdate,
)
from services.common.auth import require_permission
from services.common.db import get_database

router = APIRouter()


@router.get("", response_model=PaginatedRFIDTagResponse)
@router.get("/", response_model=PaginatedRFIDTagResponse, include_in_schema=False)
async def list_rfid_tags(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    status_filter: Optional[RFIDTagStatus] = Query(None, alias="status"),
    assigned_type: Optional[RFIDAssignedType] = Query(None),
    assigned_to: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-rfid-tags")),
):
    tags, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        status=status_filter,
        assigned_type=assigned_type,
        assigned_to=assigned_to,
        q=q,
    )
    return PaginatedRFIDTagResponse(
        total=total, page=page, page_size=page_size, results=tags
    )


@router.get("/by-barcode/{barcode}", response_model=RFIDTagRecord)
async def get_rfid_tag_by_barcode(
    barcode: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-rfid-tags")),
):
    record = await crud.get_by_barcode(db, barcode)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RFID tag barcode '{barcode}' not found",
        )
    return record


@router.get("/{rfid_tag_id}", response_model=RFIDTagRecord)
async def get_rfid_tag(
    rfid_tag_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-rfid-tags")),
):
    record = await crud.get_by_id(db, rfid_tag_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RFID tag '{rfid_tag_id}' not found",
        )
    return record


@router.post("", response_model=RFIDTagRecord, status_code=status.HTTP_201_CREATED)
@router.post(
    "/",
    response_model=RFIDTagRecord,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_rfid_tag(
    data: RFIDTagCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-rfid-tags")),
):
    if await crud.get_by_code(db, data.tag_code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"RFID tag code '{data.tag_code}' already exists",
        )
    if data.barcode and await crud.get_by_barcode(db, data.barcode):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"RFID tag barcode '{data.barcode}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{rfid_tag_id}", response_model=RFIDTagRecord)
async def update_rfid_tag(
    rfid_tag_id: str,
    data: RFIDTagUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-rfid-tags")),
):
    if data.tag_code:
        existing_code = await crud.get_by_code(db, data.tag_code)
        if existing_code and existing_code["rfid_tag_id"] != rfid_tag_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"RFID tag code '{data.tag_code}' already exists",
            )

    if data.barcode:
        existing_barcode = await crud.get_by_barcode(db, data.barcode)
        if existing_barcode and existing_barcode["rfid_tag_id"] != rfid_tag_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"RFID tag barcode '{data.barcode}' already exists",
            )

    updated = await crud.update(db, rfid_tag_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RFID tag '{rfid_tag_id}' not found",
        )
    return updated


@router.delete("/{rfid_tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rfid_tag(
    rfid_tag_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-rfid-tags")),
):
    if not await crud.delete(db, rfid_tag_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RFID tag '{rfid_tag_id}' not found",
        )
