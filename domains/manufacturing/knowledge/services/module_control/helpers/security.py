from __future__ import annotations

import secrets
from datetime import timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import Header, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.config import settings
from services.common.module_control import hash_secret, utc_now

CHALLENGE_COLLECTION = "module_control_activation_challenges"
SESSION_COLLECTION = "module_control_activation_sessions"


def _configured_password_hash() -> str:
    if settings.MODULE_CONTROL_ACTIVATION_PASSWORD_HASH:
        return settings.MODULE_CONTROL_ACTIVATION_PASSWORD_HASH
    return hash_secret(settings.MODULE_CONTROL_DEV_PASSWORD)


def _actor_id(actor: dict[str, Any] | None) -> str:
    if not actor:
        return "anonymous"
    return str(
        actor.get("user_id") or actor.get("sub") or actor.get("email") or "unknown-user"
    )


def _expired(value) -> bool:
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value < utc_now()


async def create_activation_challenge(
    db: AsyncIOMotorDatabase, *, actor: dict[str, Any] | None = None
) -> dict[str, Any]:
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = utc_now() + timedelta(
        seconds=max(settings.MODULE_CONTROL_OTP_TTL_SECONDS, 30)
    )
    challenge = {
        "challenge_id": f"mc_otp_{uuid4().hex[:16]}",
        "otp_hash": hash_secret(code),
        "actor": _actor_id(actor),
        "used": False,
        "created_at": utc_now(),
        "expires_at": expires_at,
    }
    await db[CHALLENGE_COLLECTION].insert_one(challenge)
    response = {
        "challenge_id": challenge["challenge_id"],
        "expires_at": expires_at.isoformat(),
        "delivery_mode": (
            "development_echo"
            if settings.MODULE_CONTROL_ECHO_OTP
            else "external_delivery_required"
        ),
    }
    if settings.MODULE_CONTROL_ECHO_OTP:
        response["otp_code"] = code
    return response


async def verify_activation(
    db: AsyncIOMotorDatabase,
    *,
    challenge_id: str,
    otp_code: str,
    password: str,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    challenge = await db[CHALLENGE_COLLECTION].find_one({"challenge_id": challenge_id})
    if not challenge or challenge.get("used") or _expired(challenge.get("expires_at")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Activation challenge is invalid or expired.",
        )
    if not secrets.compare_digest(
        str(challenge.get("otp_hash")), hash_secret(otp_code)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid activation OTP."
        )
    if not secrets.compare_digest(_configured_password_hash(), hash_secret(password)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid activation password.",
        )

    raw_token = f"mc_session_{secrets.token_urlsafe(32)}"
    expires_at = utc_now() + timedelta(
        seconds=max(settings.MODULE_CONTROL_SESSION_TTL_SECONDS, 60)
    )
    await db[CHALLENGE_COLLECTION].update_one(
        {"challenge_id": challenge_id}, {"$set": {"used": True, "used_at": utc_now()}}
    )
    await db[SESSION_COLLECTION].insert_one(
        {
            "token_hash": hash_secret(raw_token),
            "actor": _actor_id(actor),
            "created_at": utc_now(),
            "expires_at": expires_at,
            "revoked": False,
        }
    )
    return {"activation_token": raw_token, "expires_at": expires_at.isoformat()}


async def require_activation_session(
    x_module_control_session: str = Header(
        default="", alias="X-Module-Control-Session"
    ),
) -> str:
    if not x_module_control_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Module control activation token required.",
        )
    return x_module_control_session


async def validate_activation_session(
    db: AsyncIOMotorDatabase, token: str
) -> dict[str, Any]:
    session = await db[SESSION_COLLECTION].find_one(
        {"token_hash": hash_secret(token), "revoked": False}
    )
    if not session or _expired(session.get("expires_at")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Module control activation token is invalid or expired.",
        )
    return session
