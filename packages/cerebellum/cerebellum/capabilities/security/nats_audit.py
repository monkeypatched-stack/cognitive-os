"""NATS audit publisher — cerebellum capability.

Owned here per INV-003. services/common/audit_events.py is a thin re-export.

Publishes to NATS subject indus.audit.queue. Falls back to structured
logger when NATS is unavailable — never blocks the request path.

Connection is cached per process to avoid per-call connect/close overhead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

_CONNECT_TIMEOUT_SEC = float(os.getenv("NATS_AUDIT_CONNECT_TIMEOUT_SEC", "2"))

logger = logging.getLogger(__name__)

_NATS_URL = os.getenv("NATS_URL", "")
_AUDIT_SUBJECT = "indus.audit.queue"

_nc: Any = None  # cached NATS connection


async def _get_nats():
    global _nc
    if _nc is not None:
        try:
            if _nc.is_connected:
                return _nc
        except Exception:
            _nc = None

    url = _NATS_URL or os.getenv("NATS_URL", "")
    if not url:
        return None

    try:
        import nats

        # Live Deployment Validation finding: this module's own docstring
        # ("never blocks the request path") was violated — a bare
        # `await nats.connect(url)` has no bound on its own, and every
        # require_permission-gated route's 401/403 path routes through
        # here via _audit_auth_failure(). Confirmed live: with NATS fully
        # reachable, this call still hung indefinitely under the running
        # server's event loop (a fresh, isolated process connected in
        # ~10ms against the same URL), so an unauthenticated request to
        # ANY protected endpoint never received its 401 — it hung until
        # the client timed out. Bounding the connect attempt restores the
        # documented "never blocks" contract regardless of the deeper
        # cause of that particular hang.
        _nc = await asyncio.wait_for(nats.connect(url), timeout=_CONNECT_TIMEOUT_SEC)
        return _nc
    except Exception as exc:
        logger.warning("NATS connection failed (audit will log only): %s", exc)
        return None


async def publish(event: dict[str, Any], subject: str = _AUDIT_SUBJECT) -> None:
    """Publish event to NATS; fall back to structured log on failure."""
    nc = await _get_nats()
    if nc:
        try:
            await nc.publish(subject, json.dumps(event).encode())
            return
        except Exception as exc:
            logger.warning("NATS publish failed, falling back to log: %s", exc)

    logger.info(
        "AUDIT action=%s outcome=%s subject=%s spiffe=%s mtls=%s",
        event.get("action"),
        event.get("outcome"),
        event.get("subject"),
        event.get("spiffe_id"),
        event.get("mtls_verified"),
        extra={"audit_event": event},
    )
