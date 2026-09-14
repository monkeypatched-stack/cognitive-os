from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.inventory.helpers import inventory_stage_outputs as crud
from services.inventory.models.inventory_stage_outputs import (
    InventoryStageOutputCreate,
    InventoryStageOutputResponse,
    InventoryStageOutputUpdate,
    PaginatedInventoryStageOutputResponse,
)

router = APIRouter()


@router.get("/")
async def list_inventory_stage_outputs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return {"total": total, "page": page, "page_size": page_size, "results": records}


@router.get("/final-products", response_model=list[InventoryStageOutputResponse])
async def list_final_product_outputs(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_final_products(db)


@router.get(
    "/by-product/{product_id}", response_model=list[InventoryStageOutputResponse]
)
async def list_inventory_stage_outputs_by_product(
    product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_product_id(db, product_id)


@router.get("/by-sku/{sku}", response_model=list[InventoryStageOutputResponse])
async def list_inventory_stage_outputs_by_sku(
    sku: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_sku(db, sku)


@router.get("/by-stage/{stage_id}", response_model=list[InventoryStageOutputResponse])
async def list_inventory_stage_outputs_by_stage(
    stage_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_stage_id(db, stage_id)


@router.get(
    "/by-workstation/{workstation_id}",
    response_model=list[InventoryStageOutputResponse],
)
async def list_inventory_stage_outputs_by_workstation(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await crud.get_by_workstation_id(db, workstation_id)


@router.get("/{stage_output_id}", response_model=InventoryStageOutputResponse)
async def get_inventory_stage_output(
    stage_output_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    record = await crud.get_by_stage_output_id(db, stage_output_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory stage output '{stage_output_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=InventoryStageOutputResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_inventory_stage_output(
    data: InventoryStageOutputCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    return await crud.create(db, data)


@router.patch("/{stage_output_id}", response_model=InventoryStageOutputResponse)
async def update_inventory_stage_output(
    stage_output_id: str,
    data: InventoryStageOutputUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    updated = await crud.update(db, stage_output_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory stage output '{stage_output_id}' not found",
        )
    return updated


@router.delete("/{stage_output_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inventory_stage_output(
    stage_output_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    if not await crud.delete(db, stage_output_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory stage output '{stage_output_id}' not found",
        )
