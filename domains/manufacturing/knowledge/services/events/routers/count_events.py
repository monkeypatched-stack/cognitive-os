from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from services.common.auth import require_permission
from services.events.helpers import events as crud

router = APIRouter()


class PaginatedCountEventsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[dict[str, Any]] = Field(default_factory=list)


COUNT_EVENT_PATHS = [
    "count-events",
    "cycle-count-events",
    "return-count-updates",
    "consumed-count-events",
    "machine-equipment-output-count-events",
    "machine-equipment-output-defect-count-events",
    "workstation-rejection-count-events",
    "product-count-events",
    "product-defect-count-events",
    "machine-part-count-events",
    "machine-part-defect-count-events",
    "raw-material-consumption-events",
    "raw-material-defect-count-events",
    "workstation-product-count-events",
    "workstation-product-defect-count-events",
    "workstation-machine-part-count-events",
    "workstation-machine-part-defect-count-events",
    "workstation-raw-material-consumption-events",
    "workstation-raw-material-defect-count-events",
    "workstation-component-count-events",
    "workstation-component-defect-count-events",
    "warehouse-product-count-events",
    "warehouse-product-defect-count-events",
    "warehouse-machine-part-count-events",
    "warehouse-machine-part-defect-count-events",
    "warehouse-raw-material-consumption-events",
    "warehouse-raw-material-defect-count-events",
    "warehouse-component-count-events",
    "warehouse-component-defect-count-events",
    "warehouse-cycle-count-events",
]


async def _list_count_events(
    path: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    scope: str | None = Query(None),
    category: str | None = Query("Count Event"),
    _: dict = Depends(require_permission("perm-view-events")),
):
    events, total = await crud.get_count_events(
        path=path,
        page=page,
        page_size=page_size,
        scope=scope,
        category=category,
    )
    return PaginatedCountEventsResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=events,
    )


def _count_events_endpoint(path: str):
    async def endpoint(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        scope: str | None = Query(None),
        category: str | None = Query("Count Event"),
        current_user: dict = Depends(require_permission("perm-view-events")),
    ):
        return await _list_count_events(
            path=path,
            page=page,
            page_size=page_size,
            scope=scope,
            category=category,
            _=current_user,
        )

    return endpoint


for path in COUNT_EVENT_PATHS:
    router.add_api_route(
        f"/{path}/",
        _count_events_endpoint(path),
        methods=["GET"],
        response_model=PaginatedCountEventsResponse,
    )
    router.add_api_route(
        f"/{path}",
        _count_events_endpoint(path),
        methods=["GET"],
        response_model=PaginatedCountEventsResponse,
    )
