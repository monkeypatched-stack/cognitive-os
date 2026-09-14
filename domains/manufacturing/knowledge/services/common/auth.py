import logging
import os

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from services.auth.helpers.revocation import is_jti_revoked
from services.auth.helpers.tokens import decode_access_token
from services.common.compliance import set_compliance_user
from services.common.tenant_scope import set_tenant_from_claims

logger = logging.getLogger("services.auth")

bearer_scheme = HTTPBearer()


def _dev_bypass() -> bool:
    """Local-development ONLY: skip token validation and act as a default admin.

    Secure by default (off). This previously ran UNCONDITIONALLY — every request with any
    bearer string was granted admin with all permissions across 157 routes.
    """
    on = os.getenv("AUTH_DEV_BYPASS", "").strip().lower() in ("1", "true", "yes", "on")
    if on:
        logger.critical(
            "[auth] AUTH_DEV_BYPASS is ON — authentication is DISABLED. "
            "Never enable this outside local development."
        )
    return on


PERMISSION_ALIASES = {
    "perm-view-process-definitions": {"perm-view-workflows"},
    "perm-create-process-definitions": {"perm-create-workflows"},
    "perm-update-process-definitions": {"perm-update-workflows"},
    "perm-delete-process-definitions": {"perm-delete-workflows"},
}


_DEV_ADMIN = {
    "sub": "default-admin",
    "user_id": "default-admin",
    "email": "admin@example.com",
    "name": "Default Admin",
    "is_active": True,
    "permissions": ["*"],
    "roles": [{"role_id": "admin", "name": "Administrator"}],
}


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """Authenticate the request from its bearer token.

    Validates the JWT (signature + expiry), builds the caller's identity from the verified
    claims, and puts the caller's TENANT into context — the single chokepoint that makes
    tenant scoping actually bite for every downstream query.
    """
    if _dev_bypass():
        set_compliance_user(_DEV_ADMIN)
        set_tenant_from_claims({})
        return _DEV_ADMIN

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    try:
        payload = decode_access_token(
            credentials.credentials
        )  # verifies signature + exp
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )
    if not payload or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims"
        )
    # Security audit P1-5: signature+expiry alone don't reflect logout/
    # revocation — a token is only truly dead once its jti is checked
    # against the same live blocklist agent tokens already use (see
    # services.auth.helpers.revocation, and auth.py::logout's write side).
    if await is_jti_revoked(payload.get("jti")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked"
        )

    user = {
        # "sub" is kept alongside "user_id": the per-router get_current_user copies this
        # replaces returned the RAW JWT payload, and their callers read current_user["sub"].
        # Returning a superset keeps every existing consumer working after consolidation.
        "sub": payload.get("sub"),
        "user_id": payload.get("sub"),
        "email": payload.get("email"),
        "role": payload.get("role"),
        "is_active": True,
        "permissions": payload.get("permissions", []) or [],
        "roles": payload.get("roles", []) or [],
    }
    set_compliance_user(user)
    # Tenant provisioning: token claim → context → query filter (see tenant_scope).
    set_tenant_from_claims(payload)
    return user


def permission_ids(current_user: dict) -> set[str]:
    granted: set[str] = set()
    for permission in current_user.get("permissions", []):
        if isinstance(permission, str):
            granted.add(permission)
        elif isinstance(permission, dict):
            permission_id = permission.get("permission_id")
            if permission_id:
                granted.add(str(permission_id))
    return granted


def require_permission(required: str):
    def _check(current_user: dict = Depends(get_current_user)):
        granted = permission_ids(current_user)
        accepted = {required, *PERMISSION_ALIASES.get(required, set())}
        # "*" is the super-permission; without this a holder of "*" was denied EVERYTHING
        # (set intersection of {"*"} with a concrete permission is empty).
        if "*" in granted:
            return current_user
        if not (granted & accepted):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{required}' required",
            )
        return current_user

    return _check
