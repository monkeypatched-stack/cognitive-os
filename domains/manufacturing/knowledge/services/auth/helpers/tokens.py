import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from services.common.config import settings


def _require_secret(name: str, value: str) -> str:
    """Fail closed — never sign or verify with an empty key."""
    if not value or not value.strip():
        raise RuntimeError(
            f"{name} is not configured. "
            "Set it in your .env file or environment before starting the service."
        )
    return value


def _create_token(data: dict, secret: str, expires_delta: timedelta) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + expires_delta
    return jwt.encode(payload, secret, algorithm=settings.ALGORITHM)


def _permission_claim(permission: Any) -> str | None:
    if hasattr(permission, "model_dump"):
        permission = permission.model_dump(mode="python")
    if isinstance(permission, str):
        return permission
    if not isinstance(permission, dict):
        return None
    permission_id = permission.get("permission_id")
    return str(permission_id) if permission_id else None


def create_access_token(
    user_id: str,
    email: str,
    role: str,
    permissions: list | None = None,
    tenant: str | None = None,
    *,
    mfa_status: str = "unknown",
) -> str:
    serialized = sorted(
        {
            claim
            for permission in (permissions or [])
            if (claim := _permission_claim(permission))
        }
    )
    _mfa = (mfa_status or "unknown").strip().lower()
    if _mfa not in {"satisfied", "not_satisfied", "unknown", "not_required"}:
        _mfa = "unknown"
    claims: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "role": role,
        "permissions": serialized,
        # jti (Security audit P1-5): without a per-token id, a "revoked"
        # access token has no way to actually be looked up and rejected
        # before its natural expiry — see services.auth.helpers.revocation,
        # the same jti-blocklist mechanism agent tokens already use.
        "jti": uuid.uuid4().hex,
        # MFA evidence is minted only by this helper (trusted auth infra), never by agents.
        "mfa_status": _mfa,
    }
    # Tenant is a first-class claim: it is what get_current_user puts into context and what
    # every downstream query is filtered by. Without it there is no tenant isolation.
    if tenant:
        claims["tenant"] = tenant
    return _create_token(
        claims,
        _require_secret("ACCESS_TOKEN_SECRET", settings.ACCESS_TOKEN_SECRET),
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: str) -> str:
    return _create_token(
        {"sub": user_id},
        _require_secret("REFRESH_TOKEN_SECRET", settings.REFRESH_TOKEN_SECRET),
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def create_mfa_challenge_token(user_id: str) -> str:
    return _create_token(
        {"sub": user_id, "purpose": "mfa_challenge"},
        _require_secret("ACCESS_TOKEN_SECRET", settings.ACCESS_TOKEN_SECRET),
        timedelta(minutes=5),
    )


def decode_access_token(token: str) -> dict:
    """Raises JWTError on invalid/expired token."""
    payload = jwt.decode(
        token,
        _require_secret("ACCESS_TOKEN_SECRET", settings.ACCESS_TOKEN_SECRET),
        algorithms=[settings.ALGORITHM],
    )
    if "permissions" not in payload and isinstance(payload.get("perms"), str):
        payload["permissions"] = [
            permission for permission in payload["perms"].split(",") if permission
        ]
    return payload


def decode_refresh_token(token: str) -> dict:
    """Raises JWTError on invalid/expired token."""
    return jwt.decode(
        token,
        _require_secret("REFRESH_TOKEN_SECRET", settings.REFRESH_TOKEN_SECRET),
        algorithms=[settings.ALGORITHM],
    )


def decode_mfa_challenge_token(token: str) -> dict:
    payload = jwt.decode(
        token,
        _require_secret("ACCESS_TOKEN_SECRET", settings.ACCESS_TOKEN_SECRET),
        algorithms=[settings.ALGORITHM],
    )
    if payload.get("purpose") != "mfa_challenge":
        raise JWTError("Invalid MFA challenge token")
    return payload
