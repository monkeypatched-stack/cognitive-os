"""Metadata endpoints — predicates, pipelines, workloads.

GET /metadata/predicates  — list unique preconditions across capabilities
GET /metadata/pipelines   — list available execution pipelines
GET /metadata/workloads   — list workload capabilities
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.monkey_brain.api.dependencies import require_permission

logger = logging.getLogger("agentos.metadata_api")

router = APIRouter()


def _get_bus(runtime: Any) -> Any:
    """Extract the CapabilityBus from the runtime, or None."""
    if runtime is None:
        return None
    bus = getattr(runtime, "_bus", None)
    if bus is not None:
        return bus
    return getattr(runtime, "capability_bus", None)


@router.get("/metadata/predicates", tags=["Metadata"])
async def list_predicates(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-agents")),
) -> JSONResponse:
    """List all unique preconditions across registered capabilities."""
    runtime = getattr(request.app.state, "runtime", None)
    bus = _get_bus(runtime)
    if bus is None:
        return JSONResponse({"predicates": [], "total": 0})

    predicates: set[str] = set()
    for desc in bus.list_capabilities():
        predicates.update(desc.preconditions)

    return JSONResponse(
        {
            "predicates": sorted(predicates),
            "total": len(predicates),
        }
    )


@router.get("/metadata/pipelines", tags=["Metadata"])
async def list_pipelines(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-agents")),
) -> JSONResponse:
    """List available execution pipelines from capabilities that enable others."""
    runtime = getattr(request.app.state, "runtime", None)
    bus = _get_bus(runtime)
    if bus is None:
        return JSONResponse({"pipelines": [], "total": 0})

    pipelines = []
    for desc in bus.list_capabilities():
        if desc.effects.enable:
            pipelines.append(
                {
                    "name": desc.name,
                    "enables": desc.effects.enable,
                    "modality": desc.modality.value,
                }
            )

    return JSONResponse(
        {
            "pipelines": pipelines,
            "total": len(pipelines),
        }
    )


@router.get("/metadata/workloads", tags=["Metadata"])
async def list_workloads(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-agents")),
) -> JSONResponse:
    """List workload-related capabilities."""
    runtime = getattr(request.app.state, "runtime", None)
    bus = _get_bus(runtime)
    if bus is None:
        return JSONResponse({"workloads": [], "total": 0})

    workloads = []
    for desc in bus.list_capabilities():
        if any(kw in desc.name.lower() for kw in ("workload", "work_order", "batch", "pipeline")):
            workloads.append(
                {
                    "name": desc.name,
                    "modality": desc.modality.value,
                    "confidence": desc.confidence,
                    "preconditions": desc.preconditions,
                }
            )

    return JSONResponse(
        {
            "workloads": workloads,
            "total": len(workloads),
        }
    )
