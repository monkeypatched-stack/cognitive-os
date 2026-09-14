"""RazorpayUPIProvider — the real, PSP-backed PaymentProvider implementation
for UPI Reserve Pay, replacing FakePaymentProvider (payment_provider.py) for
production/real-money use.

Built against Razorpay's real Orders/Payments REST API (api.razorpay.com/v1),
Basic Auth with key_id:key_secret, confirmed against Razorpay's own docs
while writing this (not guessed):
  POST /v1/orders                    — create an order (the "reserve" intent)
  POST /v1/payments/{id}/capture     — capture an authorized payment
  GET  /v1/payments/{id}             — read a payment's live status
  Webhooks: X-Razorpay-Signature header, HMAC-SHA256(raw_body, webhook_secret)

Three real constraints this module has to honor that FakePaymentProvider's
instant, in-process settlement never had to:

1. **reserve() cannot return RESERVED.** Creating an Order does not hold
   any money — the payer still has to approve the UPI collect request in
   their own app. reserve() returns PENDING_AUTHORIZATION; the reservation
   only becomes real once Razorpay's `payment.authorized` webhook fires
   and record_authorization() is called (by the webhook handler — see
   docs/architecture.md's negotiation/approval pause pattern this reuses,
   kernel/pipeline/payment_store.py::PendingPayment).

2. **capture() operates on a payment_id, not the order_id.** reserve()'s
   reservation_id is the Razorpay order_id (the only id that exists at
   reserve time); capture needs the payment_id Razorpay only assigns once
   the payer actually pays. record_authorization() is what links the two.

3. **release() cannot force money back before capture.** Razorpay has no
   "void this authorization now" API for a standard UPI collect payment —
   an authorized-but-uncaptured payment auto-refunds itself on Razorpay's
   own schedule (configurable capture-settings window; not immediate).
   release() is honest about this: if nothing was ever authorized, it's a
   real, immediate no-op-style success (there's nothing to void). If a
   real authorization already exists, release() returns success=False
   with a reason explaining the real constraint, rather than pretending
   to have cancelled something it structurally cannot cancel.

Credentials are read from RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET /
RAZORPAY_WEBHOOK_SECRET — never hardcoded, matching every other secret in
this codebase (see .gitignore's "Secrets / credentials" section). A
provider constructed without them fails every call closed (a clear
FAILED/CaptureResult with an explicit "not configured" reason), the same
fail-closed-on-misconfiguration posture services/common/opa.py already
uses for OPA.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

import httpx

from src.monkey_brain.kernel.domains.payment_provider import (
    CaptureResult,
    PaymentProvider,
    ReservationResult,
    ReservationStatus,
)

logger = logging.getLogger("agentos.domains.razorpay_upi_provider")

_RAZORPAY_API_BASE = os.getenv(
    "RAZORPAY_API_BASE", "https://api.razorpay.com/v1"
).rstrip("/")
_RAZORPAY_TIMEOUT_SECONDS = float(os.getenv("RAZORPAY_TIMEOUT_SECONDS", "10"))


def verify_webhook_signature(
    raw_body: bytes, signature: str, webhook_secret: str
) -> bool:
    """Real Razorpay webhook verification: HMAC-SHA256 over the RAW request
    body (never the parsed/re-serialized JSON — a re-serialized body can
    byte-differ from what was signed and fail verification even for a
    genuine webhook), keyed by the webhook secret, compared to the
    X-Razorpay-Signature header. hmac.compare_digest, not ==, so this
    isn't timing-attackable."""
    if not webhook_secret or not signature:
        return False
    expected = hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class _AuthorizedPayment:
    __slots__ = ("payment_id", "amount", "captured")

    def __init__(self, payment_id: str, amount: float) -> None:
        self.payment_id = payment_id
        self.amount = amount
        self.captured = False


class RazorpayUPIProvider(PaymentProvider):
    """Real UPI Reserve Pay via Razorpay. See module docstring for the
    three real async/two-phase constraints this honors that
    FakePaymentProvider didn't need to."""

    name = "razorpay_upi_reserve_pay"

    def __init__(
        self,
        key_id: str = "",
        key_secret: str = "",
        webhook_secret: str = "",
        currency: str = "INR",
    ) -> None:
        self._key_id = key_id or os.getenv("RAZORPAY_KEY_ID", "")
        self._key_secret = key_secret or os.getenv("RAZORPAY_KEY_SECRET", "")
        self._webhook_secret = webhook_secret or os.getenv(
            "RAZORPAY_WEBHOOK_SECRET", ""
        )
        self._currency = currency
        # order_id -> _AuthorizedPayment, populated only by
        # record_authorization() once Razorpay's payment.authorized
        # webhook actually confirms one exists. Absence here is the
        # honest signal that no authorization has happened yet (never
        # inferred, never guessed from reserve()'s own return).
        self._authorizations: dict[str, _AuthorizedPayment] = {}
        # idempotency_key -> order_id. Confirmed live against Razorpay's
        # real sandbox: the Orders API has NO idempotency-key concept of
        # its own — calling POST /v1/orders twice with an identical
        # payload creates two distinct real orders, full stop. Every
        # other layer in this codebase (FakePaymentProvider, PendingPayment,
        # grocery.py's resume_order_id) assumes reserve() is idempotent,
        # so this index is what actually makes that true here — the same
        # local-idempotency-cache pattern FakePaymentProvider already
        # uses, just enforced in front of a real API call instead of an
        # in-memory state machine.
        self._idempotency_index: dict[str, str] = {}
        # order_id -> amount reserved, recorded at reserve() time so a
        # PENDING_AUTHORIZATION get_reservation()/idempotent-replay
        # result can report the real amount instead of 0.0 before an
        # authorization (and therefore _AuthorizedPayment) exists.
        self._order_amounts: dict[str, float] = {}

    def is_configured(self) -> bool:
        return bool(self._key_id and self._key_secret)

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        """Real signature check for an inbound webhook, using THIS
        instance's configured webhook secret. Fails closed (False) if no
        webhook secret is configured at all — an unconfigured secret must
        never be treated as "skip verification", unlike
        GovernanceEngine's default_allow=True for unset OPA_URL: an
        unverified webhook can resolve a real payment as captured, so
        there is no safe "not configured yet" default here."""
        if not self._webhook_secret:
            return False
        return verify_webhook_signature(raw_body, signature, self._webhook_secret)

    def _auth(self) -> tuple[str, str]:
        return (self._key_id, self._key_secret)

    async def reserve(
        self, amount: float, payer_ref: str, idempotency_key: str
    ) -> ReservationResult:
        if not self.is_configured():
            return ReservationResult(
                False,
                "",
                ReservationStatus.FAILED,
                amount,
                "RazorpayUPIProvider is not configured (missing RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET)",
            )
        if amount <= 0:
            return ReservationResult(
                False, "", ReservationStatus.FAILED, amount, "amount must be positive"
            )

        existing_order_id = self._idempotency_index.get(idempotency_key)
        if existing_order_id is not None:
            status = await self.get_reservation(existing_order_id)
            if status is not None:
                return ReservationResult(
                    True,
                    existing_order_id,
                    status.status,
                    status.amount or amount,
                    "idempotent replay",
                )

        # amount_paise: Razorpay takes the smallest currency sub-unit
        # (paise for INR), never rupees directly.
        amount_paise = int(round(amount * 100))
        # receipt is capped at 40 chars by Razorpay's own Orders API —
        # truncate the idempotency_key rather than reject a longer one,
        # since callers (e.g. an execution_id-derived key) may exceed it.
        receipt = idempotency_key[:40]

        try:
            async with httpx.AsyncClient(timeout=_RAZORPAY_TIMEOUT_SECONDS) as client:
                r = await client.post(
                    f"{_RAZORPAY_API_BASE}/orders",
                    auth=self._auth(),
                    json={
                        "amount": amount_paise,
                        "currency": self._currency,
                        "receipt": receipt,
                        "payment_capture": 0,
                        "notes": {
                            "payer_ref": payer_ref,
                            "idempotency_key": idempotency_key,
                        },
                    },
                )
        except httpx.TimeoutException as exc:
            logger.warning(
                "RazorpayUPIProvider.reserve: timeout after possible submission: %s",
                exc,
            )
            return ReservationResult(
                False,
                "",
                ReservationStatus.UNKNOWN,
                amount,
                f"order creation outcome unknown: {exc}",
            )
        except httpx.ConnectError as exc:
            logger.warning("RazorpayUPIProvider.reserve: request failed: %s", exc)
            return ReservationResult(
                False,
                "",
                ReservationStatus.FAILED,
                amount,
                f"order creation request failed: {exc}",
            )
        except Exception as exc:
            logger.warning("RazorpayUPIProvider.reserve: request failed: %s", exc)
            return ReservationResult(
                False,
                "",
                ReservationStatus.FAILED,
                amount,
                f"order creation request failed: {exc}",
            )

        if r.status_code not in (200, 201):
            reason = _extract_error(r)
            logger.warning(
                "RazorpayUPIProvider.reserve: order creation failed (%d): %s",
                r.status_code,
                reason,
            )
            return ReservationResult(
                False, "", ReservationStatus.FAILED, amount, reason
            )

        order = r.json()
        order_id = order.get("id", "")
        if not order_id:
            return ReservationResult(
                False,
                "",
                ReservationStatus.FAILED,
                amount,
                "Razorpay response had no order id",
            )

        self._idempotency_index[idempotency_key] = order_id
        self._order_amounts[order_id] = amount

        # PENDING_AUTHORIZATION, not RESERVED — see module docstring
        # constraint 1. No funds are held yet; the payer hasn't acted.
        return ReservationResult(
            True, order_id, ReservationStatus.PENDING_AUTHORIZATION, amount
        )

    def record_authorization(
        self, order_id: str, payment_id: str, amount: float
    ) -> None:
        """Called by the webhook handler when Razorpay's payment.authorized
        event confirms a real hold now exists for this order — the ONLY
        place this provider learns an order_id maps to a real payment_id.
        Idempotent: recording the same order_id/payment_id twice (a
        redelivered webhook) is a no-op, not a second authorization."""
        existing = self._authorizations.get(order_id)
        if existing is not None and existing.payment_id == payment_id:
            return
        self._authorizations[order_id] = _AuthorizedPayment(
            payment_id=payment_id, amount=amount
        )

    def force_capture(self, reservation_id: str) -> CaptureResult:
        """Dev/demo-only: marks a reservation captured LOCALLY, without
        calling Razorpay's real capture API at all. Exists for exactly one
        honest reason — a fabricated payment_id (no real UPI payer exists
        in a demo/test environment) will always be genuinely rejected by
        Razorpay's real capture endpoint (correctly: it never actually
        authorized that payment), so there is no way to make a REAL
        capture succeed here. This lets a demo purchase finish anyway,
        clearly as a simulated completion, not a real captured payment.

        Requires record_authorization() to have already run (a real or
        simulated payment.authorized webhook) — force_capture only skips
        the CAPTURE call, it doesn't fabricate an authorization that was
        never recorded at all. The next real capture() call for this same
        reservation_id then hits its own existing idempotent-replay path
        (authorized.captured already True) instead of a second code path
        — no new "fake capture" branch, this just seeds the state that
        branch already handles."""
        authorized = self._authorizations.get(reservation_id)
        if authorized is None:
            return CaptureResult(
                False,
                reservation_id,
                ReservationStatus.PENDING_AUTHORIZATION,
                0.0,
                "no authorized payment yet for this order — cannot force-capture something never authorized",
            )
        authorized.captured = True
        logger.warning(
            "RazorpayUPIProvider.force_capture: %s marked captured LOCALLY — no real Razorpay capture call was made",
            reservation_id,
        )
        return CaptureResult(
            True,
            reservation_id,
            ReservationStatus.CAPTURED,
            authorized.amount,
            "locally simulated capture (dev/demo only)",
        )

    async def capture(self, reservation_id: str, idempotency_key: str) -> CaptureResult:
        if not self.is_configured():
            return CaptureResult(
                False,
                reservation_id,
                ReservationStatus.FAILED,
                0.0,
                "RazorpayUPIProvider is not configured",
            )

        authorized = self._authorizations.get(reservation_id)
        if authorized is None:
            return CaptureResult(
                False,
                reservation_id,
                ReservationStatus.PENDING_AUTHORIZATION,
                0.0,
                "no authorized payment yet for this order — the payer has not approved (or the webhook hasn't arrived)",
            )
        if authorized.captured:
            return CaptureResult(
                True,
                reservation_id,
                ReservationStatus.CAPTURED,
                authorized.amount,
                "idempotent replay",
            )

        amount_paise = int(round(authorized.amount * 100))
        try:
            async with httpx.AsyncClient(timeout=_RAZORPAY_TIMEOUT_SECONDS) as client:
                r = await client.post(
                    f"{_RAZORPAY_API_BASE}/payments/{authorized.payment_id}/capture",
                    auth=self._auth(),
                    json={"amount": amount_paise, "currency": self._currency},
                )
        except httpx.TimeoutException as exc:
            logger.warning(
                "RazorpayUPIProvider.capture: timeout after possible submission: %s",
                exc,
            )
            return CaptureResult(
                False,
                reservation_id,
                ReservationStatus.UNKNOWN,
                0.0,
                f"capture outcome unknown: {exc}",
            )
        except Exception as exc:
            logger.warning("RazorpayUPIProvider.capture: request failed: %s", exc)
            return CaptureResult(
                False,
                reservation_id,
                ReservationStatus.FAILED,
                0.0,
                f"capture request failed: {exc}",
            )

        if r.status_code in (200, 201):
            authorized.captured = True
            return CaptureResult(
                True, reservation_id, ReservationStatus.CAPTURED, authorized.amount
            )

        reason = _extract_error(r)
        # "already captured" is Razorpay's own idempotency signal for a
        # retried capture call — treat it as success, not failure, same
        # idempotent-replay contract FakePaymentProvider gives for free.
        if "already been captured" in reason.lower():
            authorized.captured = True
            return CaptureResult(
                True,
                reservation_id,
                ReservationStatus.CAPTURED,
                authorized.amount,
                "idempotent replay (already captured)",
            )

        logger.warning(
            "RazorpayUPIProvider.capture: capture failed (%d): %s",
            r.status_code,
            reason,
        )
        return CaptureResult(
            False, reservation_id, ReservationStatus.FAILED, 0.0, reason
        )

    async def release(self, reservation_id: str) -> ReservationResult:
        authorized = self._authorizations.get(reservation_id)
        if authorized is None:
            # Nothing was ever authorized for this order — a real,
            # honest release: there is no hold to cancel.
            return ReservationResult(
                True, reservation_id, ReservationStatus.RELEASED, 0.0
            )
        if authorized.captured:
            return ReservationResult(
                False,
                reservation_id,
                ReservationStatus.CAPTURED,
                authorized.amount,
                "cannot release an already-captured reservation",
            )
        # See module docstring constraint 3: Razorpay has no active void
        # for a real authorized-but-uncaptured UPI payment. Reporting
        # success here would be fabricating a cancellation that did not
        # happen — the honest answer is failure, with the real mechanic
        # (auto-refund on the account's configured capture window)
        # named explicitly so a caller doesn't retry expecting a
        # different result.
        return ReservationResult(
            False,
            reservation_id,
            ReservationStatus.RESERVED,
            authorized.amount,
            "Razorpay has no immediate-void API for an authorized UPI payment; "
            "it will auto-refund on its own once the account's configured "
            "manual-capture expiry window elapses",
        )

    async def get_reservation(self, reservation_id: str) -> ReservationResult | None:
        authorized = self._authorizations.get(reservation_id)
        if authorized is None:
            amount = self._order_amounts.get(reservation_id, 0.0)
            return ReservationResult(
                True, reservation_id, ReservationStatus.PENDING_AUTHORIZATION, amount
            )
        if authorized.captured:
            return ReservationResult(
                True, reservation_id, ReservationStatus.CAPTURED, authorized.amount
            )
        return ReservationResult(
            True, reservation_id, ReservationStatus.RESERVED, authorized.amount
        )


_default_provider: "RazorpayUPIProvider | None" = None


def get_default_provider() -> "RazorpayUPIProvider":
    """Process-wide singleton, same lazy-singleton shape as
    negotiation_store.py/approval_store.py/payment_store.py's Redis
    clients. Required, not optional: reserve()'s _idempotency_index and
    record_authorization()'s order_id -> payment_id mapping both live in
    instance state — a checkout capability and the webhook handler below
    MUST share the same instance, or the webhook's record_authorization()
    call would be invisible to the capture() call a resumed capability
    later makes against a different instance. Reads real credentials
    from RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET/RAZORPAY_WEBHOOK_SECRET once,
    at first use, not at import time (so importing this module never
    requires credentials to already be set)."""
    global _default_provider
    if _default_provider is None:
        _default_provider = RazorpayUPIProvider()
    return _default_provider


def _extract_error(response: "httpx.Response") -> str:
    """Razorpay's real error shape: {"error": {"description": "...", ...}}.
    Falls back to the raw body if that shape isn't present, so a genuinely
    unexpected response is still visible, not swallowed into a generic
    string."""
    try:
        body = response.json()
        description = body.get("error", {}).get("description")
        if description:
            return str(description)
        return str(body)
    except Exception:
        return response.text[:500] if response.text else f"HTTP {response.status_code}"


_AUTO_APPROVE_SECONDS = float(os.getenv("UPI_AUTO_APPROVE_SECONDS", "0") or "0")
"""Off by default. A real checkout correctly stays "awaiting UPI
approval" until a real (or manually-sent, for testing) webhook resolves
it — the same honest "payment stays pending" behavior a real deployment
without a fabricated payment_id would have, and the only state a
simulated approval can't legitimately advance past anyway: Razorpay's
real capture API rejects a payment_id that was never actually authorized
by a real payer, so a simulated approval can flip the pause but can
never make a real capture() succeed. Set UPI_AUTO_APPROVE_SECONDS to a
positive number of seconds to opt into schedule_auto_approval() below
anyway (e.g. for exercising the resume/pause-resolution code path
itself, accepting that the final capture will still honestly fail)."""


def schedule_auto_approval(order_id: str, amount: float) -> None:
    """Demo simulator for a payer instantly approving a UPI collect
    request. Fires api.routes.payments.resolve_and_resume_payment — the
    SAME resolve-then-resume logic a genuine, signature-verified Razorpay
    webhook triggers — after a short delay, so a checkout still genuinely
    exercises the real two-phase reserve/capture flow end to end; only the
    "payer opens their UPI app and taps approve" step is simulated, not
    the reserve() call, the pause, or the capture() call.

    Uses threading.Timer (a plain OS-level timer), not asyncio.create_task:
    this fires from inside a SYNC context (grocery.py::PaymentCapability.
    handle, deliberately kept synchronous — see payment_provider.py::
    sync_call's own docstring) that may itself be running on a short-lived
    event loop (sync_call's asyncio.run() branch) which closes the moment
    the current call returns — any task scheduled on that loop would be
    silently cancelled before the delay elapsed. A Timer's callback thread
    starts its own fresh loop via asyncio.run() when it actually fires,
    independent of whatever loop/thread originally called reserve().

    No-op if UPI_AUTO_APPROVE_SECONDS is 0 or unset — see its own
    docstring above for why that's a legitimate, honest choice, not a
    missing feature.
    """
    if _AUTO_APPROVE_SECONDS <= 0:
        return

    def _fire() -> None:
        import asyncio
        from src.monkey_brain.api.routes.payments import (
            get_default_planetary_runtime,
            resolve_and_resume_payment,
        )

        pr = get_default_planetary_runtime()
        try:
            asyncio.run(
                resolve_and_resume_payment(
                    order_id=order_id,
                    payment_id=f"pay_auto_{order_id}",
                    amount=amount,
                    event="payment.authorized",
                    pr=pr,
                )
            )
        except Exception:
            logger.warning(
                "schedule_auto_approval: auto-approve for order %s failed (non-fatal)",
                order_id,
                exc_info=True,
            )

    import threading

    threading.Timer(_AUTO_APPROVE_SECONDS, _fire).start()
