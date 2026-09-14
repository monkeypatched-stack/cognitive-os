"""Pipeline-scoped SPIFFE token issuer and validator.

Every pipeline step gets a short-lived token (PIPELINE_TTL_SECONDS = 120 s) whose
sub claim is the canonical cognitive SPIFFE URI:

  spiffe://{trust_domain}/pipeline/{pipeline_id}/step/{step_name}/agent/{agent_role}

This ties the bearer token to a specific step in a specific pipeline run.
PolicyAgent validates the SPIFFE URI structure against the live Redis attestation
record to prevent token reuse across steps or pipelines.

Usage in Runtime (per step):
  token = await issue_step_token(pipeline_id, step_name, agent_role, agent_identity)
  state["__pipeline_token__"] = token
  state["__pipeline_id__"]    = pipeline_id
  state["__step_name__"]      = step_name

Usage in agent/capability receiving a call:
  claims = verify(bearer_token, expected_pipeline_id=state.get("__pipeline_id__"))

No-op when auth is not configured (ACCESS_TOKEN_SECRET absent) — runtime still
works without a secrets store.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


async def issue(
    pipeline_id: str,
    agent_identity: dict[str, Any] | None = None,
) -> str | None:
    """Issue a pipeline-level token (no step scoping).

    Kept for backward-compatibility. Prefer issue_step_token() for new code.
    """
    try:
        from services.auth.helpers.agent_tokens import create_pipeline_token

        identity = agent_identity or {}
        spiffe_id = identity.get("spiffe_id") or os.getenv(
            "SPIFFE_ID", f"spiffe://monkeybrain/runtime/pipeline/{pipeline_id}"
        )
        return create_pipeline_token(
            pipeline_id=pipeline_id,
            spiffe_id=spiffe_id,
            client_id=identity.get("client_id", "runtime"),
            agent_type=identity.get("agent_type", "runtime"),
            scopes=identity.get("scopes", ["execute:pipeline"]),
            role_ids=identity.get("role_ids") or [],
        )
    except Exception as exc:
        logger.debug("Pipeline token issue skipped (auth not configured): %s", exc)
        return None


async def issue_step_token(
    pipeline_id: str,
    step_name: str,
    agent_role: str,
    agent_identity: dict[str, Any] | None = None,
) -> str | None:
    """Issue a step-scoped SPIFFE token with canonical cognitive URI as sub.

    sub = spiffe://{trust_domain}/pipeline/{pipeline_id}/step/{step_name}/agent/{agent_role}

    Returns None when auth secrets are not configured.
    """
    try:
        from services.auth.helpers.agent_tokens import create_pipeline_token

        identity = agent_identity or {}
        spiffe_id = identity.get("spiffe_id") or os.getenv(
            "SPIFFE_ID", f"spiffe://monkeybrain/runtime/pipeline/{pipeline_id}"
        )
        return create_pipeline_token(
            pipeline_id=pipeline_id,
            spiffe_id=spiffe_id,
            client_id=identity.get("client_id", "runtime"),
            agent_type=identity.get("agent_type", "runtime"),
            scopes=identity.get("scopes", ["execute:pipeline"]),
            role_ids=identity.get("role_ids") or [],
            step_name=step_name,
            agent_role=agent_role,
        )
    except Exception as exc:
        logger.debug("Step token issue skipped (auth not configured): %s", exc)
        return None


def verify(
    token: str,
    *,
    expected_pipeline_id: str = "",
) -> dict[str, Any]:
    """Decode and validate a pipeline-scoped token.

    Raises ValueError on pipeline_id mismatch.
    Raises jose.JWTError on invalid/expired token.
    """
    from services.auth.helpers.agent_tokens import decode_pipeline_token

    claims = decode_pipeline_token(token)
    if expected_pipeline_id and claims.get("pipeline_id") != expected_pipeline_id:
        raise ValueError(
            f"Pipeline token mismatch: token bound to {claims.get('pipeline_id')!r}, "
            f"expected {expected_pipeline_id!r}"
        )
    return claims


def extract_pipeline_id(token: str) -> str | None:
    """Return pipeline_id from a pipeline token without full validation."""
    try:
        from services.auth.helpers.agent_tokens import decode_pipeline_token

        return decode_pipeline_token(token).get("pipeline_id")
    except Exception:
        return None
