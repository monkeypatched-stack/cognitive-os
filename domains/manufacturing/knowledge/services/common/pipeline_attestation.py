"""Pipeline attestation — registers the DAG contract into Redis at materialization.

When the System 2 Engine materialises a pipeline it calls register() to push:
  {
    status:              "EXECUTING",
    current_step:        "",         # updated per step by advance()
    parent_goal:         str,
    sandbox_id:          str,
    allowed_capabilities: {step_name: [cap1, cap2, ...]},
    pipeline_id:         str,
    trust_domain:        str,
  }

into Redis at key compiled_pipelines:{pipeline_id} with TTL PIPELINE_TTL_SECONDS.

This is the attestation store that OPA queries (via PolicyAgent) to verify that
a pipeline-step SPIFFE token is currently valid and authorised to invoke the
requested capability.

SPIRE integration is optional: if SPIFFE_ENDPOINT_SOCKET is set, the attestation
is also pushed to the cerebellum SPIRE client; otherwise Redis is the sole store.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any

logger = logging.getLogger(__name__)

PIPELINE_TTL_SECONDS: int = int(os.getenv("PIPELINE_TTL_SECONDS", "120"))
TRUST_DOMAIN: str = os.getenv("SPIFFE_TRUST_DOMAIN", "monkeypatched.internal")
_KEY_PREFIX = "compiled_pipelines"


def canonical_spiffe_id(pipeline_id: str, step_name: str, agent_role: str) -> str:
    """Build the canonical cognitive SPIFFE URI for a pipeline step.

    Format:
      spiffe://{trust_domain}/pipeline/{pipeline_id}/step/{step_name}/agent/{agent_role}
    """
    return (
        f"spiffe://{TRUST_DOMAIN}"
        f"/pipeline/{pipeline_id}"
        f"/step/{step_name}"
        f"/agent/{agent_role}"
    )


def _redis_key(pipeline_id: str) -> str:
    return f"{_KEY_PREFIX}:{pipeline_id}"


async def _get_redis():
    try:
        import redis.asyncio as aioredis

        url = os.getenv("REDIS_URL", "redis://localhost:6379")
        return aioredis.from_url(url, decode_responses=True)
    except Exception as exc:
        logger.warning("Pipeline attestation: Redis unavailable: %s", exc)
        return None


async def register(
    pipeline_id: str,
    steps: list[str],
    allowed_capabilities: dict[str, list[str]],
    *,
    parent_goal: str = "",
    sandbox_id: str = "",
) -> bool:
    """Register the DAG contract for a pipeline at materialisation.

    allowed_capabilities maps step_name → [capability_name, ...] so OPA can
    verify that a step only invokes the capabilities explicitly in the DAG.

    Returns True on success, False when Redis is unreachable (non-fatal).
    """
    sandbox = sandbox_id or secrets.token_hex(8)
    record = {
        "pipeline_id": pipeline_id,
        "trust_domain": TRUST_DOMAIN,
        "status": "EXECUTING",
        "current_step": steps[0] if steps else "",
        "steps": steps,
        "allowed_capabilities": allowed_capabilities,
        "parent_goal": parent_goal,
        "sandbox_id": sandbox,
    }
    r = await _get_redis()
    if r is None:
        return False
    try:
        key = _redis_key(pipeline_id)
        await r.set(key, json.dumps(record), ex=PIPELINE_TTL_SECONDS)
        logger.debug(
            "Attestation registered: pipeline=%s steps=%s ttl=%ds",
            pipeline_id,
            steps,
            PIPELINE_TTL_SECONDS,
        )
        return True
    except Exception as exc:
        logger.warning("Attestation register failed: %s", exc)
        return False
    finally:
        await r.aclose()


async def advance(pipeline_id: str, step_name: str) -> bool:
    """Advance the attestation record to the next step and refresh TTL."""
    r = await _get_redis()
    if r is None:
        return False
    try:
        key = _redis_key(pipeline_id)
        raw = await r.get(key)
        if not raw:
            return False
        record = json.loads(raw)
        record["current_step"] = step_name
        await r.set(key, json.dumps(record), ex=PIPELINE_TTL_SECONDS)
        return True
    except Exception as exc:
        logger.debug("Attestation advance failed (non-fatal): %s", exc)
        return False
    finally:
        await r.aclose()


async def expire(pipeline_id: str) -> None:
    """Mark the pipeline as COMPLETED and let the Redis TTL evict it."""
    r = await _get_redis()
    if r is None:
        return
    try:
        key = _redis_key(pipeline_id)
        raw = await r.get(key)
        if raw:
            record = json.loads(raw)
            record["status"] = "COMPLETED"
            # Keep the record for a short window so late-arriving OPA queries
            # can still see it and return a clean denial rather than a Redis miss.
            await r.set(key, json.dumps(record), ex=30)
    except Exception as exc:
        logger.debug("Attestation expire failed (non-fatal): %s", exc)
    finally:
        await r.aclose()


async def get(pipeline_id: str) -> dict[str, Any] | None:
    """Fetch the attestation record (used by OPA data provider and debug endpoints)."""
    r = await _get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(_redis_key(pipeline_id))
        return json.loads(raw) if raw else None
    except Exception:
        return None
    finally:
        await r.aclose()
