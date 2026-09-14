from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.process_definitions.helpers import mbmr_templates as crud
from services.process_definitions.models.mbmr_templates import (
    MbmrTemplateCreate,
    MbmrTemplateResponse,
    MbmrTemplateUpdate,
    PaginatedMbmrTemplateResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedMbmrTemplateResponse)
async def list_mbmr_templates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    process_definition_id: str | None = Query(None),
    bom_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    records, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        process_definition_id=process_definition_id,
        bom_id=bom_id,
        status=status_filter,
    )
    return PaginatedMbmrTemplateResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/{mbmr_template_id}", response_model=MbmrTemplateResponse)
async def get_mbmr_template(
    mbmr_template_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    record = await crud.get_by_id(db, mbmr_template_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"MBMR template '{mbmr_template_id}' not found",
        )
    return record


@router.post(
    "/", response_model=MbmrTemplateResponse, status_code=status.HTTP_201_CREATED
)
async def create_mbmr_template(
    data: MbmrTemplateCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-process-definitions")),
):
    if await crud.get_by_id(db, data.mbmr_template_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"MBMR template '{data.mbmr_template_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    return await crud.create(db, data)


@router.patch("/{mbmr_template_id}", response_model=MbmrTemplateResponse)
async def update_mbmr_template(
    mbmr_template_id: str,
    data: MbmrTemplateUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    updated = await crud.update(db, mbmr_template_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"MBMR template '{mbmr_template_id}' not found",
        )
    return updated


@router.delete("/{mbmr_template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mbmr_template(
    mbmr_template_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-process-definitions")),
):
    if not await crud.delete(db, mbmr_template_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"MBMR template '{mbmr_template_id}' not found",
        )
