"""Per-action capability dispatch deduplication in Redis.

Claims (execution_id, action_id) before invoking a capability and caches the
outcome so retries cannot double-apply side effects between handle() return
and execution_checkpoint_store persistence.

Enabled when COGNITIVEOS_PRODUCTION_MODE or CAPABILITY_DISPATCH_DEDUP is set.
Never raises; if Redis is unavailable, checkpoint-based resume still applies.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Literal

logger = logging.getLogger("agentos.pipeline.capability_dispatch_store")

_DISPATCH_KEY_PREFIX = "monkeybrain:cap_dispatch:"
_IN_PROGRESS = "in_progress"
_COMPLETED = "completed"
_DEFAULT_TTL_SECONDS = int(os.getenv("CAPABILITY_DISPATCH_TTL_SECONDS", "600"))

_client: Any = None


def _redis_url() -> str:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    return f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"


def _get_client() -> Any:
    global _client
    if _client is not None:
        return _client
    try:
        import redis

        client = redis.from_url(
            _redis_url(),
            decode_responses=True,
            socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT_SEC", "5")),
            socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_SEC", "5")),
        )
        client.ping()
        _client = client
    except Exception as exc:
        logger.warning("CapabilityDispatch persistence: Redis unavailable (non-fatal): %s", exc)
        _client = None
    return _client


def _key(execution_id: str, action_id: str) -> str:
    return f"{_DISPATCH_KEY_PREFIX}{execution_id}:{action_id}"


def load_cached_outcome(execution_id: str, action_id: str) -> dict[str, Any] | None:
    client = _get_client()
    if client is None or not execution_id or not action_id:
        return None
    try:
        raw = client.get(_key(execution_id, action_id))
        if not raw:
            return None
        record = json.loads(raw)
        if record.get("state") != _COMPLETED:
            return None
        outcome = record.get("outcome")
        return dict(outcome) if isinstance(outcome, dict) else None
    except Exception as exc:
        logger.debug("load_cached_outcome(%s, %s) failed: %s", execution_id, action_id, exc)
        return None


ReserveStatus = Literal["fresh", "cached", "in_progress", "unavailable"]


def reserve_dispatch(execution_id: str, action_id: str) -> ReserveStatus:
    """Atomically claim a dispatch slot. Returns cached if already completed."""
    client = _get_client()
    if client is None or not execution_id or not action_id:
        return "unavailable"
    key = _key(execution_id, action_id)
    try:
        cached = load_cached_outcome(execution_id, action_id)
        if cached is not None:
            return "cached"
        payload = json.dumps({"state": _IN_PROGRESS, "reserved_at": time.time()})
        claimed = client.set(key, payload, nx=True, ex=_DEFAULT_TTL_SECONDS)
        if claimed:
            return "fresh"
        raw = client.get(key)
        if not raw:
            return "fresh"
        record = json.loads(raw)
        if record.get("state") == _COMPLETED:
            return "cached"
        return "in_progress"
    except Exception as exc:
        logger.warning("reserve_dispatch(%s, %s) failed: %s", execution_id, action_id, exc)
        return "unavailable"


def complete_dispatch(execution_id: str, action_id: str, outcome: dict[str, Any]) -> bool:
    client = _get_client()
    if client is None or not execution_id or not action_id:
        return False
    try:
        payload = json.dumps(
            {
                "state": _COMPLETED,
                "outcome": outcome,
                "completed_at": time.time(),
            }
        )
        client.set(_key(execution_id, action_id), payload, ex=_DEFAULT_TTL_SECONDS)
        return True
    except Exception as exc:
        logger.warning("complete_dispatch(%s, %s) failed: %s", execution_id, action_id, exc)
        return False


def release_dispatch(execution_id: str, action_id: str) -> bool:
    """Drop an in-progress claim so a failed attempt may be retried."""
    client = _get_client()
    if client is None or not execution_id or not action_id:
        return False
    try:
        key = _key(execution_id, action_id)
        raw = client.get(key)
        if not raw:
            return True
        record = json.loads(raw)
        if record.get("state") == _COMPLETED:
            return False
        client.delete(key)
        return True
    except Exception as exc:
        logger.debug("release_dispatch(%s, %s) failed: %s", execution_id, action_id, exc)
        return False
