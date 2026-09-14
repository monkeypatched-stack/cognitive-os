"""Actor Profile API Routes — REST endpoints for actor profile operations.

Endpoints:
- GET /api/v1/actors/{actor_id}/profile - Get actor profile
- PUT /api/v1/actors/{actor_id}/profile - Update actor profile
- GET /api/v1/actors/{actor_id}/account - Get actor account
- PUT /api/v1/actors/{actor_id}/account - Update actor account (set/change password here)
- POST /api/v1/actors/{actor_id}/login - Login
- POST /api/v1/actors/{actor_id}/logout - Logout (invalidates all active sessions)
- GET /api/v1/actors/{actor_id}/sessions - List active sessions
- POST /api/v1/actors/{actor_id}/otp/request - Request an OTP code
- POST /api/v1/actors/{actor_id}/otp/verify - Verify an OTP code and log in

Gate 7 (Security) finding: login/logout/sessions were entirely fake —
login() accepted ANY email/password and returned a hardcoded
"mock_token_"+actor_id string; logout() and sessions() always returned
canned/empty responses. kernel/login_info.py already had a real,
complete implementation (PBKDF2-HMAC-SHA256 password hashing,
constant-time verification, account lockout, session tracking) that
nothing in this file ever called — this now actually calls it, backed
by kernel/login_store.py's Redis persistence (same pattern as every
other store this session), and issues real signed JWTs via the same
services.auth.helpers.tokens.create_access_token every other route's
Bearer-token auth already verifies (api/dependencies.py::
require_permission).

OTP: kernel/login_info.py::LoginInfo.generate_otp()/verify_otp() are
equally real (6-digit code, 5-minute expiry, 3-attempt limit, constant-
time comparison via hmac.compare_digest, auto-marks email/mobile
verified on success) and were equally never called from anywhere. The
one genuine gap: this codebase has NO real email/SMS delivery
integration anywhere (checked — no SMTP/SendGrid/Twilio/etc.), so an
OTP has nowhere real to be sent. Rather than silently return the code
in the API response as if that were normal (which would defeat the
entire point of a second factor — anyone with API access could read
it), the code is only ever included in the response when
dependencies.auth_required() is False (the same dev-mode escape hatch
every other auth check in this codebase already respects), logged as a
loud warning. In a real deployment (auth required), /otp/request
generates and stores a real, correctly-expiring code but does not
return it — honestly reflecting that delivery isn't wired up yet,
rather than pretending it is.

profile GET/PUT now genuinely persist (kernel/actor_profile/persistence.py
::ActorProfilePersistence, via kernel/profile.py::Profile) — previously
real stubs with no persistence at all, closed the same way login/logout/
sessions were: a real, already-built implementation existed
(ActorProfilePersistence, MongoDB-backed through the same get_db_pool()
singleton ActorStateStore's confirmed-live belief persistence already
uses) but nothing had ever wired it to an actual route, and it had its
own real bug (a bare `time.time()` call with no `import time`) that would
have raised the instant it was ever exercised. Still unauthenticated —
name/bio/location/etc. carry no credential or session material, so this
intentionally does not gate behind require_self_or_permission the way
account/sessions do.

Production Hardening audit finding (later pass): despite the above,
get_account/update_account/list_sessions had NO auth dependency at
all — the Gate 7 pass fixed the FAKE auth in login/logout/otp but
never added a real guard to these three, even though update_account is
exactly where a real password gets set (PBKDF2-hashed via
kernel/login_info.py, genuinely persisted) and get_account/
list_sessions leak real account state (email, active session count).
Any unauthenticated caller who knew/guessed an actor_id could read or
take over any actor's account. Fixed: all three now require
require_self_or_permission("perm-manage-actors") (api/dependencies.py)
— the actor themselves (their own token's `sub` matches the path
actor_id) is always allowed, matching login()'s own permissions=[]
tokens; anyone else needs perm-manage-actors.
"""

from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.monkey_brain.api.dependencies import require_self_or_permission
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.api.actor_profile")

router = APIRouter(prefix="/api/v1/actors", tags=["actor-profile"])


def _mask(destination: str) -> str:
    """t***@example.com / +1***4567 — never echo the full destination
    back in a response that a client-side log could capture."""
    if "@" in destination:
        local, _, domain = destination.partition("@")
        return f"{local[:1]}***@{domain}" if local else destination
    if len(destination) > 4:
        return f"{destination[:2]}***{destination[-4:]}"
    return "***"


class ProfileUpdateRequest(BaseModel):
    """Request to update actor profile."""

    name: str | None = None
    email: str | None = None
    bio: str | None = None
    location: str | None = None
    preferred_language: str | None = None
    timezone: str | None = None
    metadata: dict[str, Any] | None = None


class AccountUpdateRequest(BaseModel):
    """Request to update actor account. Setting `password` (and, the
    first time, `email`) is how an actor's login credentials get
    established — there is no separate registration endpoint."""

    email: str | None = None
    password: str | None = None
    subscription: str | None = None
    metadata: dict[str, Any] | None = None


class LoginRequest(BaseModel):
    """Request to login."""

    email: str
    password: str


class LoginResponse(BaseModel):
    """Response from login."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600


class OTPRequestRequest(BaseModel):
    destination: str | None = None
    """email or mobile to send the OTP to — defaults to whatever's
    already on file for this actor if omitted."""


class OTPRequestResponse(BaseModel):
    status: str = "sent"
    destination: str = ""
    expires_in: int = 300
    otp_code: str | None = None
    """Only populated when auth is not required (dev mode) — see module
    docstring. None in a real deployment: there is no delivery channel
    to have sent it through, so it is honestly withheld rather than
    echoed back as if that were normal."""


class OTPVerifyRequest(BaseModel):
    code: str


@router.get("/{actor_id}/profile")
async def get_profile(actor_id: str):
    """Get actor profile — real persisted data (kernel/profile.py::Profile)
    when one has been saved, an honest "not set" shape otherwise."""
    from src.monkey_brain.kernel.actor_profile.persistence import (
        get_actor_profile_persistence,
    )

    data = get_actor_profile_persistence().load_profile(actor_id)
    if data is None:
        return {"actor_id": actor_id, "status": "ok", "profile_set": False}
    return {"actor_id": actor_id, "status": "ok", "profile_set": True, **data}


@router.put("/{actor_id}/profile")
@idempotent("actor_profile.update_profile")
async def update_profile(actor_id: str, request: ProfileUpdateRequest):
    """Update actor profile — a real, partial update against persisted
    state: loads whatever profile already exists (or starts a fresh one),
    applies only the fields actually present in the request via Profile.
    update(), and saves it back."""
    from src.monkey_brain.kernel.profile import Profile
    from src.monkey_brain.kernel.actor_profile.persistence import (
        get_actor_profile_persistence,
    )

    persistence = get_actor_profile_persistence()
    existing = persistence.load_profile(actor_id)
    profile = Profile.from_dict(existing) if existing else Profile()

    fields_updated = [k for k, v in request.dict().items() if v is not None]
    updates = {k: v for k, v in request.dict().items() if v is not None}
    profile.update(**updates)
    persistence.save_profile(actor_id, profile)

    return {"status": "updated", "actor_id": actor_id, "fields_updated": fields_updated}


@router.get("/{actor_id}/account")
async def get_account(
    actor_id: str,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors")),
):
    """Get actor account — reflects real LoginInfo state when credentials
    have been set, without ever exposing the password hash."""
    from src.monkey_brain.kernel.login_store import get_login_store

    info = get_login_store().get(actor_id)
    if info is None:
        return {"actor_id": actor_id, "status": "ok", "credentials_configured": False}
    return {
        "actor_id": actor_id,
        "status": "ok",
        "credentials_configured": True,
        "email": info.email,
        "account_status": info.status.value,
        "active_sessions": len(info.active_sessions),
    }


@router.put("/{actor_id}/account")
@idempotent("actor_profile.update_account")
async def update_account(
    actor_id: str,
    request: AccountUpdateRequest,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors")),
):
    """Update actor account. A `password` in the request sets/replaces
    this actor's login credentials via the real LoginInfo implementation
    (kernel/login_info.py) — hashed with PBKDF2-HMAC-SHA256, never stored
    or returned in plaintext."""
    from src.monkey_brain.kernel.login_info import AccountStatus, LoginInfo
    from src.monkey_brain.kernel.login_store import get_login_store

    store = get_login_store()
    info = store.get(actor_id) or LoginInfo()
    fields_updated = []

    if request.email is not None:
        info.email = request.email
        fields_updated.append("email")
    if request.password is not None:
        if not info.set_password(request.password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password too weak — needs 8+ characters with upper, lower, and a digit",
            )
        # No separate email-verification flow exists in this system —
        # setting a password is the deliberate signal this actor's
        # credentials are meant to be usable immediately, not stuck
        # permanently unauthenticated waiting on a verification step
        # nothing here ever sends.
        info.email_verified = True
        info.status = AccountStatus.ACTIVE
        fields_updated.append("password")
    if request.metadata is not None:
        info.metadata.update(request.metadata)
        fields_updated.append("metadata")

    store.save(actor_id, info)
    return {"status": "updated", "actor_id": actor_id, "fields_updated": fields_updated}


@router.post("/{actor_id}/login", response_model=LoginResponse)
@idempotent("actor_profile.login")
async def login(actor_id: str, request: LoginRequest):
    """Login and get a real, signed access token.

    Fails honestly (401/423) rather than the previous behavior of
    accepting any password and minting a fake token — no credentials
    configured for this actor is a real, distinct failure from a wrong
    password, not silently treated the same as success."""
    from src.monkey_brain.kernel.login_store import get_login_store
    from services.auth.helpers.tokens import create_access_token

    store = get_login_store()
    info = store.get(actor_id)
    if info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No credentials configured for this actor — set a password via PUT .../account first",
        )

    if info.is_locked:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account locked after repeated failed login attempts — try again later",
        )

    if request.email != info.email or not info.verify_password(request.password):
        locked = info.record_failed_login()
        store.save(actor_id, info)
        detail = "Account locked after repeated failed login attempts" if locked else "Invalid email or password"
        raise HTTPException(
            status_code=(status.HTTP_423_LOCKED if locked else status.HTTP_401_UNAUTHORIZED),
            detail=detail,
        )

    info.create_session()
    store.save(actor_id, info)

    # Production Hardening audit finding (login flow): permissions=[]
    # meant a real actor's own token could never pass require_permission
    # on ANY route, including POST /prompt itself -- verified live under
    # AGENTOS_AUTH_REQUIRED=true: a real actor who successfully logged in
    # got 403 "Permission 'perm-execute-prompt' required" trying to buy
    # groceries, the product's core function. perm-execute-prompt is safe
    # to grant broadly here specifically because /prompt is self-scoped
    # by construction -- it acts as the authenticated caller (user_id),
    # never a caller-supplied actor_id, so this permission alone can
    # never be used to act as another actor. This is NOT a general fix
    # for every permission-gated route: actors.py's goals/addresses/
    # beliefs/etc. take actor_id as a PATH parameter, so granting THOSE
    # permissions broadly would let any actor read/write another actor's
    # data by changing the path -- those need require_self_or_permission
    # (self-check by identity, not a broad grant), not a token change.
    token = create_access_token(
        user_id=actor_id,
        email=info.email,
        role="actor",
        permissions=["perm-execute-prompt"],
    )
    return LoginResponse(access_token=token, token_type="bearer", expires_in=3600)


@router.post("/{actor_id}/logout")
@idempotent("actor_profile.logout")
async def logout(actor_id: str):
    """Logout — invalidates every active session for this actor."""
    from src.monkey_brain.kernel.login_store import get_login_store

    store = get_login_store()
    info = store.get(actor_id)
    if info is None:
        return {"status": "logged_out", "actor_id": actor_id, "sessions_invalidated": 0}

    count = info.invalidate_all_sessions()
    store.save(actor_id, info)
    return {"status": "logged_out", "actor_id": actor_id, "sessions_invalidated": count}


@router.get("/{actor_id}/sessions")
async def list_sessions(
    actor_id: str,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors")),
):
    """List this actor's real active (non-expired) sessions."""
    from src.monkey_brain.kernel.login_store import get_login_store

    info = get_login_store().get(actor_id)
    if info is None:
        return {"actor_id": actor_id, "sessions": []}
    return {
        "actor_id": actor_id,
        "sessions": [s.to_dict() for s in info.active_sessions],
    }


@router.post("/{actor_id}/otp/request", response_model=OTPRequestResponse)
@idempotent("actor_profile.request_otp")
async def request_otp(actor_id: str, body: OTPRequestRequest):
    """Generate a real, time-limited OTP (kernel/login_info.py::
    LoginInfo.generate_otp()) — see module docstring for why the code
    itself is only returned in dev mode."""
    from src.monkey_brain.api.dependencies import auth_required
    from src.monkey_brain.kernel.login_info import LoginInfo
    from src.monkey_brain.kernel.login_store import get_login_store

    store = get_login_store()
    info = store.get(actor_id) or LoginInfo()

    destination = body.destination or info.mobile or info.email
    if not destination:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No destination on file for this actor — pass one explicitly, "
            "or set an email via PUT .../account first",
        )
    if body.destination and "@" in body.destination and not info.email:
        info.email = body.destination

    code = info.generate_otp(destination=destination)
    store.save(actor_id, info)

    response = OTPRequestResponse(status="sent", destination=_mask(destination), expires_in=300)
    if not auth_required():
        logger.warning(
            "OTP code returned in response for actor=%r — dev mode only "
            "(AGENTOS_AUTH_REQUIRED disabled); never do this in production.",
            actor_id,
        )
        response.otp_code = code
    return response


@router.post("/{actor_id}/otp/verify", response_model=LoginResponse)
@idempotent("actor_profile.verify_otp")
async def verify_otp(actor_id: str, body: OTPVerifyRequest):
    """Verify an OTP code (constant-time, max 3 attempts, 5-minute
    expiry — all real, in kernel/login_info.py::OTPInfo.verify()) and,
    on success, log in exactly like password login does: a real session
    plus a real signed JWT."""
    from src.monkey_brain.kernel.login_store import get_login_store
    from services.auth.helpers.tokens import create_access_token

    store = get_login_store()
    info = store.get(actor_id)
    if info is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No OTP has been requested for this actor",
        )

    if not info.verify_otp(body.code):
        store.save(actor_id, info)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired OTP code",
        )

    info.create_session()
    store.save(actor_id, info)

    # Production Hardening audit finding (login flow): permissions=[]
    # meant a real actor's own token could never pass require_permission
    # on ANY route, including POST /prompt itself -- verified live under
    # AGENTOS_AUTH_REQUIRED=true: a real actor who successfully logged in
    # got 403 "Permission 'perm-execute-prompt' required" trying to buy
    # groceries, the product's core function. perm-execute-prompt is safe
    # to grant broadly here specifically because /prompt is self-scoped
    # by construction -- it acts as the authenticated caller (user_id),
    # never a caller-supplied actor_id, so this permission alone can
    # never be used to act as another actor. This is NOT a general fix
    # for every permission-gated route: actors.py's goals/addresses/
    # beliefs/etc. take actor_id as a PATH parameter, so granting THOSE
    # permissions broadly would let any actor read/write another actor's
    # data by changing the path -- those need require_self_or_permission
    # (self-check by identity, not a broad grant), not a token change.
    token = create_access_token(
        user_id=actor_id,
        email=info.email,
        role="actor",
        permissions=["perm-execute-prompt"],
    )
    return LoginResponse(access_token=token, token_type="bearer", expires_in=3600)
