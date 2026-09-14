"""Agent token helpers — client_credentials + refresh grant for machine-to-machine auth.

Token lifecycle
  Access token:  60 min  — short-lived, bearer
  Refresh token: 24 h    — single-use, tracked in Redis by jti
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from services.common.config import settings

logger = logging.getLogger(__name__)

AGENT_ACCESS_TOKEN_MINUTES: int = 60
AGENT_REFRESH_TOKEN_HOURS: int = 24
PIPELINE_TTL_SECONDS: int = 120  # 2-minute attestation window per pipeline step


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------


def hash_client_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def generate_client_credentials() -> tuple[str, str]:
    """Return (client_id, plaintext_client_secret). Caller stores only the hash."""
    return f"agent-{secrets.token_hex(12)}", secrets.token_hex(32)


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------


def create_agent_access_token(
    client_id: str,
    agent_type: str,
    scopes: list[str],
    spiffe_id: str,
    role_ids: list[str] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": spiffe_id,  # SPIFFE URI is the canonical subject
        "client_id": client_id,
        "agent_type": agent_type,
        "scopes": scopes,  # derived from RBAC role permissions
        "role_ids": role_ids or [],
        "spiffe_id": spiffe_id,
        "grant_type": "client_credentials",
        "jti": secrets.token_hex(16),
        "iat": now,
        "exp": now + timedelta(minutes=AGENT_ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(
        payload, settings.ACCESS_TOKEN_SECRET, algorithm=settings.ALGORITHM
    )


def create_agent_refresh_token(client_id: str, spiffe_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": spiffe_id,
        "client_id": client_id,
        "token_use": "agent_refresh",
        "jti": secrets.token_hex(16),
        "iat": now,
        "exp": now + timedelta(hours=AGENT_REFRESH_TOKEN_HOURS),
    }
    return jwt.encode(
        payload, settings.REFRESH_TOKEN_SECRET, algorithm=settings.ALGORITHM
    )


# ---------------------------------------------------------------------------
# Token decoding
# ---------------------------------------------------------------------------


def create_pipeline_token(
    pipeline_id: str,
    spiffe_id: str,
    client_id: str,
    agent_type: str,
    scopes: list[str],
    role_ids: list[str] | None = None,
    step_name: str = "",
    agent_role: str = "",
) -> str:
    """Short-lived token scoped to a single pipeline step.

    When step_name is provided the sub claim becomes the canonical cognitive
    SPIFFE URI that encodes full pipeline provenance:
      spiffe://{trust_domain}/pipeline/{pipeline_id}/step/{step_name}/agent/{agent_role}

    Without step_name the sub is the caller's SPIFFE ID (pipeline-level token,
    backward-compatible).

    Additional claims:
      pipeline_id  — Pipeline.pipeline_id
      step_name    — the step this token was minted for
      agent_role   — the role/type of the agent executing this step
      token_use    — "pipeline"

    TTL is PIPELINE_TTL_SECONDS (120 s) — one attestation window.
    """
    import os as _os

    now = datetime.now(timezone.utc)

    if step_name:
        trust_domain = _os.getenv("SPIFFE_TRUST_DOMAIN", "monkeypatched.internal")
        role = agent_role or agent_type or "unknown"
        canonical_sub = (
            f"spiffe://{trust_domain}"
            f"/pipeline/{pipeline_id}"
            f"/step/{step_name}"
            f"/agent/{role}"
        )
    else:
        canonical_sub = spiffe_id

    payload: dict[str, Any] = {
        "sub": canonical_sub,
        "client_id": client_id,
        "agent_type": agent_type,
        "scopes": scopes,
        "role_ids": role_ids or [],
        "spiffe_id": canonical_sub,
        "pipeline_id": pipeline_id,
        "step_name": step_name,
        "agent_role": agent_role or agent_type,
        "grant_type": "client_credentials",
        "token_use": "pipeline",
        "jti": secrets.token_hex(16),
        "iat": now,
        "exp": now + timedelta(seconds=PIPELINE_TTL_SECONDS),
    }
    return jwt.encode(
        payload, settings.ACCESS_TOKEN_SECRET, algorithm=settings.ALGORITHM
    )


def decode_pipeline_token(token: str) -> dict[str, Any]:
    """Raises JWTError if not a valid pipeline-scoped token."""
    payload = jwt.decode(
        token, settings.ACCESS_TOKEN_SECRET, algorithms=[settings.ALGORITHM]
    )
    if payload.get("token_use") != "pipeline":
        raise JWTError("Not a pipeline-scoped token")
    if not payload.get("pipeline_id"):
        raise JWTError("pipeline_id missing from pipeline token")
    return payload


# ---------------------------------------------------------------------------
# Backward-compat alias kept so pipeline_token.py import doesn't break
# ---------------------------------------------------------------------------
PIPELINE_TOKEN_MINUTES: int = PIPELINE_TTL_SECONDS // 60  # ≈ 2 min — for display only


def decode_agent_access_token(token: str) -> dict[str, Any]:
    """Raises JWTError if invalid, expired, or not an agent/pipeline token."""
    payload = jwt.decode(
        token, settings.ACCESS_TOKEN_SECRET, algorithms=[settings.ALGORITHM]
    )
    if payload.get("grant_type") != "client_credentials":
        raise JWTError("Not an agent access token")
    return payload


def decode_agent_refresh_token(token: str) -> dict[str, Any]:
    """Raises JWTError if invalid, expired, or not an agent refresh token."""
    payload = jwt.decode(
        token, settings.REFRESH_TOKEN_SECRET, algorithms=[settings.ALGORITHM]
    )
    if payload.get("token_use") != "agent_refresh":
        raise JWTError("Not an agent refresh token")
    return payload
