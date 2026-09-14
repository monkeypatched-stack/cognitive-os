"""Dashboard routes — backend-driven cognitive observation.

GET /api/v1/agentos/dashboard/current
GET /api/v1/agentos/dashboard/{run_id}

Returns a complete DashboardModel. No business logic in the frontend.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.dashboard.service import DashboardService

logger = logging.getLogger("agentos.dashboard.route")

router = APIRouter()


@router.get("/dashboard/current", tags=["Dashboard"])
async def dashboard_current(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-observability")),
):
    """Return the current cognitive state as a complete DashboardModel."""
    try:
        service = DashboardService()
        model = service.build()
        return model.to_dict()
    except Exception as e:
        logger.error("Dashboard build failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/dashboard/{run_id}", tags=["Dashboard"])
async def dashboard_for_run(
    run_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-observability")),
):
    """Return the cognitive state for a specific run_id."""
    try:
        service = DashboardService()
        model = service.build(run_id=run_id)
        return model.to_dict()
    except Exception as e:
        logger.error("Dashboard build failed for run %s: %s", run_id, e)
        return JSONResponse(status_code=500, content={"error": str(e)})
