"""State router — runtime, policy, persistence, and epistemic state snapshot."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.monkey_brain.api.dependencies import require_permission

logger = logging.getLogger("agentos.state")

router = APIRouter()


@router.get("/state", tags=["State"])
async def get_state(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-state")),
):
    """Current runtime state: runtime, policy, persistence, observability, epistemic."""
    try:
        runtime = getattr(request.app.state, "runtime", None)
        policy = getattr(request.app.state, "policy", None)
        pm = getattr(request.app.state, "persistence_manager", None)
        lemon = getattr(request.app.state, "lemon", None)
        kernel = getattr(request.app.state, "kernel", None)

        epistemic_state = None
        try:
            cognitive_kernel = getattr(request.app.state, "cognitive_kernel", None)
            if cognitive_kernel is not None:
                epistemic_state = cognitive_kernel.get_state()
        except Exception as e:
            logger.debug("Exception caught: %s", e)

        resource_manager = getattr(kernel, "resource_manager", None) if kernel else None

        return {
            "runtime": runtime.summary() if runtime else None,
            "policy": policy.summary() if policy else None,
            "persistence": pm.summary() if pm else None,
            "observability": lemon.summary() if lemon else None,
            "epistemic": epistemic_state,
            "resources": resource_manager.summary() if resource_manager else None,
        }
    except Exception as e:
        logger.error("[state] user=%r error: %s", user_id, e)
        return JSONResponse(status_code=500, content={"error": str(e)})


def _get_store():
    from src.monkey_brain.persistence.graph_store import get_graph_store_instance

    store = get_graph_store_instance()
    if store is None or not store.is_connected():
        return None
    return store


@router.get("/mesh", tags=["State"])
async def get_mesh(
    user_id: str = Depends(require_permission("perm-view-state")),
    limit: int = 10,
):
    """Layer 1 view — the Execution Mesh: reusable ExecutionGraph nodes,
    their mesh-level edges (SIMILAR_TO/COMPOSES/...), plus the top-ranked
    capabilities and workflows. Ranks refresh automatically after every
    executed run — see CognitiveRuntime._persist_execution_mesh. This view
    shows ONLY mesh nodes; drill into a workflow with /mesh/graph/{key} and
    into an agent with /mesh/agent/{name}.
    """
    try:
        store = _get_store()
        if store is None:
            return JSONResponse(status_code=503, content={"error": "GraphStore not connected"})
        return {
            "mesh": await store.get_execution_mesh_view(),
            "top_capabilities": await store.get_top_ranked_capabilities(limit=limit),
            "top_graphs": await store.get_top_ranked_execution_graphs(limit=limit),
        }
    except Exception as e:
        logger.error("[mesh] user=%r error: %s", user_id, e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/mesh/graph/{graph_key}", tags=["State"])
async def get_mesh_graph(
    graph_key: str,
    user_id: str = Depends(require_permission("perm-view-state")),
):
    """Layer 2 view — one Execution Graph: its Agents and their DEPENDS_ON
    execution ordering. Agents only; capabilities live one layer down."""
    try:
        store = _get_store()
        if store is None:
            return JSONResponse(status_code=503, content={"error": "GraphStore not connected"})
        return await store.get_execution_graph_view(graph_key)
    except Exception as e:
        logger.error("[mesh/graph] user=%r error: %s", user_id, e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/mesh/agent/{agent_name}", tags=["State"])
async def get_agent_capabilities(
    agent_name: str,
    user_id: str = Depends(require_permission("perm-view-state")),
):
    """Layer 3 view — one Agent's private Capability Graph: capabilities,
    the Providers that resolve them, and the Tools those providers expose."""
    try:
        store = _get_store()
        if store is None:
            return JSONResponse(status_code=503, content={"error": "GraphStore not connected"})
        return await store.get_agent_capability_graph(agent_name)
    except Exception as e:
        logger.error("[mesh/agent] user=%r error: %s", user_id, e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/mesh/ontology", tags=["State"])
async def get_ontology(
    user_id: str = Depends(require_permission("perm-view-state")),
):
    """Ontology view — OntologyConcept nodes and IS_A/PART_OF/SPECIALIZES/
    GENERALIZES relations, stored separately from all execution structure."""
    try:
        store = _get_store()
        if store is None:
            return JSONResponse(status_code=503, content={"error": "GraphStore not connected"})
        return await store.get_ontology_view()
    except Exception as e:
        logger.error("[mesh/ontology] user=%r error: %s", user_id, e)
        return JSONResponse(status_code=500, content={"error": str(e)})
