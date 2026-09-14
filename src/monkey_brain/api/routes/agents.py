"""Agents search endpoints.

GET /agents              — search registered agents (local + NANDA remote)
GET /agents/{type}       — get a single agent by type
POST /agents/discover    — trigger NANDA discovery for a capability/domain
GET /agents/bus/resolve  — resolve agent for a capability via CapabilityBus
POST /execute-direct     — execute via capability classification (no agent name needed)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.agents_api")

router = APIRouter()


# ---------------------------------------------------------------------------
# Agent search
# ---------------------------------------------------------------------------


@router.get("/agents", tags=["Agents"])
async def search_agents(
    request: Request,
    type: str | None = Query(None, description="Filter by agent_type"),
    domain: str | None = Query(None, description="Filter by domain (software_engineering, manufacturing, ...)"),
    capability: str | None = Query(None, description="Filter by capability keyword in description"),
    include_remote: bool = Query(True, description="Include NANDA-discovered remote agents"),
    user_id: str = Depends(require_permission("perm-view-agents")),
) -> JSONResponse:
    """Search registered agents by type, domain, or capability keyword.

    Returns local agents from BrocaAgentRegistry.
    When include_remote=true and NANDA_REGISTRY_URL is set, also queries NANDA
    for remote agents matching the filters.
    """
    local = _local_agents(type_filter=type, capability_filter=capability)

    remote: list[dict[str, Any]] = []
    if include_remote:
        remote = await _nanda_agents(
            agent_type=type,
            domain=domain,
            capability=capability,
        )

    seen = {a["agent_type"] for a in local}
    merged = local + [r for r in remote if r["agent_type"] not in seen]

    if domain:
        merged = [a for a in merged if domain in a.get("domains", []) or a.get("source") == "local"]

    return JSONResponse(
        {
            "agents": merged,
            "total": len(merged),
            "local": len(local),
            "remote": len(remote),
            "filters": {"type": type, "domain": domain, "capability": capability},
        }
    )


@router.get("/agents/{agent_type}", tags=["Agents"])
async def get_agent(
    request: Request,
    agent_type: str,
    user_id: str = Depends(require_permission("perm-view-agents")),
) -> JSONResponse:
    """Get a single agent by type. Checks local registry first, then NANDA."""
    try:
        from broca.registry import get_registry

        registry = get_registry()
        agent = registry.discover(agent_type)
        if agent is not None:
            return JSONResponse(
                {
                    "agent_type": agent.agent_type,
                    "description": agent.description,
                    "source": "local",
                    "feedback": agent.feedback() if callable(agent.feedback) else None,
                }
            )
    except Exception as exc:
        logger.warning("Local agent lookup failed: %s", exc)

    remote = await _nanda_agents(agent_type=agent_type)
    if remote:
        return JSONResponse({"agent": remote[0], "source": "nanda"})

    return JSONResponse({"error": f"agent_type={agent_type!r} not found"}, status_code=404)


@router.post("/agents/{agent_type}/execute", tags=["Agents"])
@idempotent("agents.execute_agent")
async def execute_agent(
    request: Request,
    agent_type: str,
    user_id: str = Depends(require_permission("perm-execute-action")),
) -> JSONResponse:
    """Execute an agent with a question. Uses middleware for state, formatting, serialization."""
    import time

    t0 = time.monotonic()
    try:
        body = await request.json()
    except Exception:
        body = {}

    question = body.get("question", "")
    state = body.get("state", {})
    state.setdefault("question", question)

    try:
        from src.monkey_brain.runtime.agent_resolver import get_resolver
        from src.monkey_brain.runtime.agent_middleware import AgentMiddleware
        from src.monkey_brain.kernel.society.legacy_adapters import AgentRuntimeAdapter

        resolver = get_resolver()
        agent = await resolver.resolve(agent_type)
        if agent is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": f"Could not resolve agent {agent_type!r}",
                    "agent_type": agent_type,
                },
            )

        # Wrap with middleware — returns CapabilityResult. AgentRuntimeAdapter
        # marks this as the one global, non-actor-scoped agent-execution
        # surface reached outside Planet/Society/Actor (Runtime Encapsulation
        # Refactor, Phase 5) — transparent passthrough, no behavior change.
        middleware = AgentRuntimeAdapter(AgentMiddleware(agent_type))
        capability_result = await middleware.execute(agent, state)

        # Serialize CapabilityResult to JSON
        elapsed_ms = (time.monotonic() - t0) * 1000
        response_dict = capability_result.to_dict()
        response_dict["agent_type"] = agent_type
        response_dict["elapsed_ms"] = round(elapsed_ms, 2)

        return JSONResponse(response_dict)

    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.error("Agent execution failed for %s: %s", agent_type, e)
        return JSONResponse(
            status_code=500,
            content={
                "error": str(e),
                "agent_type": agent_type,
                "elapsed_ms": round(elapsed_ms, 2),
            },
        )


@router.post("/agents/discover", tags=["Agents"])
@idempotent("agents.discover_agents")
async def discover_agents(
    request: Request,
    capability: str | None = Query(None),
    domain: str | None = Query(None),
    agent_type: str | None = Query(None),
    user_id: str = Depends(require_permission("perm-manage-agents")),
) -> JSONResponse:
    """Trigger a live NANDA discovery query and cache results in BrocaRegistry."""
    discovered = await _nanda_agents(
        agent_type=agent_type,
        domain=domain,
        capability=capability,
    )

    registered: list[str] = []
    try:
        from broca.registry import get_registry
        from broca.agents.nanda import NANDAProxyAgent

        registry = get_registry()
        for card in discovered:
            proxy = NANDAProxyAgent(card)
            if not registry.discover(proxy.agent_type):
                registry.register(proxy)
                registered.append(proxy.agent_type)
    except Exception as exc:
        logger.warning("NANDA proxy registration failed: %s", exc)

    return JSONResponse(
        {
            "discovered": len(discovered),
            "registered": registered,
            "agents": discovered,
        }
    )


@router.post("/execute-direct", tags=["Agents"])
@idempotent("agents.execute_direct")
async def execute_direct(
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-action")),
) -> JSONResponse:
    """Execute directly via capability classification — no agent name needed.

    The runtime classifies the capability, routes it, resolves implementation,
    and executes. Users never need to specify agent names.
    """
    import time

    t0 = time.monotonic()
    try:
        body = await request.json()
    except Exception:
        body = {}

    question = body.get("question", "")
    if not question:
        return JSONResponse(status_code=400, content={"error": "question is required"})

    try:
        from src.monkey_brain.runtime.agent_middleware import AgentMiddleware
        from src.monkey_brain.kernel.society.legacy_adapters import AgentRuntimeAdapter

        middleware = AgentRuntimeAdapter(AgentMiddleware())
        result = await middleware.execute(state={"question": question})

        elapsed_ms = (time.monotonic() - t0) * 1000

        # Convert to JSON-safe dict
        if hasattr(result, "to_dict"):
            response_dict = result.to_dict()
        else:
            response_dict = {"answer": str(result), "success": True}

        response_dict["elapsed_ms"] = round(elapsed_ms, 2)
        return JSONResponse(response_dict)

    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.error("Direct execution failed: %s", e)
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "elapsed_ms": round(elapsed_ms, 2)},
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _local_agents(
    type_filter: str | None = None,
    capability_filter: str | None = None,
) -> list[dict[str, Any]]:
    try:
        from broca.registry import get_registry

        registry = get_registry()
        all_agents = registry.list_agents()
        result = []
        for agent_type, description in all_agents.items():
            if type_filter and type_filter.lower() not in agent_type.lower():
                continue
            if capability_filter and capability_filter.lower() not in description.lower():
                continue
            result.append(
                {
                    "agent_type": agent_type,
                    "description": description,
                    "source": "local",
                }
            )
        return result
    except Exception as exc:
        logger.warning("Local agents query failed: %s", exc)
        return []


async def _nanda_agents(
    agent_type: str | None = None,
    domain: str | None = None,
    capability: str | None = None,
) -> list[dict[str, Any]]:
    try:
        from cerebellum.capabilities.agent.nanda import NANDACapability

        cap = NANDACapability()
        if not cap._available:
            return []
        query: dict[str, str] = {}
        if agent_type:
            query["agent_type"] = agent_type
        if domain:
            query["domain"] = domain
        if capability:
            query["capability"] = capability
        result = await cap.execute({"operation": "discover", **query})
        agents = result.get("agents", [])
        for a in agents:
            a["source"] = "nanda"
        return agents
    except Exception as exc:
        logger.debug("NANDA query failed: %s", exc)
        return []


def _get_runtime(request: Request) -> Any:
    """See capabilities.py::_get_runtime's own docstring — same stale
    app.state.runtime fix, same real target (app.state.cognitive_runtime.
    wolverine)."""
    direct = getattr(request.app.state, "runtime", None)
    if direct is not None:
        return direct
    cognitive_runtime = getattr(request.app.state, "cognitive_runtime", None)
    return getattr(cognitive_runtime, "wolverine", None)


def _get_bus(runtime: Any) -> Any:
    """Extract the CapabilityBus from the runtime, or None."""
    if runtime is None:
        return None
    bus = getattr(runtime, "_bus", None)
    if bus is not None:
        return bus
    return getattr(runtime, "capability_bus", None)


# ---------------------------------------------------------------------------
# Bus-based agent resolution
# ---------------------------------------------------------------------------


@router.get("/agents/bus/resolve", tags=["Agents"])
async def resolve_agent_for_capability(
    request: Request,
    capability: str = Query(..., description="Capability name to resolve agent for"),
    user_id: str = Depends(require_permission("perm-view-agents")),
) -> JSONResponse:
    """Resolve which agent serves a given capability via the CapabilityBus."""
    runtime = _get_runtime(request)
    bus = _get_bus(runtime)
    if bus is None:
        return JSONResponse({"error": "CapabilityBus not available"}, status_code=503)

    agent_type = bus.agent_bus.resolve_agent(capability)
    if agent_type is None:
        return JSONResponse(
            {
                "capability": capability,
                "agent": None,
                "error": f"No agent registered for capability '{capability}'",
            }
        )

    caps = bus.agent_bus.get_capabilities_for_agent(agent_type)
    provider = bus.agent_bus.get_provider_for(agent_type, capability)
    return JSONResponse(
        {
            "capability": capability,
            "agent": agent_type,
            "provider": provider,
            "all_capabilities": caps,
        }
    )
