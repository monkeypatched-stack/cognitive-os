from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.workorders.helpers import batch_release_workflows as crud
from services.workorders.models.batch_release_workflows import (
    BatchReleaseWorkflowCreate,
    BatchReleaseWorkflowResponse,
    BatchReleaseWorkflowUpdate,
    PaginatedBatchReleaseWorkflowResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedBatchReleaseWorkflowResponse)
async def list_batch_release_workflows(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    batch_execution_record_id: str | None = Query(None),
    batch_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    release_decision: str | None = Query(None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    records, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        batch_execution_record_id=batch_execution_record_id,
        batch_id=batch_id,
        status=status_filter,
        release_decision=release_decision,
    )
    return PaginatedBatchReleaseWorkflowResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/{batch_release_workflow_id}", response_model=BatchReleaseWorkflowResponse)
async def get_batch_release_workflow(
    batch_release_workflow_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    record = await crud.get_by_id(db, batch_release_workflow_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Batch release workflow '{batch_release_workflow_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=BatchReleaseWorkflowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_batch_release_workflow(
    data: BatchReleaseWorkflowCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-tasks")),
):
    if await crud.get_by_id(db, data.batch_release_workflow_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Batch release workflow '{data.batch_release_workflow_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    return await crud.create(db, data)


@router.patch(
    "/{batch_release_workflow_id}", response_model=BatchReleaseWorkflowResponse
)
async def update_batch_release_workflow(
    batch_release_workflow_id: str,
    data: BatchReleaseWorkflowUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await crud.update(db, batch_release_workflow_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Batch release workflow '{batch_release_workflow_id}' not found",
        )
    return updated


@router.delete("/{batch_release_workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_batch_release_workflow(
    batch_release_workflow_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-tasks")),
):
    if not await crud.delete(db, batch_release_workflow_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Batch release workflow '{batch_release_workflow_id}' not found",
        )
