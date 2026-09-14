from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import get_current_user, require_permission
from services.common.db import get_database
from services.shifts.helpers import timesheets as crud
from services.shifts.models.timesheet import (
    PaginatedTimeSheetResponse,
    TimeSheetCreate,
    TimeSheetResponse,
    TimeSheetUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedTimeSheetResponse)
async def list_timesheets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedTimeSheetResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-worker/{worker_id}", response_model=list[TimeSheetResponse])
async def list_timesheets_by_worker(
    worker_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await crud.get_by_worker(db, worker_id)


@router.get(
    "/by-payroll-reference/{payroll_reference}", response_model=TimeSheetResponse
)
async def get_timesheet_by_payroll_reference(
    payroll_reference: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await crud.get_by_payroll_reference(db, payroll_reference)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Timesheet with payroll reference '{payroll_reference}' not found",
        )
    return record


@router.get("/by-approval/{approved}", response_model=list[TimeSheetResponse])
async def list_timesheets_by_approval(
    approved: bool,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await crud.get_by_approval(db, approved)


@router.get("/{timesheet_id}", response_model=TimeSheetResponse)
async def get_timesheet(
    timesheet_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await crud.get_by_id(db, timesheet_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Timesheet '{timesheet_id}' not found"
        )
    return record


@router.post("/", response_model=TimeSheetResponse, status_code=status.HTTP_201_CREATED)
async def create_timesheet(
    data: TimeSheetCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-shifts")),
):
    timesheet_id = str(data.id)
    if await crud.get_by_id(db, timesheet_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Timesheet '{timesheet_id}' already exists",
        )
    if data.payroll_reference and await crud.get_by_payroll_reference(
        db, data.payroll_reference
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Timesheet with payroll reference '{data.payroll_reference}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{timesheet_id}", response_model=TimeSheetResponse)
async def update_timesheet(
    timesheet_id: str,
    data: TimeSheetUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-shifts")),
):
    if data.payroll_reference:
        existing = await crud.get_by_payroll_reference(db, data.payroll_reference)
        if existing and existing.get("id") != timesheet_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Timesheet with payroll reference '{data.payroll_reference}' already exists",
            )
    updated = await crud.update(db, timesheet_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Timesheet '{timesheet_id}' not found"
        )
    return updated


@router.delete("/{timesheet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_timesheet(
    timesheet_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-shifts")),
):
    if not await crud.delete(db, timesheet_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Timesheet '{timesheet_id}' not found"
        )
