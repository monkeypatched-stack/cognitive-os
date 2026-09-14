from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from services.common.auth import require_permission
from services.events.helpers import events as crud
from services.events.models.events import EventResponse, PaginatedEventsResponse

router = APIRouter()


@router.get("/", response_model=PaginatedEventsResponse)
async def list_events(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, description="Items per page"),
    scope: Optional[str] = Query(None, description="Filter by scope"),
    category: Optional[str] = Query(None, description="Filter by category"),
    status: Optional[str] = Query(None, description="Filter by status"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    event_type: Optional[str] = Query(
        None, alias="type", description="Filter by event type"
    ),
    _: dict = Depends(require_permission("perm-view-events")),
):
    events, total = await crud.get_all_events(
        page=page,
        page_size=page_size,
        status=status,
        priority=priority,
        event_type=event_type,
        scope=scope,
        category=category,
    )
    return PaginatedEventsResponse(
        total=total, page=page, page_size=page_size, results=events
    )


@router.get("/by-machine/{machine_id}", response_model=PaginatedEventsResponse)
async def get_events_by_machine(
    machine_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    _: dict = Depends(require_permission("perm-view-events")),
):
    events, total = await crud.get_events_by_machine_id(
        machine_id=machine_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedEventsResponse(
        total=total, page=page, page_size=page_size, results=events
    )


@router.get("/by-equipment/{equipment_id}", response_model=PaginatedEventsResponse)
async def get_events_by_equipment_id(
    equipment_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    _: dict = Depends(require_permission("perm-view-events")),
):
    events, total = await crud.get_events_by_equipment_id(
        equipment_id=equipment_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedEventsResponse(
        total=total, page=page, page_size=page_size, results=events
    )


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: str,
    _: dict = Depends(require_permission("perm-view-events")),
):
    event = await crud.get_event_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return event
