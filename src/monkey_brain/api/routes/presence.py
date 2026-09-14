"""Presence API — read-only queries over PresenceTimeline.

New category (#4): where_is/history/who_is_in already existed as real,
tested kernel methods (MB-3053) but were never reachable over HTTP.

GET /presence                       — current occupancy, every actor
GET /presence/actors/{id}           — where_is(actor) — current location
GET /presence/spaces/{id}           — who_is_in(space) — current occupants
GET /presence/history/{actor}       — history(actor) — full presence timeline
GET /presence/history/space/{space} — every actor who has ever occupied this space
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import (
    OccupancyResponse,
    PresenceHistoryResponse,
    PresenceRecordResponse,
    SpaceOccupantsResponse,
    SpacePresenceHistoryResponse,
)

logger = logging.getLogger("agentos.gateway.presence")
router = APIRouter()


def _get_planetary_runtime(request: Request) -> Any:
    return getattr(request.app.state, "planetary_runtime", None)


def _pr(request: Request) -> Any:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    return pr


@router.get("/presence", tags=["Presence"], response_model=OccupancyResponse)
async def get_current_occupancy(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    pr = _pr(request)
    occupancy = pr.presence.current_occupancy()
    return {"occupancy": [{"actor_id": a, "space_id": s} for a, s in occupancy.items()]}


@router.get(
    "/presence/actors/{actor_id}",
    tags=["Presence"],
    response_model=PresenceRecordResponse,
)
async def where_is(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    pr = _pr(request)
    presence = pr.presence.current(actor_id)
    if presence is None:
        raise HTTPException(status_code=404, detail=f"no current presence for actor {actor_id!r}")
    return presence.to_dict()


@router.get(
    "/presence/spaces/{space_id}",
    tags=["Presence"],
    response_model=SpaceOccupantsResponse,
)
async def who_is_in(
    space_id: str,
    request: Request,
    timestamp: float | None = None,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    pr = _pr(request)
    occupant_ids = pr.presence.occupants(space_id, timestamp)
    return {
        "space_id": space_id,
        "timestamp": timestamp,
        "actor_ids": list(occupant_ids),
    }


@router.get(
    "/presence/history/{actor_id}",
    tags=["Presence"],
    response_model=PresenceHistoryResponse,
)
async def actor_presence_history(
    actor_id: str,
    request: Request,
    since: float | None = None,
    until: float | None = None,
    enriched: bool = False,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    """enriched=true joins in associated_societies/nearby_actors/
    goals_active_during per visit (PresenceTimeline.enriched_history) —
    opt-in since it's more expensive than the plain history() the default
    stays."""
    pr = _pr(request)
    if enriched:
        history = pr.presence.enriched_history(actor_id, since, until)
        return {"actor_id": actor_id, "history": history}
    history = pr.presence.history(actor_id, since, until)
    return {"actor_id": actor_id, "history": [p.to_dict() for p in history]}


@router.get(
    "/presence/history/space/{space_id}",
    tags=["Presence"],
    response_model=SpacePresenceHistoryResponse,
)
async def space_presence_history(
    space_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    """Every actor who has ever occupied space_id — scans every actor
    PlanetaryRuntime currently knows about (same "known actor" source
    verification.py's own checks use), not PresenceTimeline's private
    store directly, so this stays a pure, additive read at the API
    layer without reaching into PresenceTimeline's internals."""
    pr = _pr(request)
    entries = []
    seen_actor_ids: set[str] = set()
    for sr in pr.all_societies():
        for state in sr.all_actors():
            if state.actor_id in seen_actor_ids:
                continue
            seen_actor_ids.add(state.actor_id)
            for presence in pr.presence.history(state.actor_id):
                if presence.space_id == space_id:
                    entries.append(presence.to_dict())
    entries.sort(key=lambda e: e.get("start_time", 0))
    return {"space_id": space_id, "history": entries}
