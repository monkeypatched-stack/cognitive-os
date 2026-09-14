"""Persistence for a paused, awaiting-payment-confirmation execution step —
the payment counterpart of negotiation_store.py::PendingNegotiation and
approval_store.py::PendingApproval.

Same lazy Redis singleton / never-raises shape as both siblings. A
capability opts a step into this state machine by returning
{"requires_payment_confirmation": True, "reservation_id": ..., ...} in its
own result dict, after calling PaymentProvider.reserve()
(kernel/domains/payment_provider.py) — ActionExecutor.execute() is the
intended caller, the same place it already branches on
requires_negotiation/requires_approval.

Real UPI Reserve Pay (and any other two-phase PaymentProvider) confirms
OUTSIDE this process — the payer approves in their own UPI app, and the
PSP calls back later. This store is what lets that later callback (a
webhook) find its way back to the SAME paused execution: keyed by
execution_id like its siblings, but also indexed by reservation_id, since
a webhook arrives with the PSP's reservation_id and nothing else.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.kernel.domains.payment_provider import ReservationStatus

logger = logging.getLogger("agentos.pipeline.payment_store")

_PAYMENT_KEY_PREFIX = "monkeybrain:pending_payment:"
_RESERVATION_INDEX_PREFIX = "monkeybrain:pending_payment_by_reservation:"
_client: Any = None
_connect_attempted = False


def _redis_url() -> str:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    return f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"


def _get_client() -> Any:
    """Lazy, module-level singleton — same shape as negotiation_store.py's
    and approval_store.py's.

    Live Deployment finding: this used to set `_connect_attempted = True`
    unconditionally before the connection attempt, so a single transient
    failure (confirmed live: the first-ever call, arriving as part of a
    synchronous multi-actor planetary cycle burst right after boot, racing
    Docker's bridge network/DNS settling for the sibling `redis` container)
    permanently disabled PendingPayment persistence for the rest of the
    process's life — every later call short-circuited straight to `None`
    even once Redis was trivially reachable again (confirmed: a fresh
    process resolves and connects fine). Only cache success now; a failed
    attempt retries on the next call instead of poisoning the singleton
    forever."""
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
        logger.warning("PendingPayment persistence: Redis unavailable (non-fatal): %s", exc)
        _client = None
    return _client


@dataclass
class PendingPayment:
    execution_id: str
    actor_id: str = ""
    step_index: int = -1
    capability: str = ""
    action_id: str = ""
    provider_name: str = ""
    """Which PaymentProvider (payment_provider.py::PaymentProvider.name)
    holds this reservation — resolving a payment needs to call back into
    the SAME provider that reserved it, never a guessed/default one."""
    reservation_id: str = ""
    payer_ref: str = ""
    amount: float = 0.0
    reserve_idempotency_key: str = ""
    capture_idempotency_key: str = ""
    status: str = ReservationStatus.RESERVED.value
    """Mirrors the PaymentProvider's own ReservationStatus (reused, not
    reinvented, to avoid two disagreeing sources of truth for the same
    reservation) — RESERVED while genuinely pending, CAPTURED/RELEASED/
    FAILED/EXPIRED once resolved."""
    reason: str = ""
    correlation_id: str = ""
    causation_id: str = ""
    created_at: float = field(default_factory=time.time)
    decided: bool | None = None
    """None = still pending (RESERVED). True = captured (payment
    succeeded). False = released, failed, or expired (payment did not
    go through) — same three-state contract PendingNegotiation/
    PendingApproval already use, so ActionExecutor's resume logic can
    treat all three pause types uniformly."""
    decided_at: float | None = None
    original_question: str = ""
    """The real prompt text that produced this tick — same
    has_goal-on-resume requirement documented in
    approval_store.py::PendingApproval.original_question; reused verbatim
    here so a resumed payment confirmation parses to the same goal."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "actor_id": self.actor_id,
            "step_index": self.step_index,
            "capability": self.capability,
            "action_id": self.action_id,
            "provider_name": self.provider_name,
            "reservation_id": self.reservation_id,
            "payer_ref": self.payer_ref,
            "amount": self.amount,
            "reserve_idempotency_key": self.reserve_idempotency_key,
            "capture_idempotency_key": self.capture_idempotency_key,
            "status": self.status,
            "reason": self.reason,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "created_at": self.created_at,
            "decided": self.decided,
            "decided_at": self.decided_at,
            "original_question": self.original_question,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PendingPayment":
        return PendingPayment(
            execution_id=d.get("execution_id", ""),
            actor_id=d.get("actor_id", ""),
            step_index=int(d.get("step_index", -1)),
            capability=d.get("capability", ""),
            action_id=d.get("action_id", ""),
            provider_name=d.get("provider_name", ""),
            reservation_id=d.get("reservation_id", ""),
            payer_ref=d.get("payer_ref", ""),
            amount=float(d.get("amount", 0.0) or 0.0),
            reserve_idempotency_key=d.get("reserve_idempotency_key", ""),
            capture_idempotency_key=d.get("capture_idempotency_key", ""),
            status=d.get("status", ReservationStatus.RESERVED.value),
            reason=d.get("reason", ""),
            correlation_id=d.get("correlation_id", ""),
            causation_id=d.get("causation_id", ""),
            created_at=float(d.get("created_at", time.time())),
            decided=d.get("decided"),
            decided_at=d.get("decided_at"),
            original_question=d.get("original_question", ""),
        )


def save_pending_payment(payment: PendingPayment) -> bool:
    """Never raises — a dropped write here means a paused payment can't be
    inspected/resolved through the real API, which is a real, visible
    failure (the webhook will find nothing to resume) rather than a
    silent one, same degrade-gracefully shape as every sibling store.

    Also maintains the reservation_id -> execution_id index, since a
    real PSP webhook (kernel/domains/payment_provider.py's eventual
    real, PSP-backed implementation) only ever knows the reservation_id
    it was asked to confirm, not the execution paused on it.
    """
    client = _get_client()
    if client is None or not payment.execution_id:
        return False
    try:
        client.set(
            f"{_PAYMENT_KEY_PREFIX}{payment.execution_id}",
            json.dumps(payment.to_dict()),
        )
        if payment.reservation_id:
            client.set(
                f"{_RESERVATION_INDEX_PREFIX}{payment.reservation_id}",
                payment.execution_id,
            )
        return True
    except Exception as exc:
        logger.warning(
            "save_pending_payment(%s) failed: %s",
            payment.execution_id,
            exc,
            exc_info=True,
        )
        return False


def load_pending_payment(execution_id: str) -> PendingPayment | None:
    client = _get_client()
    if client is None or not execution_id:
        return None
    try:
        raw = client.get(f"{_PAYMENT_KEY_PREFIX}{execution_id}")
        if not raw:
            return None
        return PendingPayment.from_dict(json.loads(raw))
    except Exception as exc:
        logger.debug("load_pending_payment(%s) failed: %s", execution_id, exc)
        return None


def load_pending_payment_by_reservation(reservation_id: str) -> PendingPayment | None:
    """The lookup a real PSP webhook actually performs — reservation_id in,
    the paused execution's PendingPayment out. Returns None (never raises)
    if the index has nothing for this reservation_id, whether because it
    was never paused here, or was already resolved and cleared."""
    client = _get_client()
    if client is None or not reservation_id:
        return None
    try:
        execution_id = client.get(f"{_RESERVATION_INDEX_PREFIX}{reservation_id}")
        if not execution_id:
            return None
        return load_pending_payment(execution_id)
    except Exception as exc:
        logger.debug("load_pending_payment_by_reservation(%s) failed: %s", reservation_id, exc)
        return None


def resolve_pending_payment(execution_id: str, captured: bool, status: str, reason: str = "") -> PendingPayment | None:
    """Records a real capture/release outcome against an existing pending
    payment. Returns None (no write) if nothing is pending for this
    execution_id — a caller resolving something that was never asked for
    is an honest no-op, not a fabricated success. `status` should be one
    of ReservationStatus's values (the PaymentProvider's own post-capture/
    release/expiry status), not re-derived here — this store never
    invents payment state, only records what the provider reported."""
    payment = load_pending_payment(execution_id)
    if payment is None:
        return None
    payment.decided = captured
    payment.decided_at = time.time()
    payment.status = status
    if reason:
        payment.reason = reason
    save_pending_payment(payment)
    return payment


def clear_pending_payment(execution_id: str) -> None:
    client = _get_client()
    if client is None or not execution_id:
        return
    try:
        payment = load_pending_payment(execution_id)
        client.delete(f"{_PAYMENT_KEY_PREFIX}{execution_id}")
        if payment is not None and payment.reservation_id:
            client.delete(f"{_RESERVATION_INDEX_PREFIX}{payment.reservation_id}")
    except Exception as exc:
        logger.debug("clear_pending_payment(%s) failed: %s", execution_id, exc)
