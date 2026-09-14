"""Multi-strategy auth dependency — zero-trust principal resolution.

Resolution order (first match wins):
  1. Existing HMAC human JWT      — decode_access_token()        [always active]
  2. Pipeline-scoped SPIFFE token — decode_pipeline_token()      + pipeline_id binding
  3. Agent client_credentials JWT — decode_agent_access_token()  + revocation check
  4. Keycloak RS256               — only if KEYCLOAK_ISSUER env var is set

Pipeline tokens (strategy 2) are short-lived (120 s) step-scoped SPIFFE tokens whose
sub encodes pipeline_id/step_name/agent_role and are bound to the active Redis attestation.  They are recognised before generic agent tokens so the
narrower claim is checked first.

Falls back to HTTP 401 only when every strategy fails.
Existing get_current_user() and require_permission() are unchanged.

Auth decisions are emitted as audit events to indus.audit.queue (best-effort).
"""

import logging
import os
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from services.auth.helpers.tokens import decode_access_token
from services.auth.helpers.agent_tokens import (
    decode_agent_access_token,
    decode_pipeline_token,
)
from services.auth.helpers.revocation import is_jti_revoked, is_agent_blocked

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Keycloak fallback
# ---------------------------------------------------------------------------


def _try_keycloak(token: str) -> dict | None:
    if not os.getenv("KEYCLOAK_ISSUER"):
        return None
    try:
        from fastapi.security import HTTPAuthorizationCredentials as _Creds
        from services.file.src.core.keycloak import verify_jwt as _kc_verify

        return _kc_verify(_Creds(scheme="bearer", credentials=token))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Principal dependency
# ---------------------------------------------------------------------------


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """Unified auth dependency for mixed human + agent endpoints.

    Always carries:
      principal_type: "user" | "agent" | "keycloak"
      sub:            identity subject (SPIFFE URI for agents)

    Agent tokens additionally carry:
      agent_type, scopes, role_ids, spiffe_id, jti

    Revocation is enforced for agent tokens (jti blocklist + agent-level block).
    """
    from services.common import audit_events  # lazy — avoids circular at module load

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials

    # 1. Human HMAC JWT
    try:
        payload = decode_access_token(token)
        # Security audit P1-5: this strategy previously skipped the jti
        # revocation check the other three strategies already perform —
        # a logged-out/revoked human token stayed valid here until its
        # natural expiry even after auth.py::logout revoked it.
        if await is_jti_revoked(payload.get("jti")):
            await audit_events.emit(
                "auth.denied", "deny", payload, metadata={"reason": "jti_revoked"}
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        payload["principal_type"] = "user"
        return payload
    except HTTPException:
        raise
    except JWTError:
        pass

    # 2. Pipeline-scoped SPIFFE token — checked before generic agent token so the
    #    narrower pipeline_id binding is enforced when present.
    try:
        payload = decode_pipeline_token(token)
        if await is_jti_revoked(payload.get("jti")):
            await audit_events.emit(
                "auth.denied", "deny", payload, metadata={"reason": "jti_revoked"}
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        payload["principal_type"] = "agent"
        return payload
    except HTTPException:
        raise
    except JWTError:
        pass

    # 3. Agent client_credentials JWT + revocation
    try:
        payload = decode_agent_access_token(token)

        # Revocation checks — both are Redis lookups, fall through on Redis failure
        if await is_jti_revoked(payload.get("jti")):
            await audit_events.emit(
                "auth.denied", "deny", payload, metadata={"reason": "jti_revoked"}
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if await is_agent_blocked(payload.get("client_id")):
            await audit_events.emit(
                "auth.denied", "deny", payload, metadata={"reason": "agent_blocked"}
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Agent identity has been deactivated",
                headers={"WWW-Authenticate": "Bearer"},
            )

        payload["principal_type"] = "agent"
        return payload
    except HTTPException:
        raise
    except JWTError:
        pass

    # 4. Keycloak RS256 (optional — no-op when not configured)
    kc = _try_keycloak(token)
    if kc is not None:
        kc["principal_type"] = "keycloak"
        return kc

    await audit_events.emit(
        "auth.denied", "deny", metadata={"reason": "no_valid_token"}
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ---------------------------------------------------------------------------
# Scope-aware permission dependency
# ---------------------------------------------------------------------------


def require_scope(scope: str):
    """For endpoints that accept both humans and agents.

    Agents: enforces token scopes (derived from RBAC role permission_ids).
    Humans: no scope check — use existing require_permission() for human-only routes.
    """

    async def _check(principal: dict = Depends(get_current_principal)) -> dict:
        if principal.get("principal_type") == "agent":
            if scope not in set(principal.get("scopes") or []):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Scope '{scope}' required",
                )
        return principal

    return _check
