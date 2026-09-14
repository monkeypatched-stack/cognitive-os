"""Security audit P1-5 regression: a revoked human ACCESS token must
actually stop authenticating, not just its refresh token.

Before this fix, create_access_token() minted no jti at all, and
logout() only revoked the refresh token — the access token every
get_current_user()/require_permission() call actually validates stayed
usable until its natural expiry (services/common/config.py's
ACCESS_TOKEN_EXPIRE_MINUTES) even after "logout". These tests exercise
the real jti-blocklist round trip (services.auth.helpers.revocation)
against a real local Redis, the same mechanism agent tokens already
used before this fix extended it to human tokens.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

# Imported once here, at collection time, under REAL (unpatched) settings —
# services.auth.routers.auth builds its FastAPI routes (Cookie(alias=...))
# at import time, and re-importing it for the first time from inside a
# fake_settings-patched test would bind that alias to a MagicMock instead
# of a string. Once it's in sys.modules, later imports (including the
# monkeypatch.setattr targets below) just reuse this same real module.
from services.auth.routers import auth as _auth_router_module  # noqa: F401


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.asyncio
async def test_access_token_carries_a_jti(fake_settings, monkeypatch):
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    from services.auth.helpers.tokens import decode_access_token, create_access_token

    token = create_access_token("u1", "u@example.com", "user", permissions=["perm-view-x"])
    payload = decode_access_token(token)
    assert payload.get("jti"), "access tokens must carry a jti to be individually revocable"


@pytest.mark.asyncio
async def test_revoked_access_token_is_denied_by_get_current_user(fake_settings, monkeypatch):
    """The core invariant: token issued -> request succeeds -> revoked ->
    the SAME access token -> request DENIED."""
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    from services.auth.helpers.revocation import block_jti
    from services.auth.helpers.tokens import create_access_token, decode_access_token
    from services.common.auth import get_current_user

    token = create_access_token("u1", "u@example.com", "user", permissions=["perm-view-x"])

    # Succeeds before revocation.
    user = await get_current_user(_creds(token))
    assert user["user_id"] == "u1"

    # Revoke exactly the way logout() does — via the same jti-blocklist
    # write-side agent tokens already used.
    jti = decode_access_token(token)["jti"]
    await block_jti(jti)

    # Denied afterward, same token, no new token minted.
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_creds(token))
    assert exc.value.status_code == 401
    assert "revoked" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_a_different_valid_token_is_unaffected_by_someone_elses_revocation(fake_settings, monkeypatch):
    """Revocation must be scoped to the specific jti, not global."""
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    from services.auth.helpers.revocation import block_jti
    from services.auth.helpers.tokens import create_access_token, decode_access_token
    from services.common.auth import get_current_user

    token_a = create_access_token("alice", "a@example.com", "user")
    token_b = create_access_token("bob", "b@example.com", "user")

    await block_jti(decode_access_token(token_a)["jti"])

    with pytest.raises(HTTPException):
        await get_current_user(_creds(token_a))

    # bob's own, different token is untouched.
    user_b = await get_current_user(_creds(token_b))
    assert user_b["user_id"] == "bob"


@pytest.mark.asyncio
async def test_logout_revokes_the_presented_access_token(fake_settings, monkeypatch):
    """logout() itself (not just the underlying primitive) must revoke
    the caller's own access token by jti, in addition to the refresh
    token it already revoked."""
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    monkeypatch.setattr(
        "services.auth.routers.auth._record_login_event",
        lambda *a, **k: _noop(),
    )
    from fastapi import Response
    from services.auth.helpers.revocation import is_jti_revoked
    from services.auth.helpers.tokens import create_access_token, decode_access_token
    from services.auth.routers.auth import logout
    from services.common.auth import get_current_user

    token = create_access_token("u1", "u@example.com", "user")
    jti = decode_access_token(token)["jti"]
    assert not await is_jti_revoked(jti)

    fake_request = _FakeRequest(f"Bearer {token}")
    await logout(response=Response(), request=fake_request, refresh_token=None, db=None)

    assert await is_jti_revoked(jti)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_creds(token))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_logout_without_a_bearer_token_still_revokes_only_the_refresh_cookie(fake_settings, monkeypatch):
    """Backward compatibility: a client whose access token already
    expired must still be able to log out via the refresh cookie alone
    — logout() must not start requiring a bearer token."""
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    revoke_calls = []
    monkeypatch.setattr(
        "services.auth.routers.auth._record_login_event",
        lambda *a, **k: _noop(),
    )
    monkeypatch.setattr(
        "services.auth.routers.auth.revoke_refresh_token",
        lambda tok: revoke_calls.append(tok) or _noop(),
    )
    from fastapi import Response
    from services.auth.routers.auth import logout

    fake_request = _FakeRequest(None)
    result = await logout(
        response=Response(),
        request=fake_request,
        refresh_token="some-refresh-token",
        db=None,
    )
    assert result == {"message": "Logged out"}
    assert revoke_calls == ["some-refresh-token"]


@pytest.mark.asyncio
async def test_agent_auth_get_current_principal_also_honors_revocation(fake_settings, monkeypatch):
    """Security audit P1-5 secondary gap: the mixed human+agent
    dependency (get_current_principal) previously skipped the
    revocation check for its human-JWT strategy entirely, even though
    the agent-token strategies right next to it always checked it."""
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    from services.auth.helpers.revocation import block_jti
    from services.auth.helpers.tokens import create_access_token, decode_access_token
    from services.common.agent_auth import get_current_principal

    token = create_access_token("u1", "u@example.com", "user")
    principal = await get_current_principal(_creds(token))
    assert principal["principal_type"] == "user"

    await block_jti(decode_access_token(token)["jti"])

    with pytest.raises(HTTPException) as exc:
        await get_current_principal(_creds(token))
    assert exc.value.status_code == 401


async def _noop(*a, **k):
    return None


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — logout() only reads
    request.headers.get("authorization")."""

    def __init__(self, authorization: str | None):
        self.headers = {"authorization": authorization} if authorization else {}
