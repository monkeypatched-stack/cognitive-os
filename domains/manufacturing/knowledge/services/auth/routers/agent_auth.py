"""Agent authentication router.

Pillars:
  IdP          POST /agent/register          — human-gated agent identity issuance
  RBAC         roles → permission_ids → scopes (reuses existing roles collection)
  Lifecycle    POST /agent/token             — access (60 min) + refresh (24 h) pair
               POST /agent/token             — refresh_token grant rotates the pair
               POST /agent/token             — RFC 8693 cross-domain token exchange
               POST /agent/revoke            — revoke jti or entire agent (admin)
               POST /agent/reactivate        — reverse a deactivation (admin)
  SPIFFE       agent_id is always a spiffe:// URI; embedded as `sub` in all tokens
  Audit        every issuance / revocation emits to indus.audit.queue
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from services.auth.helpers.agent_tokens import (
    AGENT_ACCESS_TOKEN_MINUTES,
    create_agent_access_token,
    create_agent_refresh_token,
    decode_agent_access_token,
    decode_agent_refresh_token,
    generate_client_credentials,
    hash_client_secret,
)
from services.auth.helpers.revocation import (
    block_jti,
    deactivate_agent,
    reactivate_agent as _reactivate_agent,
)
from services.auth.models.agent_identity import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    AgentTokenResponse,
)
from services.common.audit_events import emit as audit_emit
from services.common.auth import get_current_user
from services.common.db import get_database

router = APIRouter()
logger = logging.getLogger(__name__)

_AGENT_COLLECTION = "agent_identities"
_ROLES_COLLECTION = "roles"
_USED_JTI_COLLECTION = "agent_used_jtis"

# RFC 8693 grant type
_TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"


# ---------------------------------------------------------------------------
# Request/response models for management endpoints
# ---------------------------------------------------------------------------


class RevokeRequest(BaseModel):
    client_id: Optional[str] = Field(default=None)
    jti: Optional[str] = Field(default=None)
    reason: str = Field(default="", max_length=500)


class ReactivateRequest(BaseModel):
    client_id: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_scopes(
    db: AsyncIOMotorDatabase,
    role_ids: list[str],
    fallback_scopes: list[str],
) -> list[str]:
    """Union permission_ids from role hierarchy; fall back to explicit scopes.

    Uses rbac.resolve_permissions() which walks parent_role_ids recursively so
    a Manager role automatically inherits Supervisor and Worker permissions.
    """
    if not role_ids:
        return list(fallback_scopes)
    try:
        from services.auth.helpers.rbac import resolve_permissions

        scopes = await resolve_permissions(db, role_ids)
        return scopes if scopes else list(fallback_scopes)
    except Exception as exc:
        logger.warning(
            "Hierarchical RBAC resolution failed, falling back to flat lookup: %s", exc
        )
        scopes: set[str] = set()
        async for role in db[_ROLES_COLLECTION].find(
            {"role_id": {"$in": role_ids}}, {"_id": 0, "permissions": 1}
        ):
            scopes.update(role.get("permissions") or [])
        return sorted(scopes) if scopes else list(fallback_scopes)


async def _consume_refresh_jti(db: AsyncIOMotorDatabase, jti: str) -> None:
    """Single-use enforcement for refresh tokens (replay prevention)."""
    if await db[_USED_JTI_COLLECTION].find_one({"jti": jti}):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token already used",
            headers={"WWW-Authenticate": "Bearer"},
        )
    await db[_USED_JTI_COLLECTION].insert_one(
        {"jti": jti, "used_at": datetime.now(timezone.utc).isoformat()}
    )


def _build_token_response(identity: dict, requested_scope: str) -> AgentTokenResponse:
    registered: list[str] = identity["scopes"]
    requested = (
        [s for s in requested_scope.split() if s] if requested_scope else registered
    )
    granted = [s for s in requested if s in registered] or registered
    access_token = create_agent_access_token(
        client_id=identity["client_id"],
        agent_type=identity["agent_type"],
        scopes=granted,
        spiffe_id=identity["spiffe_id"],
        role_ids=identity.get("role_ids") or [],
    )
    refresh_token = create_agent_refresh_token(
        client_id=identity["client_id"],
        spiffe_id=identity["spiffe_id"],
    )
    return AgentTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=AGENT_ACCESS_TOKEN_MINUTES * 60,
        scope=" ".join(granted),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(
    payload: AgentRegisterRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    """Register an agent identity. Requires a valid human session token.

    The SPIFFE URI is the permanent identity anchor — it becomes `sub` in every token.
    Roles resolve from the existing RBAC roles collection; union of permission_ids = scopes.
    """
    spiffe_id = (
        payload.spiffe_id
        or f"spiffe://monkeybrain/agent/{payload.agent_type}/{uuid4().hex[:8]}"
    )
    client_id, client_secret = generate_client_credentials()
    scopes = await _resolve_scopes(db, payload.role_ids, payload.scopes)
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "agent_id": spiffe_id,
        "client_id": client_id,
        "client_secret_hash": hash_client_secret(client_secret),
        "agent_type": payload.agent_type,
        "role_ids": payload.role_ids,
        "scopes": scopes,
        "spiffe_id": spiffe_id,
        "metadata": payload.metadata,
        "is_active": True,
        "created_at": now,
        "created_by": current_user.get("sub") or current_user.get("user_id"),
    }
    await db[_AGENT_COLLECTION].insert_one(doc)
    await audit_emit(
        "agent.registered",
        "allow",
        {"sub": spiffe_id, "principal_type": "agent", "agent_type": payload.agent_type},
        metadata={"client_id": client_id, "role_ids": payload.role_ids},
    )
    logger.info(
        "Agent registered: spiffe_id=%s agent_type=%s", spiffe_id, payload.agent_type
    )
    return AgentRegisterResponse(
        agent_id=spiffe_id,
        client_id=client_id,
        client_secret=client_secret,
        agent_type=payload.agent_type,
        role_ids=payload.role_ids,
        scopes=scopes,
        spiffe_id=spiffe_id,
    )


@router.post("/token", response_model=AgentTokenResponse)
async def agent_token(
    grant_type: str = Form(...),
    client_id: str = Form(default=""),
    client_secret: str = Form(default=""),
    refresh_token: str = Form(default=""),
    subject_token: str = Form(default=""),  # RFC 8693
    audience: str = Form(default=""),  # RFC 8693 target domain
    scope: str = Form(default=""),
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    """Issue or rotate agent tokens.

    grant_type=client_credentials                         — initial pair
    grant_type=refresh_token                              — rotate (single-use)
    grant_type=urn:ietf:params:oauth:grant-type:token-exchange — RFC 8693 cross-domain
    """
    if grant_type == "client_credentials":
        return await _issue_from_credentials(db, client_id, client_secret, scope)
    if grant_type == "refresh_token":
        return await _issue_from_refresh(db, refresh_token, scope)
    if grant_type == _TOKEN_EXCHANGE_GRANT:
        return await _issue_from_token_exchange(db, subject_token, audience, scope)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported grant_type: {grant_type}",
    )


async def _issue_from_credentials(
    db: AsyncIOMotorDatabase, client_id: str, client_secret: str, scope: str
) -> AgentTokenResponse:
    identity = await db[_AGENT_COLLECTION].find_one(
        {
            "client_id": client_id,
            "client_secret_hash": hash_client_secret(client_secret),
            "is_active": True,
        },
        {"_id": 0},
    )
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    resp = _build_token_response(identity, scope)
    await audit_emit(
        "token.issued",
        "allow",
        {
            "sub": identity["spiffe_id"],
            "principal_type": "agent",
            "agent_type": identity["agent_type"],
        },
    )
    return resp


async def _issue_from_refresh(
    db: AsyncIOMotorDatabase, refresh_token_str: str, scope: str
) -> AgentTokenResponse:
    from jose import JWTError

    try:
        claims = decode_agent_refresh_token(refresh_token_str)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    await _consume_refresh_jti(db, claims["jti"])
    identity = await db[_AGENT_COLLECTION].find_one(
        {"client_id": claims["client_id"], "is_active": True}, {"_id": 0}
    )
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent identity not found or deactivated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    resp = _build_token_response(identity, scope)
    await audit_emit(
        "token.refreshed",
        "allow",
        {
            "sub": identity["spiffe_id"],
            "principal_type": "agent",
            "agent_type": identity["agent_type"],
        },
    )
    return resp


async def _issue_from_token_exchange(
    db: AsyncIOMotorDatabase, subject_token: str, audience: str, scope: str
) -> AgentTokenResponse:
    """RFC 8693 token exchange — validate inbound token, issue scoped token for target domain.

    Used for cross-domain federation: a supplier's agent exchanges its enterprise
    token for a short-lived token trusted by this mesh.
    """
    from jose import JWTError

    if not subject_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="subject_token required"
        )

    try:
        claims = decode_agent_access_token(subject_token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="subject_token is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Look up the agent identity by client_id from the inbound token
    identity = await db[_AGENT_COLLECTION].find_one(
        {"client_id": claims.get("client_id"), "is_active": True}, {"_id": 0}
    )
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent identity not found or deactivated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Scope restriction: only grant scopes the identity holds AND that were requested
    registered: list[str] = identity["scopes"]
    requested = [s for s in scope.split() if s] if scope else registered
    granted = [s for s in requested if s in registered] or registered

    # Access token for the target audience — shorter TTL than normal (30 min)
    from services.auth.helpers.agent_tokens import create_agent_access_token as _create
    import secrets as _secrets
    from datetime import timedelta
    from jose import jwt as _jwt
    from services.common.config import settings

    now = datetime.now(timezone.utc)
    exchange_payload = {
        "sub": identity["spiffe_id"],
        "client_id": identity["client_id"],
        "agent_type": identity["agent_type"],
        "scopes": granted,
        "role_ids": identity.get("role_ids") or [],
        "spiffe_id": identity["spiffe_id"],
        "grant_type": "client_credentials",
        "aud": audience or "federated",
        "act": {"sub": claims.get("sub")},  # RFC 8693 delegation chain
        "jti": _secrets.token_hex(16),
        "iat": now,
        "exp": now + timedelta(minutes=30),
    }
    access_token = _jwt.encode(
        exchange_payload, settings.ACCESS_TOKEN_SECRET, algorithm=settings.ALGORITHM
    )
    refresh_token = create_agent_refresh_token(
        identity["client_id"], identity["spiffe_id"]
    )

    await audit_emit(
        "token.exchanged",
        "allow",
        {
            "sub": identity["spiffe_id"],
            "principal_type": "agent",
            "agent_type": identity["agent_type"],
        },
        metadata={"audience": audience, "granted_scopes": granted},
    )
    return AgentTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=30 * 60,
        scope=" ".join(granted),
    )


@router.post("/revoke")
async def revoke_agent_credentials(
    payload: RevokeRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    """Revoke an agent's credentials. Admin action — requires human auth.

    Pass client_id to deactivate the entire agent (marks is_active=False + Redis block).
    Pass jti to revoke a single access token (Redis blocklist, auto-expires at token TTL).
    """
    results: dict = {}
    actor = current_user.get("sub") or current_user.get("user_id")

    if payload.client_id:
        results["agent"] = await deactivate_agent(payload.client_id, db, payload.reason)
        await audit_emit(
            "token.revoked",
            "allow",
            {"sub": payload.client_id, "principal_type": "agent"},
            metadata={"actor": actor, "reason": payload.reason, "scope": "agent"},
        )
        logger.info(
            "Agent deactivated: client_id=%s by=%s reason=%s",
            payload.client_id,
            actor,
            payload.reason,
        )

    if payload.jti:
        await block_jti(payload.jti)
        results["jti"] = {"blocked": True, "jti": payload.jti}
        await audit_emit(
            "token.revoked",
            "allow",
            metadata={"actor": actor, "jti": payload.jti, "scope": "jti"},
        )

    if not results:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide client_id (agent-level) or jti (token-level)",
        )
    return results


@router.post("/reactivate")
async def reactivate_agent_identity(
    payload: ReactivateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    """Re-activate a previously deactivated agent. Admin action — requires human auth."""
    result = await _reactivate_agent(payload.client_id, db)
    actor = current_user.get("sub") or current_user.get("user_id")
    await audit_emit(
        "agent.reactivated",
        "allow",
        {"sub": payload.client_id, "principal_type": "agent"},
        metadata={"actor": actor},
    )
    return result
