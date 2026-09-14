"""OPA policy client — thin service-layer re-export of cerebellum.capabilities.security.opa_client.

Integration logic lives in cerebellum per INV-003.
This module exists for backward-compatible imports within the services layer.

evaluate_full(policy_path, input_data, *, default_allow=True) -> dict:
    {"allowed": bool, "obligations": [...], "source": "opa"|"fallback"|"skip"}
"""

import logging
import os
from typing import Any

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Implementation — cerebellum if available, otherwise inline HTTP fallback
# ---------------------------------------------------------------------------

try:
    from cerebellum.capabilities.security.opa_client import (
        evaluate_full,
        evaluate,
    )  # noqa: F401

except ImportError:
    import httpx as _httpx  # type: ignore[import]

    _OPA_URL: str = os.getenv("OPA_URL", "").rstrip("/")
    _OPA_TIMEOUT: float = float(os.getenv("OPA_TIMEOUT_SECONDS", "2"))

    async def evaluate_full(  # type: ignore[misc]
        policy_path: str,
        input_data: dict[str, Any],
        *,
        default_allow: bool = True,
        fail_closed_on_error: bool = True,
    ) -> dict[str, Any]:
        """Doot audit P1-6 fix. Two DIFFERENT situations used to share one
        `default_allow` fallback, which is wrong — they have opposite
        correct answers:

        - OPA_URL unset: an explicit, operator-chosen "no policy layer
          configured" deployment mode (e.g. local/dev — deploy/k8s/
          configmap.yaml DOES set OPA_URL in the one real manifest, so
          this only applies where nobody wired OPA up at all). Fine to
          use `default_allow` — there's no policy engine to have failed.
        - OPA_URL IS configured but errors/times out/returns a bad
          status: the authorization service that was supposed to make
          this decision is broken. Silently answering "allow" here is
          exactly the fail-open bug the audit flagged — a protected
          action must not succeed just because the thing meant to guard
          it fell over. Fails CLOSED (allowed=False) by default; a
          caller may explicitly pass fail_closed_on_error=False for a
          specific, deliberately low-risk operation where that's a
          documented, intentional choice — no such caller exists today.
        """
        if not _OPA_URL:
            return {"allowed": default_allow, "obligations": [], "source": "skip"}
        url = f"{_OPA_URL}/v1/data/{policy_path.lstrip('/')}"
        error_fallback = default_allow if not fail_closed_on_error else False
        try:
            async with _httpx.AsyncClient(timeout=_OPA_TIMEOUT) as client:
                r = await client.post(url, json={"input": input_data})
                if r.status_code == 200:
                    result = r.json().get("result", default_allow)
                    if isinstance(result, bool):
                        return {"allowed": result, "obligations": [], "source": "opa"}
                    if isinstance(result, dict):
                        out = {
                            "allowed": bool(result.get("allow", default_allow)),
                            "obligations": result.get("obligations", []),
                            "reason": result.get("deny_reason", ""),
                            "source": "opa",
                        }
                        # Runtime Approval Gate: mirror opa_client.py's
                        # pass-through of the policy's own approval-mode/
                        # risk fields when present -- purely additive.
                        for key in (
                            "approval_mode",
                            "risk_level",
                            "policy_rule",
                            "requires_hitl",
                        ):
                            if key in result:
                                out[key] = result[key]
                        return out
                else:
                    logger.warning(
                        "OPA returned %d for %s — configured-but-unavailable, defaulting to allow=%s",
                        r.status_code,
                        policy_path,
                        error_fallback,
                    )
        except Exception as exc:
            logger.warning(
                "OPA unreachable (%s), configured-but-unavailable, defaulting to allow=%s: %s",
                policy_path,
                error_fallback,
                exc,
            )
        return {"allowed": error_fallback, "obligations": [], "source": "fallback"}

    async def evaluate(  # type: ignore[misc]
        policy_path: str,
        input_data: dict[str, Any],
        *,
        default_allow: bool = True,
        fail_closed_on_error: bool = True,
    ) -> bool:
        result = await evaluate_full(
            policy_path,
            input_data,
            default_allow=default_allow,
            fail_closed_on_error=fail_closed_on_error,
        )
        return result["allowed"]


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def require_opa(policy_path: str, *, action: str = "", resource: str = ""):
    """FastAPI dependency — evaluates an OPA policy with principal context.

    Principal resolution here is OPTIONAL, unlike get_current_principal (which
    401s on a missing Bearer token) — this is what let this dependency sit
    with zero callers: every real route in this app accepts human auth with
    no Bearer token at all (X-User-ID header, dev mode), and get_current_
    principal would have 401'd every one of them. A missing/invalid token
    resolves to an anonymous, non-agent principal instead, so it composes
    safely alongside require_permission — agentos/routes/allow's own
    `allow if principal_type != "agent"` rule passes humans straight through
    unchanged, and only a Bearer token that actually resolves to an agent
    principal is evaluated against OPA.

    Falls back to allow only when OPA is not configured at all (OPA_URL
    unset) — an explicit deployment choice, not a runtime failure. When
    OPA_URL IS configured but the service is unreachable, times out, or
    errors, this now fails CLOSED (denies) rather than silently
    allowing — see evaluate_full's own docstring (Doot audit P1-6 fix).
    """

    async def _check(request: Request) -> dict:
        principal: dict[str, Any] = {}
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                from services.common.agent_auth import get_current_principal
                from fastapi.security import HTTPAuthorizationCredentials

                creds = HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials=auth_header[7:]
                )
                principal = await get_current_principal(creds)
            except HTTPException:
                principal = {}

        input_data: dict[str, Any] = {
            "principal": {
                "sub": principal.get("sub"),
                "principal_type": principal.get("principal_type"),
                "agent_type": principal.get("agent_type"),
                "scopes": principal.get("scopes", []),
                "role_ids": principal.get("role_ids", []),
                "spiffe_id": principal.get("spiffe_id"),
                "mtls_verified": principal.get("mtls_verified", False),
            },
            "action": action,
            "resource": resource,
        }
        # default_allow=True must be explicit here: this function's own
        # docstring promises "falls back to allow only when OPA is not
        # configured" (OPA_URL unset), but evaluate()'s own default for
        # default_allow differs between backends -- False on the cerebellum
        # implementation (used whenever that package is importable) vs True
        # on this module's inline fallback -- so omitting it silently denied
        # every request through an unconfigured OPA whenever cerebellum
        # happened to be installed.
        allowed = await evaluate(policy_path, input_data, default_allow=True)
        if not allowed:
            logger.warning(
                "OPA denied: policy=%s principal=%s action=%s",
                policy_path,
                principal.get("sub"),
                action,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Policy '{policy_path}' denied this request",
            )
        return principal

    return _check
