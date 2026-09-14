"""Membership API — Membership as a First-Class Runtime Resource refactor.

Membership is the canonical relationship between Actor and Society —
roles, permissions, policies, trust, and delegation all flow through it.
Backed entirely by PlanetaryRuntime.membership_registry (kernel/society/
membership.py::SocietyMembershipRegistry, itself backed by the append-only
kernel/timeline/ MembershipRecord) — no separate flat store; every
mutation is a new, immutable timeline entry (see membership.py's
_supersede()).

Core CRUD:
    GET/POST   /memberships
    GET/PATCH/DELETE /memberships/{membership_id}
Discovery:
    GET /memberships/actor/{actor_id}, /society/{society_id}, /team/{team_id},
        /role/{role}, /active, /history/{actor_id}
Roles:
    GET/POST /memberships/{id}/roles, DELETE /memberships/{id}/roles/{role}
Policy resolution:
    GET /memberships/{id}/policies|permissions|capabilities|constraints
Trust:
    GET/PATCH /memberships/{id}/trust, GET /memberships/{id}/reputation
Delegation:
    POST/GET /memberships/{id}/delegations, DELETE /delegations/{delegation_id}
Lifecycle:
    POST /memberships/{id}/activate|suspend|resume|terminate
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import (
    MembershipCreateRequest,
    MembershipUpdateRequest,
    MembershipResponse,
    RoleAssignRequest,
    TrustUpdateRequest,
    TrustResponse,
    ReputationResponse,
    DelegationCreateRequest,
    DelegationResponse,
)
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.gateway.memberships")
router = APIRouter()


def init_memberships_store() -> None:
    """No-op — kept for api/main.py's boot sequence call site. Membership
    as a First-Class Runtime Resource refactor: memberships are backed by
    kernel/timeline/'s TimelineStore now (retiring the old flat, separately
    Redis-persisted _in_memory_memberships dict), and TimelineStore selects
    its own backend (in-memory vs Redis) lazily on first use — there's no
    separate "load persisted memberships at boot" step to perform here
    anymore."""


def _get_planetary_runtime(request: Request) -> Any:
    return getattr(request.app.state, "planetary_runtime", None)


def _require_pr(request: Request) -> Any:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    return pr


def _to_response(m: Any) -> MembershipResponse:
    return MembershipResponse(
        membership_id=m.membership_id,
        actor_id=m.actor_id,
        society_id=m.society_id,
        team_id=m.team_id,
        roles=list(m.roles),
        status=m.status,
        start_time=m.start_time,
        end_time=m.end_time,
        permissions=list(m.permissions),
        trust_score=m.trust_score,
        metadata=dict(m.metadata),
    )


def _delegation_to_response(d: Any) -> DelegationResponse:
    return DelegationResponse(**d.to_dict())


def _find_membership_or_404(pr: Any, membership_id: str) -> Any:
    """No PlanetaryRuntime booted is treated the same as "not found" for
    lookups (404) — consistent with every other read-oriented route in
    this codebase (e.g. api/routes/planet.py's get_planet_societies
    returning [] rather than 503 when pr is None). 503 is reserved for
    operations that WRITE and therefore genuinely cannot proceed at all
    without a runtime (create_membership, role/lifecycle/delegation
    mutators — see _require_pr's other call sites below)."""
    if pr is None:
        raise HTTPException(status_code=404, detail=f"Membership {membership_id} not found")
    m = pr.membership_registry.get_membership(membership_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"Membership {membership_id} not found")
    return m


# ── Core CRUD ─────────────────────────────────────────────────────────────


@router.post("/memberships", response_model=MembershipResponse, tags=["Memberships"])
@idempotent("memberships.create_membership")
async def create_membership(
    body: MembershipCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-memberships")),
) -> MembershipResponse:
    pr = _require_pr(request)
    sr = pr.get_society_runtime(body.society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {body.society_id} not found")

    if sr.get_actor(body.actor_id) is None and pr.societies_for_actor(body.actor_id) == ():
        # Brand-new actor: this membership becomes its home registration.
        from src.monkey_brain.kernel.society.domain import (
            ActorProfile,
            ActorIdentity,
            ActorType,
        )

        profile = ActorProfile(
            identity=ActorIdentity(
                actor_id=body.actor_id,
                name=body.metadata.get("name", f"Actor-{body.actor_id[:8]}"),
                actor_type=ActorType.AI_AGENT,
            ),
        )
        # Registration Entry Points: delegate to PlanetaryRuntime.
        # register_actor() — the single canonical registration workflow —
        # rather than calling SocietyRuntime.register_actor() directly, so
        # this brand-new actor gets the same world-invariant enforcement
        # (Society hosted at a Space, Actor placed there) every other
        # registration path gets.
        pr.register_actor(profile, society_id=body.society_id)
        # register_actor() already creates the Membership (role="member") —
        # apply the requested role/permissions/team_id on top if different.
        m = pr.membership_registry.get_membership(
            next(
                mm.membership_id
                for mm in pr.membership_registry.memberships_for_actor(body.actor_id)
                if mm.society_id == body.society_id
            )
        )
        if body.role and body.role not in m.roles:
            m = pr.membership_registry.assign_role(m.membership_id, body.role)
    else:
        if pr.membership_registry.is_member(body.actor_id, body.society_id):
            raise HTTPException(
                status_code=409,
                detail=f"Actor {body.actor_id} already has a membership in society {body.society_id}",
            )
        ok = pr.join_society(body.actor_id, body.society_id, role=body.role or "member")
        if not ok:
            raise HTTPException(
                status_code=404,
                detail="Actor has no home registration and society join failed",
            )
        m = next(
            mm for mm in pr.membership_registry.memberships_for_actor(body.actor_id) if mm.society_id == body.society_id
        )
    return _to_response(m)


@router.get("/memberships", response_model=list[MembershipResponse], tags=["Memberships"])
async def list_memberships(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[MembershipResponse]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    return [_to_response(m) for m in pr.membership_registry.active_memberships()]


@router.patch(
    "/memberships/{membership_id}",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
@idempotent("memberships.update_membership")
async def update_membership(
    membership_id: str,
    body: MembershipUpdateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-memberships")),
) -> MembershipResponse:
    pr = _get_planetary_runtime(request)
    m = _find_membership_or_404(pr, membership_id)
    if body.metadata is not None or body.team_id is not None:
        m = (
            pr.membership_registry.record_event(
                membership_id,
                "updated",
                **({"team_id": body.team_id} if body.team_id is not None else {}),
            )
            or m
        )
    return _to_response(m)


@router.delete("/memberships/{membership_id}", tags=["Memberships"])
@idempotent("memberships.delete_membership")
async def delete_membership(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-memberships")),
) -> dict[str, str]:
    pr = _get_planetary_runtime(request)
    m = _find_membership_or_404(pr, membership_id)
    pr.leave_society(m.actor_id, m.society_id)
    return {"status": "deleted", "membership_id": membership_id}


# ── Discovery ─────────────────────────────────────────────────────────────


@router.get(
    "/memberships/actor/{actor_id}",
    response_model=list[MembershipResponse],
    tags=["Memberships"],
)
async def get_actor_memberships(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[MembershipResponse]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    return [_to_response(m) for m in pr.membership_registry.memberships_for_actor(actor_id)]


@router.get(
    "/memberships/society/{society_id}",
    response_model=list[MembershipResponse],
    tags=["Memberships"],
)
async def get_society_memberships(
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[MembershipResponse]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    return [_to_response(m) for m in pr.membership_registry.memberships_for_society(society_id)]


@router.get(
    "/memberships/team/{team_id}",
    response_model=list[MembershipResponse],
    tags=["Memberships"],
)
async def get_team_memberships(
    team_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[MembershipResponse]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    return [_to_response(m) for m in pr.membership_registry.memberships_for_team(team_id)]


@router.get(
    "/memberships/role/{role}",
    response_model=list[MembershipResponse],
    tags=["Memberships"],
)
async def get_memberships_by_role(
    role: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[MembershipResponse]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    return [_to_response(m) for m in pr.membership_registry.memberships_by_role(role)]


@router.get("/memberships/active", response_model=list[MembershipResponse], tags=["Memberships"])
async def get_active_memberships(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[MembershipResponse]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    return [_to_response(m) for m in pr.membership_registry.active_memberships()]


@router.get("/memberships/history/{actor_id}", tags=["Memberships"])
async def get_membership_history(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[dict[str, Any]]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    history = pr.membership_registry.history_for_actor(actor_id)
    return [r.to_dict() for r in sorted(history, key=lambda r: r.start_time)]


@router.get(
    "/memberships/{membership_id}",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
async def get_membership(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> MembershipResponse:
    """Registered AFTER every /memberships/<literal-segment> discovery
    route above (actor/society/team/role/active/history) so those literal
    segments aren't swallowed by this path parameter."""
    pr = _get_planetary_runtime(request)
    return _to_response(_find_membership_or_404(pr, membership_id))


# ── Role management ───────────────────────────────────────────────────────


@router.get("/memberships/{membership_id}/roles", tags=["Memberships"])
async def list_roles(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[str]:
    pr = _get_planetary_runtime(request)
    return list(_find_membership_or_404(pr, membership_id).roles)


@router.post(
    "/memberships/{membership_id}/roles",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
@idempotent("memberships.assign_role")
async def assign_role(
    membership_id: str,
    body: RoleAssignRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-memberships")),
) -> MembershipResponse:
    pr = _get_planetary_runtime(request)
    _find_membership_or_404(pr, membership_id)
    m = pr.membership_registry.assign_role(membership_id, body.role)
    return _to_response(m)


@router.delete(
    "/memberships/{membership_id}/roles/{role}",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
@idempotent("memberships.remove_role")
async def remove_role(
    membership_id: str,
    role: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-memberships")),
) -> MembershipResponse:
    pr = _get_planetary_runtime(request)
    _find_membership_or_404(pr, membership_id)
    m = pr.membership_registry.remove_role(membership_id, role)
    return _to_response(m)


# ── Policy resolution ───────────────────────────────────────────────────


@router.get("/memberships/{membership_id}/policies", tags=["Memberships"])
async def get_effective_policies(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[dict[str, Any]]:
    pr = _get_planetary_runtime(request)
    m = _find_membership_or_404(pr, membership_id)
    governance = pr.governance_for(m.society_id)
    policies = pr.membership_registry.resolve_policies(membership_id, governance=governance)
    return [dataclasses_to_dict(p) for p in policies]


@router.get("/memberships/{membership_id}/permissions", tags=["Memberships"])
async def get_effective_permissions(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[str]:
    pr = _get_planetary_runtime(request)
    m = _find_membership_or_404(pr, membership_id)
    governance = pr.governance_for(m.society_id)
    return list(pr.membership_registry.resolve_permissions(membership_id, governance=governance))


@router.get("/memberships/{membership_id}/capabilities", tags=["Memberships"])
async def get_effective_capabilities(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[str]:
    pr = _get_planetary_runtime(request)
    m = _find_membership_or_404(pr, membership_id)
    sr = pr.get_society_runtime(m.society_id)
    state = sr.get_actor(m.actor_id) if sr is not None else None
    profile = state.profile if state is not None else None
    return list(pr.membership_registry.resolve_capabilities(membership_id, actor_profile=profile))


@router.get("/memberships/{membership_id}/constraints", tags=["Memberships"])
async def get_effective_constraints(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[dict[str, Any]]:
    pr = _get_planetary_runtime(request)
    m = _find_membership_or_404(pr, membership_id)
    governance = pr.governance_for(m.society_id)
    constraints = pr.membership_registry.resolve_constraints(membership_id, governance=governance)
    return [dataclasses_to_dict(c) for c in constraints]


# ── Trust ─────────────────────────────────────────────────────────────────


@router.get(
    "/memberships/{membership_id}/trust",
    response_model=TrustResponse,
    tags=["Memberships"],
)
async def get_trust(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> TrustResponse:
    pr = _get_planetary_runtime(request)
    m = _find_membership_or_404(pr, membership_id)
    governance = pr.governance_for(m.society_id)
    record = governance.get_trust(m.actor_id) if governance is not None else None
    return TrustResponse(
        actor_id=m.actor_id,
        trust_score=record.trust_score if record else m.trust_score,
        evidence_count=record.evidence_count if record else 0,
        factors=dict(record.factors) if record else {},
    )


@router.patch(
    "/memberships/{membership_id}/trust",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
@idempotent("memberships.update_trust")
async def update_trust(
    membership_id: str,
    body: TrustUpdateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-memberships")),
) -> MembershipResponse:
    pr = _get_planetary_runtime(request)
    m = _find_membership_or_404(pr, membership_id)
    governance = pr.governance_for(m.society_id)
    updated = pr.membership_registry.update_trust(
        membership_id, body.trust_score, governance=governance, factors=body.factors
    )
    return _to_response(updated)


@router.get(
    "/memberships/{membership_id}/reputation",
    response_model=ReputationResponse,
    tags=["Memberships"],
)
async def get_reputation(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> ReputationResponse:
    pr = _get_planetary_runtime(request)
    m = _find_membership_or_404(pr, membership_id)
    governance = pr.governance_for(m.society_id)
    record = governance.get_trust(m.actor_id) if governance is not None else None
    audit_count = (
        len([a for a in getattr(governance, "_audit_log", []) if a.actor_id == m.actor_id]) if governance else 0
    )
    return ReputationResponse(
        actor_id=m.actor_id,
        evidence_count=record.evidence_count if record else 0,
        audit_entries=audit_count,
    )


# ── Delegation ────────────────────────────────────────────────────────────


@router.post(
    "/memberships/{membership_id}/delegations",
    response_model=DelegationResponse,
    tags=["Memberships"],
)
@idempotent("memberships.create_delegation")
async def create_delegation(
    membership_id: str,
    body: DelegationCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-memberships")),
) -> DelegationResponse:
    pr = _get_planetary_runtime(request)
    _find_membership_or_404(pr, membership_id)
    delegation = pr.delegation_registry.grant(
        membership_id,
        body.delegate_actor_id,
        tuple(body.permissions),
        valid_until=body.valid_until,
        constraints=body.constraints,
        reason=body.reason,
    )
    return _delegation_to_response(delegation)


@router.get(
    "/memberships/{membership_id}/delegations",
    response_model=list[DelegationResponse],
    tags=["Memberships"],
)
async def list_delegations(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-memberships")),
) -> list[DelegationResponse]:
    pr = _get_planetary_runtime(request)
    _find_membership_or_404(pr, membership_id)
    return [_delegation_to_response(d) for d in pr.delegation_registry.list_for_membership(membership_id)]


@router.delete("/delegations/{delegation_id}", tags=["Memberships"])
@idempotent("memberships.revoke_delegation")
async def revoke_delegation(
    delegation_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-memberships")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    ok = pr.delegation_registry.revoke(delegation_id) if pr is not None else False
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Delegation {delegation_id} not found or already revoked",
        )
    return {"status": "revoked", "delegation_id": delegation_id}


# ── Lifecycle ─────────────────────────────────────────────────────────────


async def _set_status(request: Request, membership_id: str, status: str) -> MembershipResponse:
    pr = _get_planetary_runtime(request)
    _find_membership_or_404(pr, membership_id)
    m = pr.membership_registry.set_status(membership_id, status)
    if m is not None and status == "terminated":
        # set_status() writes the registry directly, bypassing
        # leave_society() — mirror its affiliation cleanup here too, or a
        # terminated membership keeps rendering a stale MEMBER_OF edge.
        pr._unmirror_membership_affiliation(m.actor_id, m.society_id)
    return _to_response(m)


@router.post(
    "/memberships/{membership_id}/activate",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
@idempotent("memberships.activate_membership")
async def activate_membership(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-memberships")),
) -> MembershipResponse:
    return await _set_status(request, membership_id, "active")


@router.post(
    "/memberships/{membership_id}/suspend",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
@idempotent("memberships.suspend_membership")
async def suspend_membership(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-memberships")),
) -> MembershipResponse:
    return await _set_status(request, membership_id, "suspended")


@router.post(
    "/memberships/{membership_id}/resume",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
@idempotent("memberships.resume_membership")
async def resume_membership(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-memberships")),
) -> MembershipResponse:
    return await _set_status(request, membership_id, "active")


@router.post(
    "/memberships/{membership_id}/terminate",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
@idempotent("memberships.terminate_membership")
async def terminate_membership(
    membership_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-memberships")),
) -> MembershipResponse:
    return await _set_status(request, membership_id, "terminated")


def dataclasses_to_dict(obj: Any) -> dict[str, Any]:
    import dataclasses

    if dataclasses.is_dataclass(obj):
        d = dataclasses.asdict(obj)
        for k, v in d.items():
            if hasattr(v, "value"):
                d[k] = v.value
        return d
    return dict(obj) if isinstance(obj, dict) else {"value": str(obj)}
