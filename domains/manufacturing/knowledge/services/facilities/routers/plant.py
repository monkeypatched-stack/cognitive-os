import re
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.facilities.helpers import plants as crud
from services.facilities.helpers import lines as line_crud
from services.facilities.helpers import stages as stage_crud
from services.assets.helpers import machines as machine_crud
from services.assets.helpers import equipment as equipment_crud

from services.facilities.models.industrialPlant import (
    IndustrialPlantCreate,
    IndustrialPlantUpdate,
    IndustrialPlantResponse,
    PaginatedPlantResponse,
)
from services.facilities.models.industrialLine import IndustrialLineResponse
from services.facilities.models.stages import IndustrialStageResponse
from services.assets.models.machines import PharmaceuticalMachineResponse
from services.assets.models.equipment import PharmaceuticalEquipmentResponse

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedPlantResponse)
async def list_plants(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    status: Optional[str] = Query(None),
    plant_type: Optional[str] = Query(None, alias="type"),
    country: Optional[str] = Query(None),
    timezone: Optional[str] = Query(None),
    manager: Optional[str] = Query(None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-plant")),
):
    plants, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        status=status,
        plant_type=plant_type,
        country=country,
        timezone=timezone,
        manager=manager,
    )
    return PaginatedPlantResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=plants,
    )


# ── Get One ───────────────────────────────────────────────────────────────────


@router.get("/{plant_id}/lines", response_model=list[IndustrialLineResponse])
async def list_plant_lines(
    plant_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-lines")),
):
    record = await crud.get_by_id(db, plant_id)
    if not record:
        record = await db["industrial_plants"].find_one(
            {"name": {"$regex": f"^{re.escape(plant_id)}$", "$options": "i"}},
        )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plant '{plant_id}' not found",
        )
    return await line_crud.get_by_plant(db, str(record.get("id") or plant_id))


@router.get("/{plant_id}/stages", response_model=list[IndustrialStageResponse])
async def list_plant_stages(
    plant_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-stages")),
):
    record = await _resolve_plant(db, plant_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plant '{plant_id}' not found",
        )
    return await stage_crud.get_by_plant(db, str(record.get("id") or plant_id))


@router.get("/{plant_id}/machines", response_model=list[PharmaceuticalMachineResponse])
async def list_plant_machines(
    plant_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-machines")),
):
    record = await _resolve_plant(db, plant_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plant '{plant_id}' not found",
        )
    return await machine_crud.get_by_plant(db, str(record.get("id") or plant_id))


@router.get(
    "/{plant_id}/equipment", response_model=list[PharmaceuticalEquipmentResponse]
)
async def list_plant_equipment(
    plant_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-equipment")),
):
    record = await _resolve_plant(db, plant_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plant '{plant_id}' not found",
        )
    return await equipment_crud.get_by_plant(db, str(record.get("id") or plant_id))


@router.get("/{plant_id}", response_model=IndustrialPlantResponse)
async def get_plant(
    plant_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-plant")),
):
    record = await crud.get_by_id(db, plant_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plant '{plant_id}' not found",
        )
    return record


async def _resolve_plant(db: AsyncIOMotorDatabase, plant_id: str) -> dict | None:
    record = await crud.get_by_id(db, plant_id)
    if record:
        return record
    return await db["industrial_plants"].find_one(
        {"name": {"$regex": f"^{re.escape(plant_id)}$", "$options": "i"}},
    )


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=IndustrialPlantResponse, status_code=status.HTTP_201_CREATED
)
async def create_plant(
    data: IndustrialPlantCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-plant")),
):
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Plant '{data.id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{plant_id}", response_model=IndustrialPlantResponse)
async def update_plant(
    plant_id: str,
    data: IndustrialPlantUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-plant")),
):
    updated = await crud.update(db, plant_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plant '{plant_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{plant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plant(
    plant_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-plant")),
):
    if not await crud.delete(db, plant_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plant '{plant_id}' not found",
        )


# ── Future Extensions ─────────────────────────────────────────────────────────

# TODO_ENDPOINT: GET /api/v1/plants/{plant_id}/lines — list production lines in a plant
# TODO_ENDPOINT: GET /api/v1/plants/{plant_id}/machines — list machines in a plant
# TODO_ENDPOINT: GET /api/v1/plants/{plant_id}/analytics — plant analytics and KPIs
