"""Persistence for a paused, awaiting-negotiation-decision execution step —
the negotiation counterpart of approval_store.py::PendingApproval.

Same lazy Redis singleton / never-raises shape as approval_store.py and
execution_checkpoint_store.py. A capability (via ActionExecutor's
transition-gate check, action_executor.py) opts a step into this state
machine when TransitionGate.evaluate() returns requires_negotiation=True —
ActionExecutor.execute() is the only caller that reads it.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.pipeline.negotiation_store")

_NEGOTIATION_KEY_PREFIX = "monkeybrain:pending_negotiation:"
_client: Any = None
_connect_attempted = False


def _redis_url() -> str:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    return f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"


def _get_client() -> Any:
    """Lazy, module-level singleton — same shape as approval_store.py's."""
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
        logger.warning("PendingNegotiation persistence: Redis unavailable (non-fatal): %s", exc)
        _client = None
    return _client


@dataclass
class PendingNegotiation:
    execution_id: str
    actor_id: str = ""
    step_index: int = -1
    capability: str = ""
    action_id: str = ""
    proposed_transition: dict[str, Any] = field(default_factory=dict)
    """The real ProposedTransition.to_dict() the gate evaluated — reused
    verbatim on resume, same "commit exactly what was proposed" contract
    as PendingApproval.proposed_action."""
    counterparties: list[str] = field(default_factory=list)
    reason: str = ""
    correlation_id: str = ""
    causation_id: str = ""
    created_at: float = field(default_factory=time.time)
    decided: bool | None = None
    """None = still pending. True = agreement reached, False = rejected."""
    decided_at: float | None = None
    original_question: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "actor_id": self.actor_id,
            "step_index": self.step_index,
            "capability": self.capability,
            "action_id": self.action_id,
            "proposed_transition": self.proposed_transition,
            "counterparties": self.counterparties,
            "reason": self.reason,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "created_at": self.created_at,
            "decided": self.decided,
            "decided_at": self.decided_at,
            "original_question": self.original_question,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PendingNegotiation":
        return PendingNegotiation(
            execution_id=d.get("execution_id", ""),
            actor_id=d.get("actor_id", ""),
            step_index=int(d.get("step_index", -1)),
            capability=d.get("capability", ""),
            action_id=d.get("action_id", ""),
            proposed_transition=dict(d.get("proposed_transition", {}) or {}),
            counterparties=list(d.get("counterparties", []) or []),
            reason=d.get("reason", ""),
            correlation_id=d.get("correlation_id", ""),
            causation_id=d.get("causation_id", ""),
            created_at=float(d.get("created_at", time.time())),
            decided=d.get("decided"),
            decided_at=d.get("decided_at"),
            original_question=d.get("original_question", ""),
        )


def save_pending_negotiation(negotiation: PendingNegotiation) -> bool:
    client = _get_client()
    if client is None or not negotiation.execution_id:
        return False
    try:
        client.set(
            f"{_NEGOTIATION_KEY_PREFIX}{negotiation.execution_id}",
            json.dumps(negotiation.to_dict()),
        )
        return True
    except Exception as exc:
        logger.warning(
            "save_pending_negotiation(%s) failed: %s",
            negotiation.execution_id,
            exc,
            exc_info=True,
        )
        return False


def load_pending_negotiation(execution_id: str) -> PendingNegotiation | None:
    client = _get_client()
    if client is None or not execution_id:
        return None
    try:
        raw = client.get(f"{_NEGOTIATION_KEY_PREFIX}{execution_id}")
        if not raw:
            return None
        return PendingNegotiation.from_dict(json.loads(raw))
    except Exception as exc:
        logger.debug("load_pending_negotiation(%s) failed: %s", execution_id, exc)
        return None


def resolve_pending_negotiation(execution_id: str, accepted: bool) -> PendingNegotiation | None:
    """Records a real negotiation outcome (agreement reached or rejected)
    against an existing pending negotiation. Returns None (no write) if
    nothing is pending for this execution_id."""
    negotiation = load_pending_negotiation(execution_id)
    if negotiation is None:
        return None
    negotiation.decided = accepted
    negotiation.decided_at = time.time()
    save_pending_negotiation(negotiation)
    return negotiation


def clear_pending_negotiation(execution_id: str) -> None:
    client = _get_client()
    if client is None or not execution_id:
        return
    try:
        client.delete(f"{_NEGOTIATION_KEY_PREFIX}{execution_id}")
    except Exception as exc:
        logger.debug("clear_pending_negotiation(%s) failed: %s", execution_id, exc)
