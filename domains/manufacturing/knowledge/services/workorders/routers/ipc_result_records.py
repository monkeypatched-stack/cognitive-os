from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.workorders.helpers import ipc_result_records as crud
from services.workorders.models.ipc_result_records import (
    IpcResultRecordCreate,
    IpcResultRecordResponse,
    IpcResultRecordUpdate,
    PaginatedIpcResultRecordResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedIpcResultRecordResponse)
async def list_ipc_result_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    batch_execution_record_id: str | None = Query(None),
    batch_step_execution_id: str | None = Query(None),
    ipc_checkpoint_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    records, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        batch_execution_record_id=batch_execution_record_id,
        batch_step_execution_id=batch_step_execution_id,
        ipc_checkpoint_id=ipc_checkpoint_id,
        status=status_filter,
    )
    return PaginatedIpcResultRecordResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/{ipc_result_id}", response_model=IpcResultRecordResponse)
async def get_ipc_result_record(
    ipc_result_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    record = await crud.get_by_id(db, ipc_result_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"IPC result record '{ipc_result_id}' not found",
        )
    return record


@router.post(
    "/", response_model=IpcResultRecordResponse, status_code=status.HTTP_201_CREATED
)
async def create_ipc_result_record(
    data: IpcResultRecordCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-tasks")),
):
    if await crud.get_by_id(db, data.ipc_result_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"IPC result record '{data.ipc_result_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    return await crud.create(db, data)


@router.patch("/{ipc_result_id}", response_model=IpcResultRecordResponse)
async def update_ipc_result_record(
    ipc_result_id: str,
    data: IpcResultRecordUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await crud.update(db, ipc_result_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"IPC result record '{ipc_result_id}' not found",
        )
    return updated


@router.delete("/{ipc_result_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ipc_result_record(
    ipc_result_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-tasks")),
):
    if not await crud.delete(db, ipc_result_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"IPC result record '{ipc_result_id}' not found",
        )
