"""Verification API — world validation.

Gate 3 (ADR-010): wraps kernel/validation/world_validator.py::
validate_world() — ten categories (geography tree, society hierarchy,
presence consistency, membership consistency, inventory consistency,
graph integrity, orphaned nodes, forbidden cycles, duplicate identifiers,
referential integrity). Read-only, never mutates anything.

POST /verify/world        — canonical entry point (Gate 3)
POST /verify               — same report (kept for backward compatibility)
GET  /verify/invariants    — same report (GET alias, since it's read-only)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import VerifyReportResponse
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.gateway.verify")
router = APIRouter()


def _get_planetary_runtime(request: Request) -> Any:
    return getattr(request.app.state, "planetary_runtime", None)


async def _run_verification(request: Request) -> dict[str, Any]:
    from src.monkey_brain.kernel.society.verification import verify_world_invariants

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    return verify_world_invariants(pr)


@router.post("/verify/world", tags=["Verify"], response_model=VerifyReportResponse)
@idempotent("verify.verify_world_canonical")
async def verify_world_canonical(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    return await _run_verification(request)


@router.post("/verify", tags=["Verify"], response_model=VerifyReportResponse)
@idempotent("verify.verify_world")
async def verify_world(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    return await _run_verification(request)


@router.get("/verify/invariants", tags=["Verify"], response_model=VerifyReportResponse)
async def get_invariants(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    return await _run_verification(request)
