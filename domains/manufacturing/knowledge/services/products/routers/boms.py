from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.products.helpers import boms as crud
from services.products.models.product_component import (
    BOMCreate,
    BOMRecord,
    BOMUpdate,
    PaginatedBOMResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedBOMResponse)
async def list_boms(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-components")),
):
    boms, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedBOMResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=boms,
    )


@router.get("/by-code/{bom_code}", response_model=list[BOMRecord])
async def list_boms_by_code(
    bom_code: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-components")),
):
    return await crud.get_by_code(db, bom_code)


@router.get("/by-parent-product/{parent_product_id}", response_model=list[BOMRecord])
async def list_boms_by_parent_product(
    parent_product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-components")),
):
    return await crud.get_by_parent_product(db, parent_product_id)


@router.get("/by-parent-product/{parent_product_id}/current", response_model=BOMRecord)
async def get_current_bom_by_parent_product(
    parent_product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-components")),
):
    record = await crud.get_current_by_parent_product(db, parent_product_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Current BOM for parent product '{parent_product_id}' not found",
        )
    return record


@router.get("/{bom_id}", response_model=BOMRecord)
async def get_bom(
    bom_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-product-components")),
):
    record = await crud.get_by_id(db, bom_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"BOM '{bom_id}' not found",
        )
    return record


@router.post("/", response_model=BOMRecord, status_code=status.HTTP_201_CREATED)
async def create_bom(
    data: BOMCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-product-components")),
):
    if await crud.get_by_id(db, data.bom_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"BOM '{data.bom_id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{bom_id}", response_model=BOMRecord)
async def update_bom(
    bom_id: str,
    data: BOMUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-product-components")),
):
    updated = await crud.update(db, bom_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"BOM '{bom_id}' not found",
        )
    return updated


@router.delete("/{bom_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bom(
    bom_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-product-components")),
):
    if not await crud.delete(db, bom_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"BOM '{bom_id}' not found",
        )
