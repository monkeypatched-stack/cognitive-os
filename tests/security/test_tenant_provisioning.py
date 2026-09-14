"""Tenant provisioning end-to-end: token claim → context → query filter.

This is the link that was missing — without it, tenant scoping could never bite because no
request ever put a tenant in context.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from services.common.tenant_scope import (
    TENANT_FIELD,
    get_tenant,
    set_tenant,
    user_tenant,
    default_tenant,
    wrap,
)


class FakeCollection:
    name = "things"

    def __init__(self):
        self.calls = []

    def find(self, filter=None, *a, **k):
        self.calls.append(filter)
        return iter([])


def _creds(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_user_tenant_resolution():
    assert user_tenant({"tenant": "acme"}) == "acme"
    assert user_tenant({"org_id": "globex"}) == "globex"
    assert user_tenant({}) == default_tenant()  # legacy user → default tenant


@pytest.mark.asyncio
async def test_token_carries_tenant_and_scopes_queries(fake_settings, monkeypatch):
    """Login issues a token with the user's tenant; authenticating scopes every query to it."""
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    from services.auth.helpers.tokens import create_access_token
    from services.common.auth import get_current_user

    set_tenant("")
    token = create_access_token("u1", "u@x.com", "user", [], tenant="acme")
    await get_current_user(_creds(token))  # ← the chokepoint
    assert get_tenant() == "acme"

    col = FakeCollection()
    list(wrap(col).find({"k": 1}))
    assert col.calls[-1][TENANT_FIELD] == "acme"  # query filtered by the token's tenant


@pytest.mark.asyncio
async def test_legacy_token_without_tenant_claim_gets_default(fake_settings, monkeypatch):
    """Tokens issued before multi-tenancy must still scope to SOME tenant, never empty."""
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    from services.auth.helpers.tokens import create_access_token
    from services.common.auth import get_current_user

    set_tenant("")
    token = create_access_token("u1", "u@x.com", "user", [])  # no tenant arg
    await get_current_user(_creds(token))
    assert get_tenant() == default_tenant()  # not "" (which would leak/fail-closed)


@pytest.mark.asyncio
async def test_two_tenants_are_isolated(fake_settings, monkeypatch):
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    from services.auth.helpers.tokens import create_access_token
    from services.common.auth import get_current_user

    col = FakeCollection()
    for tenant in ("acme", "globex"):
        set_tenant("")
        await get_current_user(_creds(create_access_token("u", "e@x.com", "r", [], tenant=tenant)))
        list(wrap(col).find({}))
        assert col.calls[-1][TENANT_FIELD] == tenant  # each caller sees only its own tenant
