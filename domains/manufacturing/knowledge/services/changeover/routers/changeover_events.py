from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.changeover.helpers import changeover as crud
from services.changeover.models.changeover import (
    ChangeoverEventCreate,
    ChangeoverEventResponse,
    ChangeoverEventUpdate,
    ChangeoverTaskCreate,
    ChangeoverTaskUpdate,
    PaginatedChangeoverEventResponse,
)

router = APIRouter()


@router.get("/events", response_model=PaginatedChangeoverEventResponse)
@router.get("", response_model=PaginatedChangeoverEventResponse)
async def list_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    results, total = await crud.get_all_events(db, page=page, page_size=page_size)
    return PaginatedChangeoverEventResponse(
        total=total, page=page, page_size=page_size, results=results
    )


@router.get(
    "/events/by-workstation/{workstation_id}",
    response_model=list[ChangeoverEventResponse],
)
@router.get(
    "/by-workstation/{workstation_id}", response_model=list[ChangeoverEventResponse]
)
async def list_events_by_workstation(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    return await crud.get_events_by_workstation(db, workstation_id)


@router.get(
    "/events/by-status/{status_value}", response_model=list[ChangeoverEventResponse]
)
@router.get("/by-status/{status_value}", response_model=list[ChangeoverEventResponse])
async def list_events_by_status(
    status_value: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    return await crud.get_events_by_status(db, status_value)


@router.get("/events/{event_id}", response_model=ChangeoverEventResponse)
@router.get("/{event_id}", response_model=ChangeoverEventResponse)
async def get_event(
    event_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-changeovers")),
):
    record = await crud.get_event_by_id(db, event_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Changeover event '{event_id}' not found"
        )
    return record


@router.post(
    "/events",
    response_model=ChangeoverEventResponse,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "", response_model=ChangeoverEventResponse, status_code=status.HTTP_201_CREATED
)
async def create_event(
    data: ChangeoverEventCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-changeovers")),
):
    if await crud.get_event_by_id(db, str(data.id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Changeover event '{data.id}' already exists",
        )
    return await crud.create_event(db, data)


@router.patch("/events/{event_id}", response_model=ChangeoverEventResponse)
@router.patch("/{event_id}", response_model=ChangeoverEventResponse)
async def update_event(
    event_id: str,
    data: ChangeoverEventUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-changeovers")),
):
    updated = await crud.update_event(db, event_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Changeover event '{event_id}' not found"
        )
    return updated


@router.post(
    "/events/{event_id}/tasks",
    response_model=ChangeoverEventResponse,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/{event_id}/tasks",
    response_model=ChangeoverEventResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_event_task(
    event_id: str,
    data: ChangeoverTaskCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-changeovers")),
):
    updated = await crud.add_event_task(db, event_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Changeover event '{event_id}' not found"
        )
    return updated


@router.patch(
    "/events/{event_id}/tasks/{task_id}", response_model=ChangeoverEventResponse
)
@router.patch("/{event_id}/tasks/{task_id}", response_model=ChangeoverEventResponse)
async def update_event_task(
    event_id: str,
    task_id: str,
    data: ChangeoverTaskUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-changeovers")),
):
    updated = await crud.update_event_task(db, event_id, task_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Changeover task '{task_id}' not found"
        )
    return updated


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-changeovers")),
):
    if not await crud.delete_event(db, event_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Changeover event '{event_id}' not found"
        )
