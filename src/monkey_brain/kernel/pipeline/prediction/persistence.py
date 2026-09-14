"""Persistence for the learned TransitionModel — the real per-action
success/failure knowledge an actor accumulates across ticks
(kernel/pipeline/prediction/transitions.py::TransitionModel.learn_from_execution).

Previously this lived only on the in-memory ComparisonIntegratedPolicy
built once per actor at registration (build_runtime_engine) — every
restart, every actor's learning genuinely started over from zero. This
gives it the same real, lazy Redis persistence TimelineStore already
uses (same connection convention: REDIS_URL, else REDIS_HOST/REDIS_PORT
— see kernel/timeline/store.py::_make_backend), keyed by actor_id.

The "together with actor name" record (monkeybrain:transition_model:
{actor_id}:meta) is written separately, at actor registration time —
the one place both the actor_id and its real display name are already
in scope (kernel/pipeline/comparison/integration.py's own learn step,
where the model itself gets updated, only ever has actor_id; see
Cognitive Loop Verification's own trace of CognitiveActor/pipeline
Actor — neither carries a name field).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("agentos.pipeline.prediction.persistence")

_MODEL_KEY_PREFIX = "monkeybrain:transition_model:"
_client: Any = None
_connect_attempted = False


def _redis_url() -> str:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    return f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"


def _get_client() -> Any:
    """Lazy, module-level singleton — same shape as TimelineStore's own
    lazy backend, minus the multi-backend abstraction (this always wants
    Redis if available, in-memory-only otherwise; there's no meaningful
    "process-local" backend for cross-restart learning)."""
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
        logger.warning("TransitionModel persistence: Redis unavailable (non-fatal): %s", exc)
        _client = None
    return _client


def save_transition_model(actor_id: str, model: Any) -> bool:
    """Real persistence, called right after a real learning update
    (kernel/pipeline/comparison/integration.py::_run_comparison, the
    same place policy._transition_model = learned already happens).
    Never raises — a dropped write here is lost learning, not lost
    cognitive history (TimelineStore's own execution/decision records
    are unaffected either way)."""
    client = _get_client()
    if client is None or not actor_id:
        return False
    try:
        client.set(f"{_MODEL_KEY_PREFIX}{actor_id}", json.dumps(model.to_dict()))
        return True
    except Exception as exc:
        logger.debug("save_transition_model(%s) failed: %s", actor_id, exc)
        return False


def load_transition_model(actor_id: str) -> Any | None:
    """Called at actor registration (api/routes/actors.py, kernel/society/
    runtime.py) — if a real prior model exists, it's passed into
    build_runtime_engine/build_comparison_integrated_runtime instead of
    always starting from zero. Returns None (not a fresh TransitionModel)
    when nothing is persisted, so the caller can tell "no prior learning"
    apart from "a real empty model" if it ever needs to."""
    from src.monkey_brain.kernel.pipeline.prediction.transitions import TransitionModel

    client = _get_client()
    if client is None or not actor_id:
        return None
    try:
        raw = client.get(f"{_MODEL_KEY_PREFIX}{actor_id}")
        if not raw:
            return None
        return TransitionModel.from_dict(json.loads(raw))
    except Exception as exc:
        logger.debug("load_transition_model(%s) failed: %s", actor_id, exc)
        return None


def save_actor_meta(actor_id: str, name: str) -> bool:
    """The "together with actor name" half of this feature — a small,
    separate companion record so the persisted transition model is
    genuinely attributable to a real actor when inspected directly
    (e.g. `redis-cli GET monkeybrain:transition_model:<id>:meta`), not
    just an opaque id. Written at registration time regardless of
    whether a model exists yet for this actor."""
    client = _get_client()
    if client is None or not actor_id:
        return False
    try:
        client.set(
            f"{_MODEL_KEY_PREFIX}{actor_id}:meta",
            json.dumps({"actor_id": actor_id, "name": name, "updated_at": time.time()}),
        )
        return True
    except Exception as exc:
        logger.debug("save_actor_meta(%s) failed: %s", actor_id, exc)
        return False
