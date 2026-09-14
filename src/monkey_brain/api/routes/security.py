"""Security admin API — real, queryable violation records and
capability-bus resolution.

GET /security/violations        — persisted auth-denial records
GET /capability-bus/resolve      — which registry (capability/agent/
                                    provider) owns a given name
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from src.monkey_brain.api.dependencies import require_permission

logger = logging.getLogger("agentos.gateway.security")
router = APIRouter()


@router.get("/security/violations", tags=["Security"])
async def get_security_violations(
    request: Request,
    limit: int = 100,
    subject: str | None = None,
    user_id: str = Depends(require_permission("perm-view-discovery")),
) -> dict:
    """Real persisted denial records (violation_store.py), most-recent
    first. Previously this signal was fire-and-forget in-memory only
    (api/dependencies.py's _record_failure_and_check_pattern) — this is
    the durable store that replaced the console's own honest
    "no persisted store exists" gap note."""
    from src.monkey_brain.kernel.pipeline.violation_store import list_violations

    records = list_violations(limit=limit, subject=subject)
    return {"violations": records, "count": len(records)}


@router.get("/capability-bus/resolve", tags=["Security"])
async def resolve_via_capability_bus(
    name: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-discovery")),
) -> dict:
    """Which of the three previously-disconnected registries
    (Wolverine capability, local/Broca/provider agent, or neither)
    actually owns `name` — the real routing CapabilityBus now performs,
    wired at boot in kernel.py's _phase_broca."""
    bus = getattr(request.app.state, "_capability_bus", None)
    if bus is None:
        return {
            "name": name,
            "found": False,
            "source": "",
            "error": "capability bus not wired",
        }
    return bus.resolve(name)
