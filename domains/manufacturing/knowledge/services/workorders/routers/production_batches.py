from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.workorders.helpers import production_batches as crud
from services.workorders.models.production_batches import (
    PaginatedProductionBatchResponse,
    ProductionBatchCreate,
    ProductionBatchResponse,
    ProductionBatchUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedProductionBatchResponse)
async def list_production_batches(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    product_id: str | None = Query(None),
    work_order_id: str | None = Query(None),
    process_definition_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    records, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        product_id=product_id,
        work_order_id=work_order_id,
        process_definition_id=process_definition_id,
        status=status_filter,
    )
    return PaginatedProductionBatchResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/{batch_id}", response_model=ProductionBatchResponse)
async def get_production_batch(
    batch_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    record = await crud.get_by_id(db, batch_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Production batch '{batch_id}' not found"
        )
    return record


@router.post(
    "/", response_model=ProductionBatchResponse, status_code=status.HTTP_201_CREATED
)
async def create_production_batch(
    data: ProductionBatchCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-tasks")),
):
    if await crud.get_by_id(db, data.batch_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Production batch '{data.batch_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    return await crud.create(db, data)


@router.patch("/{batch_id}", response_model=ProductionBatchResponse)
async def update_production_batch(
    batch_id: str,
    data: ProductionBatchUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await crud.update(db, batch_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Production batch '{batch_id}' not found"
        )
    return updated


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_production_batch(
    batch_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-tasks")),
):
    if not await crud.delete(db, batch_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Production batch '{batch_id}' not found"
        )
