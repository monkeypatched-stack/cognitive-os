"""SittingFace router — somatic chart management endpoints.

Extracted from main.py so somatic chart operations are a versioned,
auth-gated module within the SittingFace subsystem.

All endpoints require authentication via require_permission.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent

import logging

logger = logging.getLogger("agentos.sittingface")

router = APIRouter()


@router.get("/somatic/charts", tags=["SittingFace"])
async def list_somatic_charts(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-somatic")),
):
    """List all loaded somatic charts."""
    compiler = getattr(request.app.state, "somatic_compiler", None)
    if not compiler:
        return JSONResponse(
            status_code=503,
            content={"error": "SittingFace not initialized", "charts": []},
        )
    return {
        **compiler.summary(),
        # compiler.summary()'s chart_names is a flat, unlabeled list --
        # relying on callers to know it's silently ordered module/
        # capability/agent to recover each chart's type is fragile, so
        # this adds the type-labeled form alongside it.
        "charts": [{"name": c.name, "chart_type": c.chart_type} for c in compiler.charts],
    }


@router.get("/somatic/prompts", tags=["SittingFace"])
async def list_somatic_prompts(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-somatic")),
):
    """List compiled prompts from somatic charts."""
    compiler = getattr(request.app.state, "somatic_compiler", None)
    if not compiler:
        return JSONResponse(
            status_code=503,
            content={"error": "SittingFace not initialized", "prompts": []},
        )
    return {
        "prompts": [
            {
                "chart": p.chart_name,
                "preamble": p.preamble,
                "steps": p.cot_steps,
                "review_gate": p.review_gate,
                "constraints": p.constraints,
            }
            for p in compiler.prompts
        ]
    }


@router.get("/somatic/capabilities", tags=["SittingFace"])
async def list_somatic_capabilities(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-somatic")),
):
    """List capabilities registered from somatic charts."""
    compiler = getattr(request.app.state, "somatic_compiler", None)
    # bootstrap.py's init_sittingface(app, app.state._wolverine, ...) is the
    # only place "runtime" is ever set up for this subsystem -- app.state
    # itself has no "runtime" attribute at all (confirmed: no assignment
    # anywhere in the codebase), so this always read None and made both
    # endpoints below permanently return 503 regardless of whether
    # SittingFace was otherwise ready.
    runtime = getattr(request.app.state, "_wolverine", None)
    if not compiler or not runtime:
        return JSONResponse(status_code=503, content={"capabilities": []})
    return {
        "capabilities": list(runtime._capabilities.keys()),
        "from_charts": [c.name for c in compiler.charts if c.chart_type == "capability"],
        # Real chart -> registered-capability-name pairing, the exact
        # name/platform resolution _chart_to_capability() itself uses
        # (cap_data.get("name", chart.name)) -- "from_charts" above only
        # ever gave chart names, never which live capability name each one
        # actually became, so nothing could draw a real edge between them.
        "chart_capabilities": [
            {
                "chart": c.name,
                "capability_name": c.values.get("capability", {}).get("name", c.name),
                "platform": c.values.get("capability", {}).get("platform", "generic"),
            }
            for c in compiler.charts
            if c.chart_type == "capability"
        ],
    }


@router.post("/somatic/recompile", tags=["SittingFace"])
@idempotent("sittingface.recompile_somatic")
async def recompile_somatic(
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-somatic")),
):
    """Recompile all somatic charts and re-register capabilities into the Runtime."""
    compiler = getattr(request.app.state, "somatic_compiler", None)
    runtime = getattr(request.app.state, "_wolverine", None)  # app.state has no "runtime" attribute
    if not compiler or not runtime:
        return JSONResponse(status_code=503, content={"error": "SittingFace not initialized"})

    logger.info("[somatic/recompile] user=%r triggered recompile", user_id)
    compiler.charts.clear()
    compiler.prompts.clear()
    charts = compiler.load_all()
    prompts = compiler.compile_prompts()
    cap_names = compiler.register_capabilities(runtime)

    return {
        "status": "recompiled",
        "charts": len(charts),
        "capabilities_registered": len(cap_names),
        "prompts_compiled": len(prompts),
    }
