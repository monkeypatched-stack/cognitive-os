"""Persistence for security violations — every denied auth attempt
(_audit_auth_failure's actual chokepoint), not just the pattern-detected
subset.

Same shape as approval_store.py / execution_checkpoint_store.py (own lazy
Redis singleton, same REDIS_URL/REDIS_HOST/REDIS_PORT convention, never
raises). Previously this signal was fire-and-forget: an in-process dict
in dependencies.py that resets on restart and answers only "is this
subject bursting right now" — never a queryable record of what was
denied, when, or why. This is that queryable record.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

logger = logging.getLogger("agentos.pipeline.violation_store")

_VIOLATIONS_KEY = "monkeybrain:security_violations"
_MAX_STORED = 2000
"""Bounded with a capped Redis list (LTRIM after every push) so a
sustained attack can't grow this without limit — oldest entries drop
first, same trade-off _record_failure_and_check_pattern's own
_MAX_TRACKED_SUBJECTS cap makes."""

_client: Any = None
_connect_attempted = False


def _redis_url() -> str:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    return f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"


def _get_client() -> Any:
    global _client, _connect_attempted
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
        _connect_attempted = True
    except Exception as exc:
        logger.warning("Violation persistence: Redis unavailable (non-fatal): %s", exc)
        _client = None
    return _client


def record_violation(
    *,
    subject: str,
    permission: str,
    reason: str,
    outcome: str,
    pattern_detected: bool = False,
) -> None:
    """Append one denial record. Best-effort — a Redis outage must never
    turn into a 500 on top of the real 401/403 it's recording, matching
    _audit_auth_failure's own never-raises contract."""
    client = _get_client()
    if client is None:
        return
    record = {
        "id": str(uuid.uuid4()),
        "subject": subject,
        "permission": permission,
        "reason": reason,
        "outcome": outcome,
        "pattern_detected": pattern_detected,
        "recorded_at": time.time(),
    }
    try:
        pipe = client.pipeline()
        pipe.lpush(_VIOLATIONS_KEY, json.dumps(record))
        pipe.ltrim(_VIOLATIONS_KEY, 0, _MAX_STORED - 1)
        pipe.execute()
    except Exception as exc:
        logger.warning("record_violation: Redis write failed (non-fatal): %s", exc)


def list_violations(limit: int = 100, subject: str | None = None) -> list[dict]:
    """Most-recent-first. `subject` filters client-side (the list is
    already capped at _MAX_STORED, so this never scans an unbounded set)."""
    client = _get_client()
    if client is None:
        return []
    try:
        raw = client.lrange(_VIOLATIONS_KEY, 0, _MAX_STORED - 1)
    except Exception as exc:
        logger.warning("list_violations: Redis read failed (non-fatal): %s", exc)
        return []
    records = []
    for item in raw:
        try:
            record = json.loads(item)
        except Exception:
            continue
        if subject and record.get("subject") != subject:
            continue
        records.append(record)
        if len(records) >= limit:
            break
    return records
