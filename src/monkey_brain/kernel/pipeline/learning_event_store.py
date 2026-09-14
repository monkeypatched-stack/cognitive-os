"""Persistence for individual learning events — the before/after record of
one real, Comparator-verified TransitionModel update
(kernel/pipeline/comparison/integration.py::_learn_transitions).

TransitionModel/prediction/persistence.py already persists the current
BLENDED snapshot per actor, but keeps no history: each learn_from_execution
call replaces the prior WorldTransition for a (goal_key, action_key) with a
new one, so "what changed as a result of this specific execution" is lost
the moment the next tick runs. This module is the minimum needed to answer
that question for a real, test or live, execution -- not a general audit
log, and not a replacement for TransitionModel's own snapshot persistence.

Mirrors kernel/pipeline/execution_checkpoint_store.py's exact shape (own
lazy Redis singleton, same REDIS_URL/REDIS_HOST/REDIS_PORT convention,
never raises). Read-modify-write, not atomic -- an acceptable, deliberate
trade for best-effort observability data, same trade already accepted by
every sibling store here; a rare lost race under concurrent ticks for the
same actor is not a correctness issue for inspection/debugging.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.pipeline.learning_event_store")

_EXECUTION_KEY_PREFIX = "monkeybrain:learning_events:execution:"
_ACTOR_KEY_PREFIX = "monkeybrain:learning_events:actor:"
_ACTOR_HISTORY_LIMIT = 200
_client: Any = None
_connect_attempted = False


def _redis_url() -> str:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    return f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"


def _get_client() -> Any:
    """Lazy, module-level singleton — same shape as
    execution_checkpoint_store.py's own lazy backend."""
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
        logger.warning("LearningEvent persistence: Redis unavailable (non-fatal): %s", exc)
        _client = None
    return _client


@dataclass
class LearningEvent:
    execution_id: str
    actor_id: str
    goal_key: str
    action_key: str
    success: bool
    previous: dict[str, Any] | None = None
    """WorldTransition.to_dict() of the last known transition for this
    (goal_key, action_key) before this observation, or None on cold start
    (no prior transition existed for this key)."""
    updated: dict[str, Any] = field(default_factory=dict)
    """WorldTransition.to_dict() after this observation was learned."""
    recorded_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "actor_id": self.actor_id,
            "goal_key": self.goal_key,
            "action_key": self.action_key,
            "success": self.success,
            "previous": self.previous,
            "updated": self.updated,
            "recorded_at": self.recorded_at,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "LearningEvent":
        return LearningEvent(
            execution_id=d.get("execution_id", ""),
            actor_id=d.get("actor_id", ""),
            goal_key=d.get("goal_key", ""),
            action_key=d.get("action_key", ""),
            success=bool(d.get("success", False)),
            previous=d.get("previous"),
            updated=dict(d.get("updated") or {}),
            recorded_at=float(d.get("recorded_at", time.time())),
        )


def record_learning_event(event: LearningEvent) -> bool:
    """Never raises — a dropped write here loses observability into one
    learning update, not the update itself (TransitionModel's own
    persistence, in prediction/persistence.py, is unaffected either way).

    Appends to both the execution-scoped list (that tick's own events) and
    the actor-scoped history (capped at _ACTOR_HISTORY_LIMIT, newest
    first) — a real Redis GET+SET round-trip each, matching every other
    store in this codebase's plain-JSON convention rather than introducing
    native Redis list commands.
    """
    client = _get_client()
    if client is None or not event.execution_id or not event.actor_id:
        return False
    try:
        exec_key = f"{_EXECUTION_KEY_PREFIX}{event.execution_id}"
        raw = client.get(exec_key)
        events = json.loads(raw) if raw else []
        events.append(event.to_dict())
        client.set(exec_key, json.dumps(events))

        actor_key = f"{_ACTOR_KEY_PREFIX}{event.actor_id}"
        raw = client.get(actor_key)
        history = json.loads(raw) if raw else []
        history.insert(0, event.to_dict())
        del history[_ACTOR_HISTORY_LIMIT:]
        client.set(actor_key, json.dumps(history))
        return True
    except Exception as exc:
        logger.debug(
            "record_learning_event(%s, %s) failed: %s",
            event.execution_id,
            event.actor_id,
            exc,
        )
        return False


def load_learning_events_for_execution(execution_id: str) -> list[LearningEvent]:
    client = _get_client()
    if client is None or not execution_id:
        return []
    try:
        raw = client.get(f"{_EXECUTION_KEY_PREFIX}{execution_id}")
        if not raw:
            return []
        return [LearningEvent.from_dict(d) for d in json.loads(raw)]
    except Exception as exc:
        logger.debug("load_learning_events_for_execution(%s) failed: %s", execution_id, exc)
        return []


def load_learning_events_for_actor(actor_id: str, limit: int = 50) -> list[LearningEvent]:
    """Newest first (matches record_learning_event's insert(0, ...))."""
    client = _get_client()
    if client is None or not actor_id:
        return []
    try:
        raw = client.get(f"{_ACTOR_KEY_PREFIX}{actor_id}")
        if not raw:
            return []
        return [LearningEvent.from_dict(d) for d in json.loads(raw)[:limit]]
    except Exception as exc:
        logger.debug("load_learning_events_for_actor(%s) failed: %s", actor_id, exc)
        return []
