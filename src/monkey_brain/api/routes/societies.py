"""Society API — CRUD and introspection for societies.

GET    /societies                — list all societies
POST   /societies                — create a new society
GET    /societies/{id}           — get society details
DELETE /societies/{id}           — remove a society
PATCH  /societies/{id}           — update society metadata
GET    /societies/{id}/status    — society health/status
GET    /societies/{id}/actors    — actors in a society
GET    /societies/{id}/context   — context stream events
GET    /societies/{id}/beliefs   — actor beliefs in a society
GET    /societies/{id}/resources — world resources visible to a society
POST   /societies/{id}/activate  — activate society for planetary cycles
POST   /societies/{id}/deactivate — deactivate society from planetary cycles
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import (
    SocietyCreateRequest,
    SocietyUpdateRequest,
    SocietyResponse,
    SocietyStatusResponse,
    SocietyContextResponse,
    SocietyBeliefsResponse,
    SocietyResourcesResponse,
    ActorResponse,
    SharedGoalRequest,
    SharedGoalsResponse,
    PolicyRequest,
    PoliciesResponse,
    SocietyActivationRequest,
    SocietyActivationResponse,
    ActivatedSocietyResponse,
    GovernancePolicyCreateRequest,
    GovernancePolicyResponse,
    GovernancePoliciesResponse,
    ActorPermissionGrantRequest,
    ActorPermissionResponse,
    ActorPermissionsResponse,
    SocietyMembersResponse,
    SocietyCommunicationLogResponse,
)
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.gateway.societies")
router = APIRouter()


def _get_planetary_runtime(request: Request) -> Any:
    return getattr(request.app.state, "planetary_runtime", None)


def _find_society(pr: Any, society_id: str) -> Any:
    return pr.get_society_runtime(society_id)


def _has_membership(pr: Any, actor_id: str, society_id: str) -> bool:
    """Check if an actor has an active membership in a society —
    Membership as a First-Class Runtime Resource refactor: reads through
    PlanetaryRuntime.membership_registry (the sole source of truth),
    replacing the retired flat _in_memory_memberships dict."""
    return pr.membership_registry.is_member(actor_id, society_id)


def _get_society_member_ids(pr: Any, society_id: str) -> set[str]:
    """Get all actor IDs that have memberships in a society."""
    return set(pr.membership_registry.actors_for_society(society_id))


def _society_actor_counts(pr: Any, society_id: str) -> tuple[int, int]:
    """Real (total, active) actor counts derived from Membership records —
    replaces the old sr.to_dict()["actor_count"/"active_actors"] pair,
    which mixed permanent membership with temporary presence-derived
    inclusion and read a dict key ("active_actors") that to_dict() never
    actually populates (it writes "active_actor_count" instead), so
    active counts always silently rendered 0. memberships_for_society
    returns every OPEN record — terminated included, since terminating
    just changes status on the still-open row rather than closing it —
    so "current" explicitly excludes terminated members."""
    memberships = pr.membership_registry.memberships_for_society(society_id)
    current = {m.actor_id for m in memberships if m.status != "terminated"}
    active = {m.actor_id for m in memberships if m.status == "active"}
    return len(current), len(active)


def serialize_beliefs(belief_state: Any) -> dict:
    """Serialize a BeliefState to a dict."""
    if belief_state is None or not hasattr(belief_state, "beliefs"):
        return {}
    beliefs = {}
    for entry in belief_state.beliefs:
        best = entry.best_hypothesis
        beliefs[entry.subject] = {
            "confidence": round(best.confidence, 3) if best else 0,
            "predicate": best.predicate if best else "",
        }
    return beliefs


@router.get("/societies", response_model=list[SocietyResponse], tags=["Societies"])
async def list_societies(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
) -> list[SocietyResponse]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    results = []
    for sr in pr.all_societies():
        td = sr.to_dict()
        actor_count, active_actors = _society_actor_counts(pr, sr.society.society_id)
        results.append(
            SocietyResponse(
                society_id=td.get("society_id", sr.society.society_id),
                name=td.get("society_name", sr.society.name),
                description=sr.society.description,
                society_type=sr.society.society_type,
                activation_tags=list(sr.society.activation_tags),
                always_active=sr.society.always_active,
                actor_count=actor_count,
                active_actors=active_actors,
                tick_count=td.get("tick_count", 0),
                interaction_count=td.get("interaction_count", 0),
                shared_goal_count=td.get("shared_goal_count", 0),
                policy_count=td.get("policy_count", 0),
                is_active=sr.is_active,
            )
        )
    return results


@router.post("/societies", response_model=SocietyResponse, tags=["Societies"])
@idempotent("societies.create_society")
async def create_society(
    body: SocietyCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> SocietyResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = pr.create_society(
        body.name,
        description=body.description,
        society_type=body.society_type,
        activation_tags=tuple(body.activation_tags),
        always_active=body.always_active,
        subscribed_events=tuple(body.subscribed_events),
    )
    td = sr.to_dict()
    return SocietyResponse(
        society_id=td.get("society_id", sr.society.society_id),
        name=td.get("society_name", sr.society.name),
        description=sr.society.description,
        society_type=sr.society.society_type,
        activation_tags=list(sr.society.activation_tags),
        always_active=sr.society.always_active,
        subscribed_events=list(sr.society.subscribed_events),
    )


@router.get("/societies/search", response_model=list[SocietyResponse], tags=["Societies"])
async def search_societies(
    request: Request,
    tag: str = "",
    society_type: str = "",
    user_id: str = Depends(require_permission("perm-view-societies")),
) -> list[SocietyResponse]:
    """Society discovery (Society as Organizational Context refactor):
    filter by activation_tags match and/or exact society_type. Registered
    before GET /societies/{society_id} so the literal "search" segment
    isn't swallowed by that route's path parameter."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    results = []
    for sr in pr.search_societies(tag=tag, society_type=society_type):
        td = sr.to_dict()
        actor_count, active_actors = _society_actor_counts(pr, sr.society.society_id)
        results.append(
            SocietyResponse(
                society_id=td.get("society_id", sr.society.society_id),
                name=td.get("society_name", sr.society.name),
                description=sr.society.description,
                society_type=sr.society.society_type,
                activation_tags=list(sr.society.activation_tags),
                always_active=sr.society.always_active,
                actor_count=actor_count,
                active_actors=active_actors,
                tick_count=td.get("tick_count", 0),
                interaction_count=td.get("interaction_count", 0),
                shared_goal_count=td.get("shared_goal_count", 0),
                policy_count=td.get("policy_count", 0),
                is_active=sr.is_active,
            )
        )
    return results


@router.post("/societies/activate", response_model=SocietyActivationResponse, tags=["Societies"])
@idempotent("societies.activate_societies_for_goal")
async def activate_societies_for_goal(
    body: SocietyActivationRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
) -> SocietyActivationResponse:
    """Society Activation Engine (Society as Organizational Context
    refactor): given an actor and a goal, dynamically select which of the
    actor's societies are relevant and return their merged policy bundle.
    Registered before GET /societies/{society_id} for the same path-
    ordering reason as /societies/search above (distinct HTTP method, but
    kept alongside for discoverability)."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    result = pr.activate_societies(body.actor_id, body.goal)
    return SocietyActivationResponse(
        actor_id=result.actor_id,
        goal=result.goal,
        activated=[ActivatedSocietyResponse(society_id=a.society_id, reason=a.reason) for a in result.activated],
        activated_policy_names=[p.name for p in result.policy_bundle.policies],
    )


@router.get("/societies/{society_id}", response_model=SocietyResponse, tags=["Societies"])
async def get_society(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
) -> SocietyResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    td = sr.to_dict()
    actor_count, active_actors = _society_actor_counts(pr, sr.society.society_id)
    return SocietyResponse(
        society_id=td.get("society_id", sr.society.society_id),
        name=td.get("society_name", sr.society.name),
        description=sr.society.description,
        society_type=sr.society.society_type,
        activation_tags=list(sr.society.activation_tags),
        always_active=sr.society.always_active,
        actor_count=actor_count,
        active_actors=active_actors,
        tick_count=td.get("tick_count", 0),
        interaction_count=td.get("interaction_count", 0),
        shared_goal_count=td.get("shared_goal_count", 0),
        policy_count=td.get("policy_count", 0),
        is_active=sr.is_active,
    )


@router.delete("/societies/{society_id}", tags=["Societies"])
@idempotent("societies.delete_society")
async def delete_society(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    # Unregister all actors in this society
    actors_removed = []
    for state in sr.all_actors():
        sr.unregister_actor(state.actor_id)
        actors_removed.append(state.actor_id)
    # Terminate memberships for this society (Membership as a First-Class
    # Runtime Resource refactor: set_status(..., "terminated") through the
    # registry, not a flat-dict delete — keeps the lifecycle auditable).
    # Also unmirror each one's "member_of" Affiliation — set_status bypasses
    # leave_society(), which is the only other place that cleanup happens,
    # so it has to be done explicitly here too or the graph keeps showing a
    # MEMBER_OF edge for a society that no longer exists.
    memberships = pr.membership_registry.memberships_for_society(society_id)
    for membership in memberships:
        pr.membership_registry.set_status(membership.membership_id, "terminated", reason="society deleted")
        pr._unmirror_membership_affiliation(membership.actor_id, society_id)
    # NOTE: entity_for_society resolves a single hosting entity. host_society
    # has no cardinality restriction in principle (a Society could be hosted
    # at more than one entity), so a hypothetically multi-hosted Society
    # would only be un-hosted from one here. Nothing in this codebase
    # currently multi-hosts a Society, so this is a known, currently
    # unreachable limitation rather than an active bug.
    hosting_entity = pr.entity_for_society(society_id)
    if hosting_entity is not None:
        pr.unhost_society(hosting_entity.entity_id, society_id)
    # Remove the society
    pr._societies.pop(society_id, None)
    pr._save_societies()
    pr._save_actors()
    return {
        "status": "deleted",
        "society_id": society_id,
        "actors_removed": actors_removed,
        "memberships_removed": len(memberships),
    }


@router.patch("/societies/{society_id}", response_model=SocietyResponse, tags=["Societies"])
@idempotent("societies.update_society")
async def update_society(
    society_id: str,
    body: SocietyUpdateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> SocietyResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    import dataclasses

    old = sr._society
    new = dataclasses.replace(
        old,
        name=body.name if body.name is not None else old.name,
        description=(body.description if body.description is not None else old.description),
    )
    sr._society = new
    pr._save_societies()
    td = sr.to_dict()
    actor_count, active_actors = _society_actor_counts(pr, sr.society.society_id)
    return SocietyResponse(
        society_id=td.get("society_id", sr.society.society_id),
        name=td.get("society_name", sr.society.name),
        description=sr.society.description,
        society_type=sr.society.society_type,
        activation_tags=list(sr.society.activation_tags),
        always_active=sr.society.always_active,
        actor_count=actor_count,
        active_actors=active_actors,
        is_active=sr.is_active,
    )


@router.get(
    "/societies/{society_id}/status",
    response_model=SocietyStatusResponse,
    tags=["Societies"],
)
async def get_society_status(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
) -> SocietyStatusResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    td = sr.to_dict()
    actor_count, active_actors = _society_actor_counts(pr, sr.society.society_id)
    return SocietyStatusResponse(
        society_id=society_id,
        name=sr.society.name,
        status="healthy",
        actor_count=actor_count,
        active_actors=active_actors,
        tick_count=td.get("tick_count", 0),
        world_version=td.get("world_version", 0),
    )


@router.get(
    "/societies/{society_id}/actors",
    response_model=list[ActorResponse],
    tags=["Societies"],
)
async def get_society_actors(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
) -> list[ActorResponse]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    member_ids = _get_society_member_ids(pr, society_id)
    # If society has any memberships, only show members. Otherwise show all (backward compat).
    has_memberships = len(member_ids) > 0
    return [
        ActorResponse(
            actor_id=state.actor_id,
            name=state.profile.identity.name,
            actor_type=state.profile.identity.actor_type.value,
            description=state.profile.identity.description,
            status=state.status.value,
            cycle_count=state.cycle_count,
            is_active=state.is_active,
            societies=[society_id],
            goals=list(state.profile.goals),
            policies=list(state.profile.policies),
            trust_level=state.profile.trust_level,
            ownership=state.profile.ownership,
        )
        for state in sr.all_actors()
        if not has_memberships or state.actor_id in member_ids
    ]


@router.get(
    "/societies/{society_id}/members",
    response_model=SocietyMembersResponse,
    tags=["Societies"],
)
async def get_society_members(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
) -> SocietyMembersResponse:
    """Real vs temporary membership, distinguished the same way
    get_society_actors already resolves eligibility: sr.all_actors()
    holds BOTH — add_temporary_participant/remove_temporary_participant
    (SocietyRuntime) mutate the exact same dict a permanent Membership
    lands in via _attach_society — a member is "temporary" iff it's in
    all_actors() but not in the permanent MembershipRegistry."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    member_ids = _get_society_member_ids(pr, society_id)
    has_memberships = len(member_ids) > 0
    members = [
        {
            "actor_id": state.actor_id,
            "name": state.profile.identity.name,
            "actor_type": state.profile.identity.actor_type.value,
            "status": state.status.value,
            "is_active": state.is_active,
            # Backward compat with get_society_actors: a society that has
            # never used the permanent registry treats everyone present
            # as a regular member, not as if they were all temporary.
            "is_temporary": has_memberships and state.actor_id not in member_ids,
        }
        for state in sr.all_actors()
    ]
    return SocietyMembersResponse(society_id=society_id, members=members)


@router.get(
    "/societies/{society_id}/communication-log",
    response_model=SocietyCommunicationLogResponse,
    tags=["Societies"],
)
async def get_society_communication_log(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
    limit: int = 100,
) -> SocietyCommunicationLogResponse:
    """Real CommunicationDecision records this society's
    AffiliationCommunicationRouter has actually made — send_message/
    broadcast_message (wired through the cognitive OS's os.send_message())
    append here every time an actor really messages another; empty means
    no communication has happened yet, not that the feature is broken."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    limit = max(1, min(limit, 1000))
    decisions = sr.communication_audit()
    entries = [
        {
            "decision_id": d.decision_id,
            "sender_id": d.sender_id,
            "recipient_id": d.recipient_id,
            "allowed": d.allowed,
            "reason": d.reason,
            "affiliation_id": d.affiliation_id,
            "society_id": d.society_id,
            "correlation_id": d.correlation_id,
            "causation_id": d.causation_id,
        }
        for d in decisions[-limit:]
    ]
    return SocietyCommunicationLogResponse(society_id=society_id, entries=entries)


@router.get(
    "/societies/{society_id}/context",
    response_model=SocietyContextResponse,
    tags=["Societies"],
)
async def get_society_context(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
    event_type: str = "",
    limit: int = 100,
) -> SocietyContextResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    member_ids = _get_society_member_ids(pr, society_id)
    actor_ids = {state.actor_id for state in sr.all_actors() if not member_ids or state.actor_id in member_ids}
    planetary_events = pr.context_stream.events(limit=100000)
    society_events = sr.context_stream.events(limit=100000)
    all_events = []
    seen_ids = set()
    for e in society_events:
        if e.event_id in seen_ids:
            continue
        seen_ids.add(e.event_id)
        all_events.append(e)
    for e in planetary_events:
        if e.event_id in seen_ids:
            continue
        if e.actor_id in actor_ids or e.event_type.value == "world_update":
            seen_ids.add(e.event_id)
            all_events.append(e)
    all_events.sort(key=lambda e: e.timestamp)
    if event_type:
        all_events = [e for e in all_events if e.event_type.value == event_type.lower()]
    limit = max(1, min(limit, 5000))
    return SocietyContextResponse(
        society_id=society_id,
        events=[e.to_dict() for e in all_events[-limit:]],
        event_count=len(all_events),
    )


@router.get(
    "/societies/{society_id}/beliefs",
    response_model=SocietyBeliefsResponse,
    tags=["Societies"],
)
async def get_society_beliefs(
    society_id: str,
    request: Request,
    # Doot audit BYPASS-03 fix: this used to be gated ONLY by
    # require_permission("perm-view-societies") with no self/consent
    # check at all, unlike the sibling single-actor /actors/{id}/beliefs
    # route (require_self_or_permission). Unlike that route, this one
    # fans out across EVERY member actor's beliefs in one call, so a
    # straight user==target_id comparison doesn't apply the same way —
    # the fix below keeps perm-view-societies as full, unrestricted,
    # operator-level disclosure (unchanged), and additionally lets an
    # authenticated member see the listing filtered to their OWN entry
    # only, instead of either "see everyone" or "see nothing." Any other
    # authenticated, non-member caller without the permission is denied.
    user_id: str = Depends(require_permission("perm-execute-prompt")),
) -> SocietyBeliefsResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    member_ids = _get_society_member_ids(pr, society_id)

    from src.monkey_brain.api.dependencies import auth_required

    granted = getattr(request.state, "jwt_permissions", set()) or set()
    privileged = (not auth_required()) or "perm-view-societies" in granted
    is_member = bool(member_ids) and user_id in member_ids
    if not privileged and not is_member:
        raise HTTPException(
            status_code=403,
            detail="perm-view-societies required to view another actor's beliefs in this society",
        )

    actors = []
    for state in sr.all_actors():
        if member_ids and state.actor_id not in member_ids:
            continue
        if not privileged and state.actor_id != user_id:
            continue
        beliefs = serialize_beliefs(state.belief_state)
        actors.append({"actor_id": state.actor_id, "beliefs": beliefs})
    return SocietyBeliefsResponse(society_id=society_id, actors=actors)


@router.get(
    "/societies/{society_id}/resources",
    response_model=SocietyResourcesResponse,
    tags=["Societies"],
)
async def get_society_resources(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
) -> SocietyResourcesResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    world = sr.world
    resources = [r.to_dict() for r in world.resources()] if hasattr(world, "resources") else []
    return SocietyResourcesResponse(society_id=society_id, resources=resources)


@router.post("/societies/{society_id}/tick", tags=["Societies"])
@idempotent("societies.tick_society")
async def tick_society(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-societies")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    result = await sr.tick()
    pr._save_actors()
    pr._save_world()
    pr._save_context()
    pr._save_societies()
    return {
        "society_id": society_id,
        "tick_number": result.tick_number,
        "actors_ticked": result.actors_ticked,
        "interactions_routed": result.interactions_routed,
        "world_version": result.world_version,
        "duration_ms": result.duration_ms,
    }


# ── Society Activation/Deactivation ────────────────────────────────────────


@router.post("/societies/{society_id}/activate", tags=["Societies"])
@idempotent("societies.activate_society")
async def activate_society(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> dict[str, Any]:
    """Activate a society so it participates in planetary cycles."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    success = pr.activate_society(society_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    return {"society_id": society_id, "status": "activated"}


@router.post("/societies/{society_id}/deactivate", tags=["Societies"])
@idempotent("societies.deactivate_society")
async def deactivate_society(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> dict[str, Any]:
    """Deactivate a society so it's skipped during planetary cycles."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    success = pr.deactivate_society(society_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    return {"society_id": society_id, "status": "deactivated"}


# ── Shared Goals CRUD ───────────────────────────────────────────────────


@router.get(
    "/societies/{society_id}/goals",
    response_model=SharedGoalsResponse,
    tags=["Societies"],
)
async def get_society_goals(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
) -> SharedGoalsResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    goals = list(sr.society.shared_goals)
    return SharedGoalsResponse(society_id=society_id, goals=goals, count=len(goals))


@router.post(
    "/societies/{society_id}/goals",
    response_model=SharedGoalsResponse,
    tags=["Societies"],
)
@idempotent("societies.add_society_goal")
async def add_society_goal(
    society_id: str,
    body: SharedGoalRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> SharedGoalsResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    sr.add_shared_goal(body.goal)
    pr._save_societies()
    goals = list(sr.society.shared_goals)
    return SharedGoalsResponse(society_id=society_id, goals=goals, count=len(goals))


@router.delete("/societies/{society_id}/goals/{goal_index}", tags=["Societies"])
@idempotent("societies.remove_society_goal")
async def remove_society_goal(
    society_id: str,
    goal_index: int,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    goals = list(sr.society.shared_goals)
    if goal_index < 0 or goal_index >= len(goals):
        raise HTTPException(status_code=404, detail=f"Goal index {goal_index} not found")
    removed = goals.pop(goal_index)
    import dataclasses

    sr._society = dataclasses.replace(sr._society, shared_goals=tuple(goals))
    pr._save_societies()
    return {"status": "deleted", "goal": removed}


# ── Policies CRUD ───────────────────────────────────────────────────────


@router.get(
    "/societies/{society_id}/policies",
    response_model=PoliciesResponse,
    tags=["Societies"],
)
async def get_society_policies(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
) -> PoliciesResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    policies = list(sr.society.policies)
    return PoliciesResponse(society_id=society_id, policies=policies, count=len(policies))


@router.post(
    "/societies/{society_id}/policies",
    response_model=PoliciesResponse,
    tags=["Societies"],
)
@idempotent("societies.add_society_policy")
async def add_society_policy(
    society_id: str,
    body: PolicyRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> PoliciesResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    sr.add_policy(body.policy)
    pr._save_societies()
    policies = list(sr.society.policies)
    return PoliciesResponse(society_id=society_id, policies=policies, count=len(policies))


def _governance_policy_to_response(p: Any) -> GovernancePolicyResponse:
    return GovernancePolicyResponse(
        policy_id=p.policy_id,
        name=p.name,
        description=p.description,
        policy_type=p.policy_type.value,
        rules=list(p.rules),
        scope=p.scope,
        priority=p.priority,
        enabled=p.enabled,
    )


@router.get(
    "/societies/{society_id}/governance-policies",
    response_model=GovernancePoliciesResponse,
    tags=["Societies"],
)
async def get_society_governance_policies(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
) -> GovernancePoliciesResponse:
    """Real GovernancePolicy objects (kernel/society/governance.py) —
    distinct from GET /societies/{id}/policies, which reads the plain
    string list Society.policies. This is what PlanningContext.
    active_policies (and the planner's prompt) actually reads."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        return GovernancePoliciesResponse(society_id=society_id)
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    policies = [_governance_policy_to_response(p) for p in sr.governance.policies(enabled_only=False)]
    return GovernancePoliciesResponse(society_id=society_id, policies=policies, count=len(policies))


@router.post(
    "/societies/{society_id}/governance-policies",
    response_model=GovernancePolicyResponse,
    tags=["Societies"],
)
@idempotent("societies.add_society_governance_policy")
async def add_society_governance_policy(
    society_id: str,
    body: GovernancePolicyCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> GovernancePolicyResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    from src.monkey_brain.kernel.society.governance import GovernancePolicy, PolicyType

    try:
        policy_type = PolicyType(body.policy_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid policy_type: {body.policy_type!r}")
    policy = GovernancePolicy(
        name=body.name,
        description=body.description,
        policy_type=policy_type,
        rules=tuple(body.rules),
        scope=body.scope,
        priority=body.priority,
        enabled=body.enabled,
    )
    sr.governance.add_policy(policy)
    # CognitiveOS Constitution: "knowledge, policies and capabilities are
    # versioned infrastructure" -- mirror the real governance registration
    # into SharedWorld's own versioned WorldPolicy record too (see
    # SharedWorld.record_policy's docstring for why this was previously
    # dead scaffolding), keyed by the SAME policy_id so a later edit to
    # this policy is recognized as a re-registration, not a new one.
    #
    # record_policy() asserts assert_state_mutation_allowed() (world.py's
    # _require_write) -- same class of fix as POST /merchants etc.
    # elsewhere in this pass: this route is operator/admin-driven society
    # governance setup, not an agent action, and fails closed with
    # SecurityBoundaryDenied (500) outside insecure-dev-mode without this.
    from src.monkey_brain.kernel.security_boundary import privileged_infrastructure

    with privileged_infrastructure(
        reason="POST /societies/{id}/governance-policies: operator defining a policy, not an agent action"
    ):
        sr.world.record_policy(
            policy_id=policy.policy_id,
            name=policy.name,
            description=policy.description,
            rules=policy.rules,
            scope=policy.scope,
        )
    # SocietyGovernanceEngine cross-process gap: _save_societies() already
    # serializes sr.governance.policies() into the same durable blob every
    # OTHER society-mutating route here calls it after -- this route was
    # the one real exception, so a policy added here was only ever
    # persisted by accident, whenever some unrelated route happened to
    # trigger a save afterward. Without this, a second process (or this
    # one after a restart) never saw it.
    pr._save_societies()
    return _governance_policy_to_response(policy)


@router.delete("/societies/{society_id}/governance-policies/{policy_id}", tags=["Societies"])
@idempotent("societies.remove_society_governance_policy")
async def remove_society_governance_policy(
    society_id: str,
    policy_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    removed = sr.governance.remove_policy(policy_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")
    pr._save_societies()
    return {"status": "deleted", "policy_id": policy_id}


def _permission_to_response(p: Any) -> ActorPermissionResponse:
    return ActorPermissionResponse(
        permission_id=p.permission_id,
        actor_id=p.actor_id,
        resource=p.resource,
        action=p.action,
        expires_at=p.expires_at,
    )


@router.get(
    "/societies/{society_id}/actors/{actor_id}/permissions",
    response_model=ActorPermissionsResponse,
    tags=["Societies"],
)
async def get_actor_permissions(
    society_id: str,
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-societies")),
) -> ActorPermissionsResponse:
    """Real, per-actor Permission grants (kernel/society/governance.py) —
    what Membership.resolve_permissions() actually reads."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        return ActorPermissionsResponse(society_id=society_id, actor_id=actor_id)
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    permissions = [_permission_to_response(p) for p in sr.governance.permissions_for(actor_id)]
    return ActorPermissionsResponse(
        society_id=society_id,
        actor_id=actor_id,
        permissions=permissions,
        count=len(permissions),
    )


@router.post(
    "/societies/{society_id}/permissions",
    response_model=ActorPermissionResponse,
    tags=["Societies"],
)
@idempotent("societies.grant_actor_permission")
async def grant_actor_permission(
    society_id: str,
    body: ActorPermissionGrantRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> ActorPermissionResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    from src.monkey_brain.kernel.society.governance import Permission

    permission = Permission(
        actor_id=body.actor_id,
        resource=body.resource,
        action=body.action,
        granted_by=user_id,
        expires_at=body.expires_at,
    )
    sr.governance.grant_permission(permission)
    pr._save_societies()  # SocietyGovernanceEngine cross-process gap — see add_society_governance_policy's comment
    return _permission_to_response(permission)


@router.delete("/societies/{society_id}/permissions", tags=["Societies"])
@idempotent("societies.revoke_actor_permission")
async def revoke_actor_permission(
    society_id: str,
    actor_id: str,
    resource: str,
    action: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    removed = sr.governance.revoke_permission(actor_id, resource, action)
    if not removed:
        raise HTTPException(status_code=404, detail="Permission not found")
    pr._save_societies()
    return {
        "status": "revoked",
        "actor_id": actor_id,
        "resource": resource,
        "action": action,
    }


@router.delete("/societies/{society_id}/policies/{policy_index}", tags=["Societies"])
@idempotent("societies.remove_society_policy")
async def remove_society_policy(
    society_id: str,
    policy_index: int,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-societies")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    sr = _find_society(pr, society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {society_id} not found")
    policies = list(sr.society.policies)
    if policy_index < 0 or policy_index >= len(policies):
        raise HTTPException(status_code=404, detail=f"Policy index {policy_index} not found")
    removed = policies.pop(policy_index)
    import dataclasses

    sr._society = dataclasses.replace(sr._society, policies=tuple(policies))
    pr._save_societies()
    return {"status": "deleted", "policy": removed}
