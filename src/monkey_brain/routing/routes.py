"""Data Routing API routes — exposes routing policy and resolution.

GET /api/v1/agentos/data-routing/table
GET /api/v1/agentos/data-routing/entry/{capability}/{entity}
GET /api/v1/agentos/data-routing/summary
POST /api/v1/agentos/data-routing/resolve
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.monkey_brain.api.dependencies import require_permission

logger = logging.getLogger("agentos.data_routing.route")

router = APIRouter()


class ResolveRequest(BaseModel):
    capability: str = Field(..., description="Business capability (e.g., customer, order)")
    entity: str = Field(..., description="Entity type (e.g., Customer, Order)")
    entity_id: str | None = Field(default=None, description="Optional entity ID")
    filters: dict | None = Field(default=None, description="Optional query filters")


def _get_middleware(request: Request):
    return getattr(request.app.state, "data_routing", None)


@router.get("/data-routing/table", tags=["Data Routing"])
async def routing_table(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-observability")),
):
    """Return the full routing policy table."""
    middleware = _get_middleware(request)
    if not middleware:
        return JSONResponse(status_code=503, content={"error": "Data routing not initialized"})
    table = middleware.get_routing_table()
    return {"entries": [e.to_dict() for e in table], "total": len(table)}


@router.get("/data-routing/entry/{capability}/{entity}", tags=["Data Routing"])
async def routing_entry(
    capability: str,
    entity: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-observability")),
):
    """Get routing entry for a specific capability+entity."""
    middleware = _get_middleware(request)
    if not middleware:
        return JSONResponse(status_code=503, content={"error": "Data routing not initialized"})
    entry = middleware.get_entry(capability, entity)
    if entry is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"No routing entry for {capability}:{entity}"},
        )
    return entry.to_dict()


@router.get("/data-routing/summary", tags=["Data Routing"])
async def routing_summary(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-observability")),
):
    """Return routing middleware summary."""
    middleware = _get_middleware(request)
    if not middleware:
        return JSONResponse(status_code=503, content={"error": "Data routing not initialized"})
    return middleware.summary()


@router.post("/data-routing/resolve", tags=["Data Routing"])
async def resolve_entity(
    body: ResolveRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-observability")),
):
    """Resolve an entity query to the optimal data source."""
    middleware = _get_middleware(request)
    if not middleware:
        return JSONResponse(status_code=503, content={"error": "Data routing not initialized"})
    result = await middleware.resolve(
        capability=body.capability,
        entity=body.entity,
        entity_id=body.entity_id,
        filters=body.filters,
    )
    return result
