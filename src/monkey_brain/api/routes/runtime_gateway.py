"""Runtime API — introspection into the runtime system.

GET /runtime              — runtime overview
GET /runtime/status       — health status of all runtimes
GET /runtime/health       — detailed component health
GET /runtime/configuration — runtime configuration
GET /runtime/metrics      — performance metrics
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import (
    RuntimeResponse,
    RuntimeStatusResponse,
    RuntimeHealthResponse,
    RuntimeConfigurationResponse,
    RuntimeMetricsResponse,
)

logger = logging.getLogger("agentos.gateway.runtime")
router = APIRouter()


def _get_runtime(request: Request, name: str) -> Any:
    selector = getattr(getattr(request.app.state, "kernel", None), "runtime_selector", None)
    if selector is not None:
        try:
            runtime_id = name.removesuffix("_runtime")
            return selector.select(runtime_id)
        except LookupError:
            logger.debug("_get_runtime: suppressed exception", exc_info=True)
    return getattr(request.app.state, name, None)


@router.get("/runtime", response_model=RuntimeResponse, tags=["Runtime"])
async def get_runtime(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-runtime")),
) -> RuntimeResponse:
    cr = _get_runtime(request, "cognitive_runtime")
    components = {}
    if cr is not None:
        health = cr.health if hasattr(cr, "health") else {}
        components = health.get("components", {})
    return RuntimeResponse(
        name="cognitive",
        status="healthy" if cr else "offline",
        components=components,
    )


@router.get("/runtime/status", response_model=RuntimeStatusResponse, tags=["Runtime"])
async def get_runtime_status(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-runtime")),
) -> RuntimeStatusResponse:
    cr = _get_runtime(request, "cognitive_runtime")
    sr = _get_runtime(request, "simulation_runtime")
    comp = _get_runtime(request, "comparator_runtime")
    return RuntimeStatusResponse(
        status="healthy" if cr else "degraded",
        cognitive="ready" if cr else "offline",
        simulation="ready" if sr else "offline",
        comparator="ready" if comp else "offline",
        society=("ready" if getattr(request.app.state, "planetary_runtime", None) else "offline"),
    )


@router.get("/runtime/health", response_model=RuntimeHealthResponse, tags=["Runtime"])
async def get_runtime_health(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-runtime")),
) -> RuntimeHealthResponse:
    cr = _get_runtime(request, "cognitive_runtime")
    components = {}
    overall = "healthy"
    if cr is not None and hasattr(cr, "health"):
        h = cr.health
        overall = h.get("status", "healthy")
        components = h.get("components", {})
    return RuntimeHealthResponse(overall=overall, components=components)


@router.get(
    "/runtime/configuration",
    response_model=RuntimeConfigurationResponse,
    tags=["Runtime"],
)
async def get_runtime_configuration(
    user_id: str = Depends(require_permission("perm-view-runtime")),
) -> RuntimeConfigurationResponse:
    from src.monkey_brain.api.dependencies import auth_required

    return RuntimeConfigurationResponse(
        auth_required=auth_required(),
        rate_limit_rps=float(os.getenv("RATE_LIMIT_RPS", "100")),
        cors_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    )


@router.get("/runtime/metrics", response_model=RuntimeMetricsResponse, tags=["Runtime"])
async def get_runtime_metrics(
    user_id: str = Depends(require_permission("perm-view-runtime")),
) -> RuntimeMetricsResponse:
    return RuntimeMetricsResponse()
