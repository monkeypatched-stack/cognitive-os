from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.changeover.helpers import changeover as crud
from services.changeover.models.changeover import (
    ChangeoverProcedureCreate,
    ChangeoverProcedureResponse,
    ChangeoverProcedureUpdate,
    ChangeoverTaskUpdate,
    PaginatedChangeoverProcedureResponse,
)

router = APIRouter()


@router.get("", response_model=PaginatedChangeoverProcedureResponse)
async def list_procedures(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    results, total = await crud.get_all_procedures(db, page=page, page_size=page_size)
    return PaginatedChangeoverProcedureResponse(
        total=total, page=page, page_size=page_size, results=results
    )


@router.get(
    "/by-factory/{factory_id}", response_model=list[ChangeoverProcedureResponse]
)
async def list_procedures_by_factory(
    factory_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    return await crud.get_procedures_by_factory(db, factory_id)


@router.get("/by-plant/{plant_id}", response_model=list[ChangeoverProcedureResponse])
async def list_procedures_by_plant(
    plant_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    return await crud.get_procedures_by_plant(db, plant_id)


@router.get("/{procedure_id}", response_model=ChangeoverProcedureResponse)
async def get_procedure(
    procedure_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    record = await crud.get_procedure_by_id(db, procedure_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Changeover procedure '{procedure_id}' not found",
        )
    return record


@router.post(
    "", response_model=ChangeoverProcedureResponse, status_code=status.HTTP_201_CREATED
)
async def create_procedure(
    data: ChangeoverProcedureCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-changeovers")),
):
    if await crud.get_procedure_by_id(db, str(data.id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Changeover procedure '{data.id}' already exists",
        )
    return await crud.create_procedure(db, data)


@router.patch("/{procedure_id}", response_model=ChangeoverProcedureResponse)
async def update_procedure(
    procedure_id: str,
    data: ChangeoverProcedureUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-changeovers")),
):
    updated = await crud.update_procedure(db, procedure_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Changeover procedure '{procedure_id}' not found",
        )
    return updated


@router.patch(
    "/{procedure_id}/tasks/{task_id}", response_model=ChangeoverProcedureResponse
)
async def update_procedure_task(
    procedure_id: str,
    task_id: str,
    data: ChangeoverTaskUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-changeovers")),
):
    updated = await crud.update_procedure_task(db, procedure_id, task_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Changeover task '{task_id}' not found"
        )
    return updated


@router.patch(
    "/{procedure_id}/tasks/{task_id}/complete",
    response_model=ChangeoverProcedureResponse,
)
async def set_procedure_task_complete(
    procedure_id: str,
    task_id: str,
    value: bool = Query(True),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-changeovers")),
):
    updated = await crud.set_procedure_task_complete(db, procedure_id, task_id, value)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Changeover task '{task_id}' not found"
        )
    return updated


@router.delete("/{procedure_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_procedure(
    procedure_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-changeovers")),
):
    if not await crud.delete_procedure(db, procedure_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Changeover procedure '{procedure_id}' not found",
        )
