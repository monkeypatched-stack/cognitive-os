"""Capabilities search endpoints.

GET /capabilities              — search registered capabilities
GET /capabilities/{name}       — get a single capability by name
GET /capabilities/bus/search   — enhanced search via CapabilityBus (modality, precondition)
GET /capabilities/bus/graph    — agent→capability graph
GET /capabilities/bus/graph/mermaid — Mermaid diagram of the graph
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from src.monkey_brain.api.dependencies import require_permission

logger = logging.getLogger("agentos.capabilities_api")

router = APIRouter()


def _get_runtime(request: Request) -> Any:
    """The real Wolverine Runtime (kernel/runtime/runtime.py, the one
    Cerebellum's load_all_providers() actually registers capabilities
    onto — see providers.py) lives at app.state.cognitive_runtime.
    wolverine (Kernel refactor, api/bootstrap.py::init_runtime +
    kernel/cognitive_layers/runtime_bootstrap.py's `rt.wolverine =
    getattr(app.state, "_wolverine", None)`). `app.state.runtime` is a
    stale attribute name nothing sets anymore — every call site below
    used to read it directly, so _capability_list()/get_capability()
    always silently fell through to the Broca-agent-description fallback
    (295 near-duplicate "capabilities") and never saw the real, smaller,
    provider-backed capability set Wolverine actually holds."""
    direct = getattr(request.app.state, "runtime", None)
    if direct is not None:
        return direct
    cognitive_runtime = getattr(request.app.state, "cognitive_runtime", None)
    return getattr(cognitive_runtime, "wolverine", None)


# ---------------------------------------------------------------------------
# Capability search
# ---------------------------------------------------------------------------


@router.get("/capabilities", tags=["Capabilities"])
async def search_capabilities(
    request: Request,
    name: str | None = Query(None, description="Filter by capability name (substring match)"),
    domain: str | None = Query(None, description="Filter by domain keyword in name"),
    provider: str | None = Query(None, description="Filter by provider: nanda, openclaw, n8n, llm"),
    user_id: str = Depends(require_permission("perm-view-agents")),
) -> JSONResponse:
    """Search capabilities registered in the MonkeyBrain runtime."""
    runtime = _get_runtime(request)
    capabilities = _capability_list(runtime, name_filter=name, domain_filter=domain, provider_filter=provider)
    return JSONResponse(
        {
            "capabilities": capabilities,
            "total": len(capabilities),
            "filters": {"name": name, "domain": domain, "provider": provider},
        }
    )


@router.get("/capabilities/{cap_name}", tags=["Capabilities"])
async def get_capability(
    request: Request,
    cap_name: str,
    user_id: str = Depends(require_permission("perm-view-agents")),
) -> JSONResponse:
    """Get a single capability by name."""
    runtime = _get_runtime(request)
    agents: dict[str, Any] = getattr(runtime, "_capabilities", {}) if runtime is not None else {}
    cap = agents.get(cap_name)
    if cap is None:
        try:
            from broca.registry import get_registry

            registry = get_registry()
            cap = registry.list_agents().get(cap_name)
        except Exception:
            cap = None
    if cap is None:
        return JSONResponse({"error": f"capability={cap_name!r} not found"}, status_code=404)
    return JSONResponse(_capability_card(cap_name, cap))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _capability_list(
    runtime: Any,
    name_filter: str | None = None,
    domain_filter: str | None = None,
    provider_filter: str | None = None,
) -> list[dict[str, Any]]:
    agents: dict[str, Any] = {}
    if runtime is not None:
        agents = getattr(runtime, "_capabilities", {})
    if not agents:
        try:
            from broca.registry import get_registry

            registry = get_registry()
            agents = registry.list_agents()
        except Exception:
            logger.debug("_capability_list: suppressed exception", exc_info=True)
    result = []
    for name, description in agents.items():
        if name_filter and name_filter.lower() not in name.lower():
            continue
        if domain_filter and domain_filter.lower() not in name.lower():
            continue
        if provider_filter and provider_filter.lower() not in name.lower():
            continue
        result.append(_capability_card(name, description))
    return result


def _capability_card(name: str, cap: Any) -> dict[str, Any]:
    if isinstance(cap, dict):
        return {
            "name": name,
            "type": "agent",
            "available": True,
            "description": cap.get("description", ""),
        }
    if isinstance(cap, str):
        return {
            "name": name,
            "type": "agent",
            "available": True,
            "description": cap,
        }
    return {
        "name": name,
        "type": type(cap).__name__,
        "available": getattr(cap, "_available", True),
        "description": getattr(cap, "description", ""),
    }


def _get_bus(runtime: Any) -> Any:
    """Extract the CapabilityBus from the runtime, or None."""
    if runtime is None:
        return None
    bus = getattr(runtime, "_bus", None)
    if bus is not None:
        return bus
    return getattr(runtime, "capability_bus", None)


# ---------------------------------------------------------------------------
# Bus-based capability search
# ---------------------------------------------------------------------------


@router.get("/capabilities/bus/search", tags=["Capabilities"])
async def search_capabilities_bus(
    request: Request,
    name: str | None = Query(None, description="Filter by capability name (substring match)"),
    modality: str | None = Query(None, description="Filter by modality (local, rest, grpc, ...)"),
    precondition: str | None = Query(None, description="Filter by precondition keyword"),
    user_id: str = Depends(require_permission("perm-view-agents")),
) -> JSONResponse:
    """Search capabilities via CapabilityBus with modality and precondition filters."""
    runtime = _get_runtime(request)
    bus = _get_bus(runtime)
    if bus is None:
        return JSONResponse({"capabilities": [], "total": 0})

    results: list[dict[str, Any]] = []
    for desc in bus.list_capabilities():
        if name and name.lower() not in desc.name.lower():
            continue
        if modality and modality.lower() != desc.modality.value.lower():
            continue
        if precondition and not any(precondition.lower() in p.lower() for p in desc.preconditions):
            continue
        results.append(
            {
                "name": desc.name,
                "modality": desc.modality.value,
                "confidence": desc.confidence,
                "preconditions": desc.preconditions,
                "produces": desc.produces,
            }
        )

    return JSONResponse(
        {
            "capabilities": results,
            "total": len(results),
            "filters": {
                "name": name,
                "modality": modality,
                "precondition": precondition,
            },
        }
    )


@router.get("/capabilities/bus/graph", tags=["Capabilities"])
async def get_capability_graph(
    request: Request,
    fmt: str = Query("dict", description="Format: dict or mermaid"),
    user_id: str = Depends(require_permission("perm-view-agents")),
) -> JSONResponse:
    """Get the agent→capability→preconditions/effects graph."""
    runtime = _get_runtime(request)
    bus = _get_bus(runtime)
    if bus is None:
        return JSONResponse({"error": "CapabilityBus not available"}, status_code=503)

    if fmt == "mermaid":
        return JSONResponse({"mermaid": bus.view_graph("mermaid")})
    return JSONResponse(bus.view_graph("dict"))
