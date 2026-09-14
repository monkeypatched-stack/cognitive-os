from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.process_definitions.helpers import packing_instructions as crud
from services.process_definitions.models.packing_instructions import (
    PackingInstructionCreate,
    PackingInstructionResponse,
    PackingInstructionUpdate,
    PaginatedPackingInstructionResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedPackingInstructionResponse)
async def list_packing_instructions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    process_definition_id: str | None = Query(None),
    bpr_document_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    records, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        process_definition_id=process_definition_id,
        bpr_document_id=bpr_document_id,
        status=status_filter,
    )
    return PaginatedPackingInstructionResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/{packing_instruction_id}", response_model=PackingInstructionResponse)
async def get_packing_instruction(
    packing_instruction_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    record = await crud.get_by_id(db, packing_instruction_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Packing instruction '{packing_instruction_id}' not found",
        )
    return record


@router.post(
    "/", response_model=PackingInstructionResponse, status_code=status.HTTP_201_CREATED
)
async def create_packing_instruction(
    data: PackingInstructionCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-process-definitions")),
):
    if await crud.get_by_id(db, data.packing_instruction_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Packing instruction '{data.packing_instruction_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    return await crud.create(db, data)


@router.patch("/{packing_instruction_id}", response_model=PackingInstructionResponse)
async def update_packing_instruction(
    packing_instruction_id: str,
    data: PackingInstructionUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    updated = await crud.update(db, packing_instruction_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Packing instruction '{packing_instruction_id}' not found",
        )
    return updated


@router.delete("/{packing_instruction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_packing_instruction(
    packing_instruction_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-process-definitions")),
):
    if not await crud.delete(db, packing_instruction_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Packing instruction '{packing_instruction_id}' not found",
        )
