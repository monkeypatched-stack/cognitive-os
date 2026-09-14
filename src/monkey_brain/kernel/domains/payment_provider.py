"""PaymentProvider — the seam between checkout and however money actually
moves.

Extracted as its own abstraction (rather than extending finance.py's
existing wallet mechanics) because real payment rails, and UPI Reserve
Pay in particular, don't work the way kernel/domains/finance.py's wallet
does: finance.py::_cas_adjust_balance debits/credits a KG balance in one
atomic, synchronous, always-consistent step. Real UPI is two-phase and
asynchronous — reserve (hold funds, payer approves in their own UPI app)
then capture (funds actually settle) or release (hold cancelled) — so the
interface here is two-phase from the start, even though FakePaymentProvider
below settles both phases instantly.

idempotency_key scopes retries: calling reserve()/capture() again with the
SAME key must return the SAME result, never reserve or capture twice. This
is the payment-specific extension of the resume_order_id discipline
kernel/domains/grocery.py already uses for order creation (~line 6591,
"resume the SAME execution later" without repeating its side effect).

A real PSP-backed implementation (e.g. UPI Reserve Pay) is a separate,
later module implementing this same interface — nothing above this line
should ever need to change to add one.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ReservationStatus(str, Enum):
    PENDING_AUTHORIZATION = "pending_authorization"
    """reserve() was called and a real request to hold funds is in
    flight, but nothing is actually held yet — a real UPI PSP (e.g.
    Razorpay) requires the payer to approve in their own app before an
    authorization exists; a real order/intent can be CREATED without
    that ever happening. FakePaymentProvider never needs this state
    (it has no real payer to wait on) and moves straight to RESERVED."""
    RESERVED = "reserved"
    CAPTURED = "captured"
    RELEASED = "released"
    FAILED = "failed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"
    """Request was submitted (or may have been) but CognitiveOS cannot
    confirm the PSP outcome — typically a timeout after the HTTP call
    left this process. Distinct from FAILED. Must not be retried blindly."""


@dataclass(frozen=True)
class ReservationResult:
    """Result of reserve()/release()/get_reservation() — success=False
    with an empty reservation_id means the operation never took a hold at
    all (declined, invalid amount), distinct from a hold that WAS taken
    and later moved to FAILED/EXPIRED."""

    success: bool
    reservation_id: str
    status: ReservationStatus
    amount: float
    reason: str = ""


@dataclass(frozen=True)
class CaptureResult:
    success: bool
    reservation_id: str
    status: ReservationStatus
    captured_amount: float
    reason: str = ""


class PaymentProvider:
    """Base interface every real PSP integration, and every fake used for
    tests/demo, implements. Async throughout — even FakePaymentProvider's
    instant in-memory implementation stays async, so code written against
    this interface already has the right shape for a real PSP call that
    genuinely waits on a network round trip or a webhook."""

    name: str = "payment_provider"

    async def reserve(
        self, amount: float, payer_ref: str, idempotency_key: str
    ) -> ReservationResult:
        raise NotImplementedError

    async def capture(self, reservation_id: str, idempotency_key: str) -> CaptureResult:
        raise NotImplementedError

    async def release(self, reservation_id: str) -> ReservationResult:
        raise NotImplementedError

    async def get_reservation(self, reservation_id: str) -> ReservationResult | None:
        raise NotImplementedError


@dataclass
class _Reservation:
    reservation_id: str
    payer_ref: str
    amount: float
    status: ReservationStatus
    created_at: float
    reserve_idempotency_key: str
    capture_idempotency_key: str | None = None


class FakePaymentProvider(PaymentProvider):
    """In-memory, deterministic PaymentProvider for tests and the
    reference Grocery domain — no network, no PSP account required.
    Reserve/capture/release settle instantly, but the state machine
    (RESERVED -> CAPTURED | RELEASED | EXPIRED) is the real one a UPI
    Reserve Pay integration will also need, so code built against this
    interface doesn't change shape when a real PSP-backed
    PaymentProvider is swapped in behind it.

    declined_payer_refs lets tests exercise the failure path (a real UPI
    PSP declining a reservation) the same way
    finance.py::process_payment_with_fallback already simulates a
    processor outage — "a processor marked down fails every attempt,
    simulating a real outage, not a coin flip" — applied here to a
    specific payer instead of a specific processor.

    Skips PENDING_AUTHORIZATION entirely and goes straight to RESERVED —
    there's no real payer here to wait on approval from, so collapsing
    the two states is honest, not a shortcut. See
    razorpay_upi_provider.py::RazorpayUPIProvider for the real PSP
    implementation that genuinely needs the intermediate state.
    """

    name = "fake_upi_reserve_pay"

    def __init__(self, expiry_seconds: float = 900.0) -> None:
        self._reservations: dict[str, _Reservation] = {}
        self._idempotency_index: dict[str, str] = {}
        self._expiry_seconds = expiry_seconds
        self.declined_payer_refs: set[str] = set()

    async def reserve(
        self, amount: float, payer_ref: str, idempotency_key: str
    ) -> ReservationResult:
        existing_id = self._idempotency_index.get(idempotency_key)
        if existing_id is not None:
            existing = self._reservations[existing_id]
            return ReservationResult(
                True,
                existing.reservation_id,
                existing.status,
                existing.amount,
                "idempotent replay",
            )

        if amount <= 0:
            return ReservationResult(
                False, "", ReservationStatus.FAILED, amount, "amount must be positive"
            )

        if payer_ref in self.declined_payer_refs:
            return ReservationResult(
                False,
                "",
                ReservationStatus.FAILED,
                amount,
                f"reservation declined for payer {payer_ref!r}",
            )

        reservation_id = f"UPI-RSV-{uuid.uuid4().hex[:12]}"
        reservation = _Reservation(
            reservation_id=reservation_id,
            payer_ref=payer_ref,
            amount=round(amount, 2),
            status=ReservationStatus.RESERVED,
            created_at=time.time(),
            reserve_idempotency_key=idempotency_key,
        )
        self._reservations[reservation_id] = reservation
        self._idempotency_index[idempotency_key] = reservation_id
        return ReservationResult(
            True, reservation_id, ReservationStatus.RESERVED, reservation.amount
        )

    async def capture(self, reservation_id: str, idempotency_key: str) -> CaptureResult:
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            return CaptureResult(
                False,
                reservation_id,
                ReservationStatus.FAILED,
                0.0,
                "no such reservation",
            )

        if (
            reservation.status == ReservationStatus.CAPTURED
            and reservation.capture_idempotency_key == idempotency_key
        ):
            return CaptureResult(
                True,
                reservation_id,
                ReservationStatus.CAPTURED,
                reservation.amount,
                "idempotent replay",
            )

        self._expire_if_due(reservation)
        if reservation.status != ReservationStatus.RESERVED:
            return CaptureResult(
                False,
                reservation_id,
                reservation.status,
                0.0,
                f"cannot capture a reservation in status {reservation.status.value!r}",
            )

        reservation.status = ReservationStatus.CAPTURED
        reservation.capture_idempotency_key = idempotency_key
        return CaptureResult(
            True, reservation_id, ReservationStatus.CAPTURED, reservation.amount
        )

    async def release(self, reservation_id: str) -> ReservationResult:
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            return ReservationResult(
                False,
                reservation_id,
                ReservationStatus.FAILED,
                0.0,
                "no such reservation",
            )

        if reservation.status == ReservationStatus.CAPTURED:
            return ReservationResult(
                False,
                reservation_id,
                reservation.status,
                reservation.amount,
                "cannot release an already-captured reservation",
            )

        reservation.status = ReservationStatus.RELEASED
        return ReservationResult(
            True, reservation_id, ReservationStatus.RELEASED, reservation.amount
        )

    async def get_reservation(self, reservation_id: str) -> ReservationResult | None:
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            return None
        self._expire_if_due(reservation)
        return ReservationResult(
            True, reservation.reservation_id, reservation.status, reservation.amount
        )

    def _expire_if_due(self, reservation: _Reservation) -> None:
        if (
            reservation.status == ReservationStatus.RESERVED
            and (time.time() - reservation.created_at) > self._expiry_seconds
        ):
            reservation.status = ReservationStatus.EXPIRED


def sync_call(coro: Any) -> Any:
    """Bridge for a SYNC caller (kernel/domains/grocery.py::PaymentCapability.
    handle is deliberately kept synchronous — real production code
    (api/routes/orders.py's /orders/{id}/pay) and several real test files
    call .handle() directly and synchronously today; forcing it async
    would break every one of them for a change scoped to one payment
    method, same "capability.handle can be sync OR async" boundary
    action_executor.py::_execute_action already branches on via
    inspect.iscoroutinefunction — this capability deliberately stays on
    the sync side of it) to call a PaymentProvider's real async reserve()/
    capture()/release().

    Safe whether or not an event loop is already running in this thread.
    ActionExecutor.execute() calls capability.handle() synchronously from
    within its OWN running event loop — plain asyncio.run() would raise
    "cannot be called from a running event loop" in that case, so this
    runs the coroutine on a fresh thread with its own loop instead and
    blocks for the result, only when a loop is already running; the
    common case (no loop running, e.g. a direct synchronous test/route
    call) just uses asyncio.run() directly, no extra thread needed."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
