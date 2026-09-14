from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import get_current_user, require_permission
from services.common.db import get_database
from services.assets.helpers import plc as crud
from services.assets.models.plc import (
    PLCCreate,
    PLCResponse,
    PLCStatus,
    PLCUpdate,
    PaginatedPLCResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedPLCResponse)
async def list_plcs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedPLCResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-status/{plc_status}", response_model=list[PLCResponse])
async def list_plcs_by_status(
    plc_status: PLCStatus,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await crud.get_by_status(db, plc_status.value)


@router.get("/by-online/{is_online}", response_model=list[PLCResponse])
async def list_plcs_by_online(
    is_online: bool,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await crud.get_by_online(db, is_online)


@router.get("/by-plant-area/{plant_area}", response_model=list[PLCResponse])
async def list_plcs_by_plant_area(
    plant_area: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await crud.get_by_plant_area(db, plant_area)


@router.get("/{plc_id}", response_model=PLCResponse)
async def get_plc(
    plc_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await crud.get_by_id(db, plc_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"PLC '{plc_id}' not found"
        )
    return record


@router.post("/", response_model=PLCResponse, status_code=status.HTTP_201_CREATED)
async def create_plc(
    data: PLCCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-sensors")),
):
    if await crud.get_by_id(db, data.plc_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"PLC '{data.plc_id}' already exists"
        )
    return await crud.create(db, data)


@router.patch("/{plc_id}", response_model=PLCResponse)
async def update_plc(
    plc_id: str,
    data: PLCUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-sensors")),
):
    updated = await crud.update(db, plc_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"PLC '{plc_id}' not found"
        )
    return updated


@router.delete("/{plc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plc(
    plc_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-sensors")),
):
    if not await crud.delete(db, plc_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"PLC '{plc_id}' not found"
        )
