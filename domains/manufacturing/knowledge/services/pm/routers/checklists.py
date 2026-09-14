from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.pm.helpers import checklists as crud
from services.pm.models.checklists import (
    ChecklistCreate,
    ChecklistItemCreate,
    ChecklistItemUpdate,
    ChecklistResponse,
    ChecklistUpdate,
    PaginatedChecklistResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedChecklistResponse)
async def list_checklists(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-checklists")),
):
    checklists, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedChecklistResponse(
        total=total, page=page, page_size=page_size, results=checklists
    )


@router.get("/by-owner/{owner_id}", response_model=list[ChecklistResponse])
async def list_checklists_by_owner(
    owner_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-checklists")),
):
    return await crud.get_by_owner(db, owner_id)


@router.get("/by-work-order/{work_order_id}", response_model=list[ChecklistResponse])
async def list_checklists_by_work_order(
    work_order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-checklists")),
):
    return await crud.get_by_work_order(db, work_order_id)


@router.get(
    "/by-process_definition/{process_definition_id}",
    response_model=list[ChecklistResponse],
)
async def list_checklists_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-checklists")),
):
    return await crud.get_by_process_definition(db, process_definition_id)


@router.get("/by-tag/{tag}", response_model=list[ChecklistResponse])
async def list_checklists_by_tag(
    tag: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-checklists")),
):
    return await crud.get_by_tag(db, tag)


@router.get("/by-stage/{stage_id}", response_model=list[ChecklistResponse])
async def list_checklists_by_stage(
    stage_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-checklists")),
):
    return await crud.get_by_stage(db, stage_id)


@router.get("/open", response_model=list[ChecklistResponse])
async def list_open_checklists(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-checklists")),
):
    return await crud.get_open(db)


@router.get("/completed", response_model=list[ChecklistResponse])
async def list_completed_checklists(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-checklists")),
):
    return await crud.get_completed(db)


@router.get("/{checklist_id}", response_model=ChecklistResponse)
async def get_checklist(
    checklist_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-checklists")),
):
    record = await crud.get_by_id(db, checklist_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checklist '{checklist_id}' not found",
        )
    return record


@router.post("/", response_model=ChecklistResponse, status_code=status.HTTP_201_CREATED)
async def create_checklist(
    data: ChecklistCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-checklists")),
):
    if await crud.get_by_id(db, data.checklist_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Checklist '{data.checklist_id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{checklist_id}", response_model=ChecklistResponse)
async def update_checklist(
    checklist_id: str,
    data: ChecklistUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-checklists")),
):
    updated = await crud.update(db, checklist_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checklist '{checklist_id}' not found",
        )
    return updated


@router.post(
    "/{checklist_id}/items",
    response_model=ChecklistResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_checklist_item(
    checklist_id: str,
    data: ChecklistItemCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-checklists")),
):
    updated = await crud.add_item(db, checklist_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checklist '{checklist_id}' not found",
        )
    return updated


@router.patch("/{checklist_id}/items/{item_id}", response_model=ChecklistResponse)
async def update_checklist_item(
    checklist_id: str,
    item_id: str,
    data: ChecklistItemUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-checklists")),
):
    updated = await crud.update_item(db, checklist_id, item_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checklist item '{item_id}' not found",
        )
    return updated


@router.patch(
    "/{checklist_id}/items/{item_id}/completed", response_model=ChecklistResponse
)
async def set_checklist_item_completed(
    checklist_id: str,
    item_id: str,
    value: bool = Query(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-checklists")),
):
    updated = await crud.set_item_completed(db, checklist_id, item_id, value)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checklist item '{item_id}' not found",
        )
    return updated


@router.delete("/{checklist_id}/items/{item_id}", response_model=ChecklistResponse)
async def remove_checklist_item(
    checklist_id: str,
    item_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-checklists")),
):
    updated = await crud.remove_item(db, checklist_id, item_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checklist '{checklist_id}' not found",
        )
    return updated


@router.delete("/{checklist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_checklist(
    checklist_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-checklists")),
):
    if not await crud.delete(db, checklist_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checklist '{checklist_id}' not found",
        )
