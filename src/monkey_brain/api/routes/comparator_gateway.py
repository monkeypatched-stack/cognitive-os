"""Comparator API — delegate to existing ComparatorRuntime.

POST /compare/run            — compare predicted vs actual
POST /compare/epistemic-loss — compute epistemic loss
GET  /compare/history        — comparison history

Not mounted at the bare POST /compare — that path already belongs to
api/routes/predict.py's compare_sim_vs_query (a real plan->simulate->
execute->diff workflow the CLI's `monkeypatched compare`, the REPL, and
tests/benchmarks/test_performance.py all depend on). Both routers are
included under the same "/api/v1/agentos" prefix, so a bare POST /compare
here would silently shadow or be shadowed by that one depending on
router-registration order — /run avoids the collision entirely instead
of relying on the two ending up in the right order.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import (
    CompareRequest,
    CompareEpistemicLossRequest,
    CompareResponseGateway,
    CompareHistoryResponse,
)
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.gateway.compare")
router = APIRouter()


def _get_comparator_runtime(request: Request) -> Any:
    selector = getattr(getattr(request.app.state, "kernel", None), "runtime_selector", None)
    if selector is not None:
        try:
            return selector.select("comparator")
        except LookupError:
            logger.debug("_get_comparator_runtime: suppressed exception", exc_info=True)
    return getattr(request.app.state, "comparator_runtime", None)


@router.post("/compare/run", response_model=CompareResponseGateway, tags=["Compare"])
@idempotent("comparator_gateway.compare")
async def compare(
    body: CompareRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-compare")),
) -> CompareResponseGateway:
    cr = _get_comparator_runtime(request)
    if cr is None:
        raise HTTPException(status_code=503, detail="ComparatorRuntime not available")
    try:
        result = await cr.compare(body.predicted, body.actual)
        loss = getattr(result, "epistemic_loss", 0.0) if hasattr(result, "epistemic_loss") else 0.0
        details = result.to_dict() if hasattr(result, "to_dict") else {"raw": str(result)}
        return CompareResponseGateway(
            status="ok",
            loss=loss,
            details=details,
        )
    except Exception as e:
        logger.error("Comparison failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Comparison failed: {e}")


@router.post("/compare/epistemic-loss", response_model=CompareResponseGateway, tags=["Compare"])
@idempotent("comparator_gateway.compare_epistemic_loss")
async def compare_epistemic_loss(
    body: CompareEpistemicLossRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-compare")),
) -> CompareResponseGateway:
    cr = _get_comparator_runtime(request)
    if cr is None:
        raise HTTPException(status_code=503, detail="ComparatorRuntime not available")
    try:
        result = await cr.compare(body.predicted, body.actual)
        loss = getattr(result, "epistemic_loss", 0.0) if hasattr(result, "epistemic_loss") else 0.0
        return CompareResponseGateway(status="ok", loss=loss)
    except Exception as e:
        logger.error("Epistemic loss computation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Epistemic loss failed: {e}")


@router.get("/compare/history", response_model=CompareHistoryResponse, tags=["Compare"])
async def compare_history(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-compare")),
) -> CompareHistoryResponse:
    cr = _get_comparator_runtime(request)
    if cr is None:
        return CompareHistoryResponse()
    last = cr.last_comparison
    comparisons = [last] if last is not None else []
    return CompareHistoryResponse(comparisons=comparisons, total=len(comparisons))
