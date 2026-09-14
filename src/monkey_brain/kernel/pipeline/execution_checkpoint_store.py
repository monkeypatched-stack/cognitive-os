"""Persistence for in-progress execution-graph state — the minimum needed
to resume a plan after a restart without repeating an already-completed
irreversible action (a real capability call: reserving stock, charging a
wallet).

Mirrors kernel/pipeline/planning/context_snapshot_store.py's exact shape
(own lazy Redis singleton, same REDIS_URL/REDIS_HOST/REDIS_PORT
convention, never raises) — deliberately a dedicated store rather than
reusing PersistedActorState.memory_kv, since checkpoint_actor_belief
(kernel/society/integration.py) unconditionally rebuilds memory_kv as {}
on every tick and would silently destroy anything stashed there.

Keyed by execution_id alone (like context_snapshot_store, not like
current_plan_store's (actor_id, goal_key)) -- a checkpoint belongs to one
specific tick attempt, not a standing goal; a caller that wants to resume
must already know the execution_id it's resuming (mirrors
OrderCreationCapability's resume_order_id -- the caller states intent
explicitly, this is never auto-discovered).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.pipeline.execution_checkpoint_store")

_CHECKPOINT_KEY_PREFIX = "monkeybrain:execution_checkpoint:"
_client: Any = None
_connect_attempted = False


def _redis_url() -> str:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    return f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"


def _get_client() -> Any:
    """Lazy, module-level singleton — same shape as
    context_snapshot_store.py's own lazy backend."""
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
        logger.warning("ExecutionCheckpoint persistence: Redis unavailable (non-fatal): %s", exc)
        _client = None
    return _client


@dataclass
class ExecutionCheckpoint:
    execution_id: str
    plan: dict[str, Any] = field(default_factory=dict)
    """Full serialized belief_state.Plan (kernel/pipeline/planning/
    current_plan_store.py::plan_to_dict) -- the resumed tick must re-run
    THIS exact plan, never a freshly re-planned one, or step indices
    stop meaning the same thing between attempts."""
    completed_steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    """str(step_index) -> serialized ActionOutcome (action_id/success/
    result/error/latency_ms) -- string keys because JSON has no integer
    keys; converted back to int at read sites."""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "plan": self.plan,
            "completed_steps": self.completed_steps,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ExecutionCheckpoint":
        return ExecutionCheckpoint(
            execution_id=d.get("execution_id", ""),
            plan=dict(d.get("plan", {}) or {}),
            completed_steps=dict(d.get("completed_steps", {}) or {}),
            updated_at=float(d.get("updated_at", time.time())),
        )


def save_execution_checkpoint(
    execution_id: str, plan: dict[str, Any], completed_steps: dict[int, dict[str, Any]]
) -> bool:
    """Never raises — a dropped write here just means a crash between now
    and the next successful write repeats one more step on resume than it
    strictly needed to; it never causes anything to be SKIPPED that
    shouldn't be (skipping only ever happens for a step this call already
    confirmed succeeded)."""
    client = _get_client()
    if client is None or not execution_id:
        return False
    try:
        checkpoint = ExecutionCheckpoint(
            execution_id=execution_id,
            plan=plan,
            completed_steps={str(k): v for k, v in completed_steps.items()},
        )
        client.set(f"{_CHECKPOINT_KEY_PREFIX}{execution_id}", json.dumps(checkpoint.to_dict()))
        return True
    except Exception as exc:
        logger.warning("save_execution_checkpoint(%s) failed: %s", execution_id, exc, exc_info=True)
        return False


def load_execution_checkpoint(execution_id: str) -> ExecutionCheckpoint | None:
    client = _get_client()
    if client is None or not execution_id:
        return None
    try:
        raw = client.get(f"{_CHECKPOINT_KEY_PREFIX}{execution_id}")
        if not raw:
            return None
        return ExecutionCheckpoint.from_dict(json.loads(raw))
    except Exception as exc:
        logger.debug("load_execution_checkpoint(%s) failed: %s", execution_id, exc)
        return None
