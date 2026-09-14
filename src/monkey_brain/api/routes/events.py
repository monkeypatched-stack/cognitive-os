"""Events API — populate the ContextStream, and (for physical disaster
events at a real Space) trigger a genuine evacuation.

World Graph Builder / Cognitive Reasoning separation (see commerce.py's
module docstring): an event is still world-building — it deterministically
mutates presence/ContextStream, it does not reason about anything.

POST /events — publish an event (fire, flood, promotion, flash_sale,
    power_failure, traffic, weather, strike, ...). Physical-disaster
    types given a space_id also evacuate every actor genuinely present
    there — the same real move_actor()/PresenceTimeline/MembershipGovernor
    chain kernel/society/movement_perturbation.py::MovementPerturbationEngine
    uses (MB-3055), just targeted at a specific Space instead of a
    randomly-picked one.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import EventCreateRequest, EventResponse
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.gateway.events")
router = APIRouter()

_EVACUATION_EVENT_TYPES = frozenset(
    {
        "fire",
        "flood",
        "structural_failure",
        "security_incident",
        "chemical_spill",
        "power_failure",
        "strike",
    }
)


def _get_planetary_runtime(request: Request) -> Any:
    return getattr(request.app.state, "planetary_runtime", None)


@router.post("/events", tags=["Events"], response_model=EventResponse)
@idempotent("events.create")
async def create_event(
    body: EventCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.society.context_stream import (
        ContextEvent,
        ContextEventType,
    )

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    event_type = body.type
    space_id = body.space_id
    description = body.description or f"Event: {event_type}"

    evacuated: list[dict[str, Any]] = []
    if event_type in _EVACUATION_EVENT_TYPES and space_id:
        evacuated = _evacuate_space(pr, space_id, event_type)

    payload: dict[str, Any] = {
        "type": event_type,
        "space_id": space_id,
        "evacuated": evacuated,
        **body.model_dump(),
    }
    if event_type == "fire" and space_id:
        # True Multi-Actor Coordination: a real evacuation is a real
        # world mutation a Society may need to react to (e.g. an
        # alternate warehouse's Society coordinating to cover for one
        # that just lost its on-site team) — same domain_event
        # convention ActionExecutor's domain_event_resolver already
        # uses for capability outcomes, just tagged directly here since
        # evacuation publishes through this route, not a capability.
        payload["domain_event"] = "WarehouseClosed"

    context_events_before = pr.context_stream.event_count
    event = pr.context_stream.publish(
        ContextEvent(
            event_type=ContextEventType.WORLD_UPDATE,
            description=description,
            payload=payload,
        )
    )

    # True Multi-Actor Coordination: propagation was previously only
    # reachable from execute_actor_request() (the /prompt path) — a
    # standalone world mutation published here, with no actor's own
    # request wrapping it, was never caught by any request's causal
    # window and could never trigger a Society reaction at all. Every
    # significant world mutation should be able to, not just actor-
    # initiated ones, so this route now drives the exact same
    # propagation engine directly.
    coordination_trace: list[dict[str, Any]] = []
    execution_scope: dict[str, Any] = {}
    if payload.get("domain_event"):
        # Same tick_lock execute_actor_request() holds around its own
        # _propagate_coordination() call — two independent coordination
        # cascades (a customer's own request and a standalone world
        # event like this one) must not run concurrently against the
        # same societies.
        async with pr._tick_lock:
            (
                propagated_actors,
                propagated_societies,
                termination_reason,
                domain_events_seen,
            ) = await pr._propagate_coordination(
                from_version=context_events_before,
                trace=coordination_trace,
                already_visited=set(),
            )
        execution_scope = {
            "societies_coordinated": len(propagated_societies),
            "actors_coordinated": len(propagated_actors),
            "propagation_steps": len(coordination_trace),
            "propagation_depth": max((s["depth"] for s in coordination_trace), default=0),
            "termination_reason": termination_reason,
            "domain_events_seen": sorted(domain_events_seen),
        }

    return {
        "success": True,
        "event": event.to_dict(),
        "evacuated": evacuated,
        "coordination_trace": coordination_trace,
        "execution_scope": execution_scope,
    }


def _evacuate_space(pr: Any, space_id: str, cause: str) -> list[dict[str, Any]]:
    """Targeted, deterministic version of MovementPerturbationEngine's
    own evacuation logic (MB-3055) — same real move_actor() chain, a
    specific Space instead of a randomly-picked occupied one."""
    from src.monkey_brain.kernel.geography.entity import GeographicEntityType

    occupants = pr.presence.occupants(space_id)
    if not occupants:
        return []

    space = pr.geo_registry.get(space_id)
    if space is None:
        return []
    destination_id = None
    if space.parent_id is not None:
        siblings = [
            e
            for e in pr.geo_registry.children_of(space.parent_id)
            if e.entity_type == GeographicEntityType.SPACE and e.entity_id != space_id
        ]
        if siblings:
            destination_id = siblings[0].entity_id
    if destination_id is None:
        others = [e for e in pr.geo_registry.all(GeographicEntityType.SPACE) if e.entity_id != space_id]
        destination_id = others[0].entity_id if others else None
    if destination_id is None:
        return []

    timestamp = time.time()
    evacuated = []
    for actor_id in occupants:
        moved = pr.move_actor(actor_id, destination_id, activity=f"evacuating {cause}")
        if moved:
            evacuated.append(
                {
                    "actor_id": actor_id,
                    "from_space_id": space_id,
                    "to_space_id": destination_id,
                    "timestamp": timestamp,
                }
            )
    return evacuated
