"""Persistence for a paused, awaiting-human-decision execution step — the
generic execution state machine's "WAITING_FOR_HUMAN" state (Qualification
Gap Closure, Phase 3).

Mirrors kernel/pipeline/execution_checkpoint_store.py's exact shape (own
lazy Redis singleton, same REDIS_URL/REDIS_HOST/REDIS_PORT convention,
never raises) — the fourth real, proven extension of the SAME pattern this
codebase already established for "persist mid-execution state, resume the
SAME execution later via meta.resume_execution_id" (Phase 3's checkpoint/
restart infrastructure).

Deliberately generic: nothing here knows what a "substitution" or a
"grocery product" is. A capability opts a step into this state machine by
returning {"requires_approval": True, "proposed_action": {...}, "reason":
"..."} in its own result dict -- ActionExecutor.execute() (the same
per-action loop Phases 1-4's own hooks already use) is the only caller
that reads it.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.pipeline.approval_store")

_APPROVAL_KEY_PREFIX = "monkeybrain:pending_approval:"
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
        logger.warning("PendingApproval persistence: Redis unavailable (non-fatal): %s", exc)
        _client = None
    return _client


@dataclass
class PendingApproval:
    execution_id: str
    actor_id: str = ""
    step_index: int = -1
    capability: str = ""
    action_id: str = ""
    proposed_action: dict[str, Any] = field(default_factory=dict)
    """Whatever the capability itself proposed to do -- fully opaque to
    the executor and this store, e.g. {"original_id": ..., "replacement":
    {...}}. Reused verbatim on resume so an approval commits EXACTLY what
    was proposed, never a freshly-recomputed (and possibly different,
    if the world moved again) candidate."""
    reason: str = ""
    correlation_id: str = ""
    causation_id: str = ""
    created_at: float = field(default_factory=time.time)
    decided: bool | None = None
    """None = still pending. True/False once a real human decision has
    been recorded via resolve_pending_approval."""
    decided_at: float | None = None
    original_question: str = ""
    """The real prompt text that produced this tick (Qualification Gap
    Closure, Phase 9 fix) -- a real, live-only bug found by actually
    resuming a paused approval end-to-end through the HTTP API: the
    resume PromptRequest needs SOME question text, and belief_runtime.py
    ::_generate_plan's checkpoint-reuse path only engages when the
    question parses to a real goal (has_goal) -- a generic placeholder
    like "resume this" doesn't parse as one, so the resumed tick fell
    through to an empty, goal-less plan and was rejected outright
    ("plan_has_no_goal") before ever reaching the checkpoint it was
    supposed to resume. Reusing the SAME real question that produced the
    original goal is what makes has_goal true again on resume."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "actor_id": self.actor_id,
            "step_index": self.step_index,
            "capability": self.capability,
            "action_id": self.action_id,
            "proposed_action": self.proposed_action,
            "reason": self.reason,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "created_at": self.created_at,
            "decided": self.decided,
            "decided_at": self.decided_at,
            "original_question": self.original_question,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PendingApproval":
        return PendingApproval(
            execution_id=d.get("execution_id", ""),
            actor_id=d.get("actor_id", ""),
            step_index=int(d.get("step_index", -1)),
            capability=d.get("capability", ""),
            action_id=d.get("action_id", ""),
            proposed_action=dict(d.get("proposed_action", {}) or {}),
            reason=d.get("reason", ""),
            correlation_id=d.get("correlation_id", ""),
            causation_id=d.get("causation_id", ""),
            created_at=float(d.get("created_at", time.time())),
            decided=d.get("decided"),
            decided_at=d.get("decided_at"),
            original_question=d.get("original_question", ""),
        )


def save_pending_approval(approval: PendingApproval) -> bool:
    """Never raises — a dropped write here means an approval can't be
    inspected/resolved through the real API, which is a real, visible
    failure (the resumed tick will find nothing to act on) rather than a
    silent one, same degrade-gracefully shape as every sibling store."""
    client = _get_client()
    if client is None or not approval.execution_id:
        return False
    try:
        client.set(
            f"{_APPROVAL_KEY_PREFIX}{approval.execution_id}",
            json.dumps(approval.to_dict()),
        )
        return True
    except Exception as exc:
        logger.warning(
            "save_pending_approval(%s) failed: %s",
            approval.execution_id,
            exc,
            exc_info=True,
        )
        return False


def load_pending_approval(execution_id: str) -> PendingApproval | None:
    client = _get_client()
    if client is None or not execution_id:
        return None
    try:
        raw = client.get(f"{_APPROVAL_KEY_PREFIX}{execution_id}")
        if not raw:
            return None
        return PendingApproval.from_dict(json.loads(raw))
    except Exception as exc:
        logger.debug("load_pending_approval(%s) failed: %s", execution_id, exc)
        return None


def resolve_pending_approval(execution_id: str, approved: bool) -> PendingApproval | None:
    """Records a real human decision against an existing pending approval.
    Returns None (no write) if no pending approval exists for this
    execution_id — a caller resolving something that was never asked for
    is an honest no-op, not a fabricated success."""
    approval = load_pending_approval(execution_id)
    if approval is None:
        return None
    approval.decided = approved
    approval.decided_at = time.time()
    save_pending_approval(approval)
    return approval


def clear_pending_approval(execution_id: str) -> None:
    client = _get_client()
    if client is None or not execution_id:
        return
    try:
        client.delete(f"{_APPROVAL_KEY_PREFIX}{execution_id}")
    except Exception as exc:
        logger.debug("clear_pending_approval(%s) failed: %s", execution_id, exc)
