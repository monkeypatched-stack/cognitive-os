"""Trusted Time client — talks to the standalone Trusted Time service
(domains/manufacturing/knowledge/services/trusted_time/) for an attested
timestamp instead of trusting the local process clock outright.

Additive: every existing time.time() call in this codebase is untouched;
this is opt-in for the specific structures identified as needing it
(currently: kernel/process/checkpoint.py's Checkpoint.created_at — the one
signed structure that didn't even include its own timestamp in what it
signs; see that module's docstring history).

Fails open, loudly: a checkpoint's usefulness (suspend/resume across
process restarts) must not become hostage to a second service's uptime.
When the Trusted Time service is unreachable, get_trusted_timestamp() falls
back to the local clock and marks the result attested=False so a verifier
can tell the difference — this function never raises.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger("agentos.trusted_time")

TRUSTED_TIME_URL = os.environ.get("TRUSTED_TIME_URL", "http://trusted-time:8040")
TRUSTED_TIME_TIMEOUT_SECONDS = 1.5
_CACHE_TTL_SECONDS = 1.0


@dataclass(frozen=True)
class TrustedTimestamp:
    timestamp: float
    signature: str
    key_id: str
    attested: bool  # False when this is a local time.time() fallback, not a real attestation


_cache: TrustedTimestamp | None = None
_cache_fetched_at: float = 0.0


def _signing_key() -> bytes:
    """Same env-var-driven shared secret the service itself signs with —
    see domains/.../services/trusted_time/main.py::_signing_key."""
    key = os.environ.get("TRUSTED_TIME_SIGNING_KEY", "") or os.environ.get("AGENTOS_MASTER_KEY", "")
    if not key:
        key = "insecure-dev-only-trusted-time-key"
    return key.encode("utf-8")


async def get_trusted_timestamp() -> TrustedTimestamp:
    """Never raises. Returns a real attested timestamp when the Trusted
    Time service is reachable, otherwise a local-clock fallback
    (attested=False, logged) — this service is additive infrastructure,
    not a new hard availability dependency for anything that calls this."""
    global _cache, _cache_fetched_at

    now = time.time()
    if _cache is not None and (now - _cache_fetched_at) < _CACHE_TTL_SECONDS:
        return _cache

    try:
        async with httpx.AsyncClient(timeout=TRUSTED_TIME_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{TRUSTED_TIME_URL}/timestamp")
            resp.raise_for_status()
            data = resp.json()
        result = TrustedTimestamp(
            timestamp=float(data["timestamp"]),
            signature=str(data["signature"]),
            key_id=str(data["key_id"]),
            attested=True,
        )
    except Exception as exc:
        logger.warning("Trusted Time service unreachable, falling back to local clock: %s", exc)
        result = TrustedTimestamp(timestamp=time.time(), signature="", key_id="", attested=False)

    _cache = result
    _cache_fetched_at = now
    return result


def verify_trusted_timestamp(ts: TrustedTimestamp) -> bool:
    """Verify a timestamp's signature against the shared HMAC key. An
    unattested (local-fallback) timestamp always verifies False — it was
    never actually signed by the service."""
    if not ts.attested or not ts.signature:
        return False
    expected = hmac.new(_signing_key(), f"{ts.timestamp:.6f}|{ts.key_id}".encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, ts.signature)
