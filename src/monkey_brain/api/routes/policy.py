"""Policy and auth introspection endpoints for the MonkeyBrain runtime.

POST /api/v1/agentos/policy/evaluate
    Run AuthPolicyAgent decision for a given principal + action + resource.
    Body carries the full context; principal comes from the Bearer token.

GET /api/v1/agentos/auth/principal
    Return the resolved principal for the current token (debug / health check).

GET /api/v1/agentos/policy/trust-domains
    List federated trust domains (requires perm-view-policy).

GET /api/v1/agentos/policy/roles
    List active roles from the Policy Control Plane (requires perm-view-policy).

/auth/principal and /policy/evaluate use optional multi-strategy auth:
  human HMAC JWT → agent client_credentials JWT → Keycloak RS256.

When AGENTOS_AUTH_REQUIRED is not "true" principal resolution is soft on the
first two endpoints; /policy/trust-domains and /policy/roles still gate with
require_permission.

/policy/evaluate fails closed in production (AGENTOS_AUTH_REQUIRED=true)
when AuthPolicyAgent is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.policy")

router = APIRouter()


class PolicyEvaluateRequest(BaseModel):
    action: str = Field(default="query", description="Operation being requested")
    resource: str = Field(default="", description="Target resource identifier")
    required_scopes: list[str] = Field(default_factory=list)
    opa_policy_path: str = Field(default="agent/mesh/allow")


def _get_principal_optional():
    """Return principal if token present, None otherwise. Never raises 401."""
    try:
        from services.common.agent_auth import get_current_principal, _bearer
        from fastapi.security import HTTPAuthorizationCredentials

        async def _dep(
            credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
        ) -> dict | None:
            if credentials is None:
                return None
            try:
                return await get_current_principal(credentials)
            except Exception:
                return None

        return _dep
    except ImportError:

        async def _no_auth() -> dict | None:
            return None

        return _no_auth


_optional_principal = _get_principal_optional()


@router.get("/auth/principal", tags=["Auth"])
async def get_principal(
    principal: dict | None = Depends(_optional_principal),
) -> JSONResponse:
    """Return the resolved principal for the current Bearer token.

    Useful for debugging token content and SPIFFE identity without calling
    the auth service directly.
    """
    if principal is None:
        return JSONResponse({"principal": None, "authenticated": False})
    safe = {k: v for k, v in principal.items() if k not in ("_cert_obj",)}
    return JSONResponse({"principal": safe, "authenticated": True})


@router.post("/policy/evaluate", tags=["Policy"])
@idempotent("policy.evaluate_policy")
async def evaluate_policy(
    body: PolicyEvaluateRequest,
    request: Request,
    principal: dict | None = Depends(_optional_principal),
) -> JSONResponse:
    """Run the AuthPolicyAgent decision stack for a given action + resource.

    The principal is resolved from the Bearer token (multi-strategy: human JWT,
    agent JWT, or Keycloak). When no token is present, principal is treated as
    anonymous and the decision will typically be 'denied' for protected resources.

    OPA is consulted when OPA_URL is configured; otherwise falls through to allow.
    Every decision is emitted as an audit event to indus.audit.queue.
    Response includes obligations and dynamic context (risk level, reliability score).
    """
    p: dict[str, Any] = principal or {}

    # Attach mTLS cert info if available
    mtls_cert = getattr(request.state, "mtls_cert", None)
    if mtls_cert:
        p["mtls_verified"] = True
        p["cert_subject"] = mtls_cert.get("subject")
        p["cert_fingerprint"] = mtls_cert.get("fingerprint")

    context: dict[str, Any] = {
        "principal": p,
        "action": body.action,
        "resource": body.resource,
        "required_scopes": body.required_scopes,
        "opa_policy_path": body.opa_policy_path,
    }

    # Run AuthPolicyAgent
    try:
        from broca.agents.auth_policy import AuthPolicyAgent

        agent = AuthPolicyAgent()
        result = await agent.handle(context)
        payload = result.payload if hasattr(result, "payload") else result
        return JSONResponse({"decision": payload, "reward": getattr(result, "reward", None)})
    except Exception as exc:
        logger.warning("AuthPolicyAgent unavailable: %s", exc)
        from src.monkey_brain.api.dependencies import auth_required

        if auth_required():
            return JSONResponse(
                status_code=503,
                content={
                    "decision": {"allowed": False, "reason": "agent_unavailable"},
                    "fallback": True,
                },
            )
        return JSONResponse(
            {
                "decision": {
                    "allowed": True,
                    "reason": "agent_unavailable_dev_fallback",
                },
                "fallback": True,
            }
        )


# ---------------------------------------------------------------------------
# Policy Control Plane endpoints
# ---------------------------------------------------------------------------


@router.get("/policy/trust-domains", tags=["Policy"])
async def list_trust_domains(
    request: Request, user_id: str = Depends(require_permission("perm-view-policy"))
) -> JSONResponse:
    """List all registered federated trust domains."""
    pcp = getattr(request.app.state, "_pcp", None)
    if not pcp:
        return JSONResponse(status_code=503, content={"error": "Policy Control Plane unavailable"})
    domains = await pcp.list_trust_domains()
    return JSONResponse({"trust_domains": domains})


@router.get("/policy/roles", tags=["Policy"])
async def list_policy_roles(
    request: Request, user_id: str = Depends(require_permission("perm-view-policy"))
) -> JSONResponse:
    """List all active roles as seen by the Policy Control Plane."""
    pcp = getattr(request.app.state, "_pcp", None)
    if not pcp:
        return JSONResponse(status_code=503, content={"error": "Policy Control Plane unavailable"})
    roles = await pcp.list_roles()
    return JSONResponse({"roles": roles})
