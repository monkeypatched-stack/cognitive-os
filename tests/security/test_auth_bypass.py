"""Authentication bypass regression — get_current_user MUST validate the token.

Previously it ignored the token entirely and returned a hardcoded admin with
permissions ["*"] for ANY bearer string, across 134 router files / 157 routes.
Also proves the tenant claim is put into context (the tenancy chokepoint).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.asyncio
async def test_garbage_token_is_rejected(fake_settings, monkeypatch):
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    from services.common.auth import get_current_user

    with pytest.raises(HTTPException) as exc:
        await get_current_user(_creds("not-a-real-token"))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_missing_credentials_rejected(fake_settings, monkeypatch):
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    from services.common.auth import get_current_user

    with pytest.raises(HTTPException) as exc:
        await get_current_user(None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_token_authenticates_and_sets_tenant(fake_settings, monkeypatch):
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    from services.auth.helpers.tokens import create_access_token
    from services.common.auth import get_current_user
    from services.common.tenant_scope import get_tenant, set_tenant

    set_tenant("")
    token = create_access_token("u1", "u@example.com", "user", permissions=["perm-view-x"], tenant="acme")
    user = await get_current_user(_creds(token))
    assert user["user_id"] == "u1"
    assert user["permissions"] == ["perm-view-x"]  # from the VERIFIED claims
    assert get_tenant() == "acme"  # tenant provisioned into context


def test_wildcard_permission_grants_access(fake_settings, monkeypatch):
    """A holder of "*" was previously DENIED everything ({"*"} & {"perm-x"} == empty)."""
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    from services.common.auth import require_permission, _DEV_ADMIN

    check = require_permission("perm-view-x")
    assert check(current_user=_DEV_ADMIN) is _DEV_ADMIN
