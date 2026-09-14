"""Token and agent revocation store (Redis-backed).

Three revocation targets:
  jti          — a specific access token (blocked by token ID, auto-expires at token TTL)
  client_id    — an entire agent's identity (all tokens rejected until re-activated)
  agent        — convenience: marks is_active=False in MongoDB AND blocks client_id in Redis

Falls back gracefully when Redis is unreachable — logs a warning and allows the
request rather than creating a hard dependency on Redis availability.
"""

import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.config import settings
from services.auth.helpers.agent_tokens import AGENT_ACCESS_TOKEN_MINUTES

logger = logging.getLogger(__name__)

_memory_jti: set[str] = set()
_redis = None


def _get_redis():
    """Lazy singleton — constructed on first real use, not at import time.

    redis.asyncio is imported here so a missing redis package becomes a
    runtime revocation-check failure (fail-closed in production) rather
    than an import-time crash of every auth route.
    """
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


_ACCESS_TTL = AGENT_ACCESS_TOKEN_MINUTES * 60  # seconds — matches token expiry
_AGENT_BLOCK_TTL = 60 * 60 * 24 * 30  # 30 days

_AGENT_COLLECTION = "agent_identities"


# ---------------------------------------------------------------------------
# JTI blocklist (individual access tokens)
# ---------------------------------------------------------------------------


async def block_jti(jti: str) -> None:
    """Add an access token's jti to the blocklist."""
    try:
        await _get_redis().setex(f"agent:jti:{jti}", _ACCESS_TTL, "revoked")
        _memory_jti.add(jti)
    except Exception as exc:
        _memory_jti.add(jti)
        try:
            from src.monkey_brain.kernel.production_gates import insecure_dev_mode

            if insecure_dev_mode():
                logger.warning(
                    "Redis unavailable, jti %s blocked in-process only: %s", jti, exc
                )
                return
        except Exception:
            pass
        logger.error("Redis unavailable, cannot durably block jti %s: %s", jti, exc)
        raise


async def is_jti_revoked(jti: str | None) -> bool:
    """Return True if this jti has been explicitly revoked."""
    if not jti:
        return False
    if jti in _memory_jti:
        return True
    try:
        return await _get_redis().exists(f"agent:jti:{jti}") == 1
    except Exception as exc:
        try:
            from src.monkey_brain.kernel.production_gates import insecure_dev_mode

            if insecure_dev_mode():
                logger.warning(
                    "Redis unavailable, jti revocation check skipped: %s", exc
                )
                return False
        except Exception:
            pass
        logger.error("Redis unavailable, jti revocation check failing closed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Agent-level block (all tokens for a client_id)
# ---------------------------------------------------------------------------


async def block_agent(client_id: str) -> None:
    """Block all tokens for a given client_id (marks agent as compromised/retired)."""
    try:
        await _get_redis().setex(
            f"agent:revoked:{client_id}", _AGENT_BLOCK_TTL, "revoked"
        )
    except Exception as exc:
        logger.warning("Redis unavailable, agent %s not blocked: %s", client_id, exc)


async def is_agent_blocked(client_id: str | None) -> bool:
    """Return True if the entire agent identity has been revoked."""
    if not client_id:
        return False
    try:
        return await _get_redis().exists(f"agent:revoked:{client_id}") == 1
    except Exception as exc:
        logger.warning("Redis unavailable, agent revocation check skipped: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Full agent deactivation (Redis + MongoDB)
# ---------------------------------------------------------------------------


async def deactivate_agent(
    client_id: str, db: AsyncIOMotorDatabase, reason: str = ""
) -> dict:
    """Deactivate an agent: block in Redis and set is_active=False in MongoDB.

    Returns a summary of what was done.
    """
    await block_agent(client_id)
    result = await db[_AGENT_COLLECTION].update_one(
        {"client_id": client_id},
        {"$set": {"is_active": False, "deactivation_reason": reason}},
    )
    return {
        "client_id": client_id,
        "redis_blocked": True,
        "mongo_updated": result.modified_count > 0,
        "reason": reason,
    }


async def reactivate_agent(client_id: str, db: AsyncIOMotorDatabase) -> dict:
    """Reverse a deactivation (admin action)."""
    try:
        await _get_redis().delete(f"agent:revoked:{client_id}")
    except Exception as exc:
        logger.warning("Redis unavailable during reactivation: %s", exc)
    result = await db[_AGENT_COLLECTION].update_one(
        {"client_id": client_id},
        {"$set": {"is_active": True}, "$unset": {"deactivation_reason": ""}},
    )
    return {
        "client_id": client_id,
        "reactivated": result.modified_count > 0,
    }
