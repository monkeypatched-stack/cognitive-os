from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.changeover.helpers import changeover as crud
from services.changeover.models.changeover import (
    ChangeoverKPICreate,
    ChangeoverKPIResponse,
    ChangeoverKPIUpdate,
    PaginatedChangeoverKPIResponse,
)

router = APIRouter()


@router.get("/kpis", response_model=PaginatedChangeoverKPIResponse)
async def list_kpis(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    results, total = await crud.get_all_kpis(db, page=page, page_size=page_size)
    return PaginatedChangeoverKPIResponse(
        total=total, page=page, page_size=page_size, results=results
    )


@router.get(
    "/kpis/by-workstation/{workstation_id}", response_model=list[ChangeoverKPIResponse]
)
async def list_kpis_by_workstation(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    return await crud.get_kpis_by_workstation(db, workstation_id)


@router.get("/kpis/{kpi_id}", response_model=ChangeoverKPIResponse)
async def get_kpi(
    kpi_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    record = await crud.get_kpi_by_id(db, kpi_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Changeover KPI '{kpi_id}' not found"
        )
    return record


@router.post(
    "/kpis", response_model=ChangeoverKPIResponse, status_code=status.HTTP_201_CREATED
)
async def create_kpi(
    data: ChangeoverKPICreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-changeovers")),
):
    if await crud.get_kpi_by_id(db, str(data.id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Changeover KPI '{data.id}' already exists",
        )
    return await crud.create_kpi(db, data)


@router.patch("/kpis/{kpi_id}", response_model=ChangeoverKPIResponse)
async def update_kpi(
    kpi_id: str,
    data: ChangeoverKPIUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-changeovers")),
):
    updated = await crud.update_kpi(db, kpi_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Changeover KPI '{kpi_id}' not found"
        )
    return updated


@router.delete("/kpis/{kpi_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kpi(
    kpi_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-changeovers")),
):
    if not await crud.delete_kpi(db, kpi_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Changeover KPI '{kpi_id}' not found"
        )
