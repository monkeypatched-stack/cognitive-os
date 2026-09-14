from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import get_current_user, require_permission
from services.shifts.helpers import shift_templates as crud
from services.shifts.models.shift_template import (
    PaginatedShiftTemplateResponse,
    ShiftTemplateCreate,
    ShiftTemplateResponse,
    ShiftTemplateUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedShiftTemplateResponse)
async def list_shift_templates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    templates, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedShiftTemplateResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=templates,
    )


@router.get("/by-factory/{factory_id}", response_model=list[ShiftTemplateResponse])
async def list_shift_templates_by_factory(
    factory_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await crud.get_by_factory(db, factory_id)


@router.get("/{template_id}", response_model=ShiftTemplateResponse)
async def get_shift_template(
    template_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await crud.get_by_id(db, template_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shift template '{template_id}' not found",
        )
    return record


@router.post(
    "/", response_model=ShiftTemplateResponse, status_code=status.HTTP_201_CREATED
)
async def create_shift_template(
    data: ShiftTemplateCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-shift-templates")),
):
    if await crud.get_by_id(db, str(data.id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Shift template '{data.id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{template_id}", response_model=ShiftTemplateResponse)
async def update_shift_template(
    template_id: str,
    data: ShiftTemplateUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-shift-templates")),
):
    updated = await crud.update(db, template_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shift template '{template_id}' not found",
        )
    return updated


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shift_template(
    template_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-shift-templates")),
):
    if not await crud.delete(db, template_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shift template '{template_id}' not found",
        )
