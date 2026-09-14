"""Real UPI Reserve Pay webhook — the endpoint Razorpay itself calls back
on, confirming a real payer's action outside this process (approving, or
not approving, a UPI collect request in their own app).

Real Razorpay webhook payload shape (confirmed against Razorpay's own
docs while writing this, not guessed):
    {"event": "payment.authorized" | "payment.captured" | "payment.failed",
     "payload": {"payment": {"entity": {"id": "pay_...", "order_id": "order_...",
                                         "amount": <paise>, "status": "..."}}}}
Signed via the X-Razorpay-Signature header — HMAC-SHA256 over the RAW
request body (razorpay_upi_provider.py::verify_webhook_signature).

Unlike every other route in this codebase, there is no calling actor and
no Bearer/OPA identity to check — the ONLY trust boundary here is the
webhook signature itself. A request with a missing, wrong, or unverifiable
signature is rejected outright (401) before any payment state is touched.

resolve_and_resume_payment() is the actual business logic (record the
authorization/failure, resolve the paused execution, resume it) — factored
out so the auto-approve demo simulator (kernel/domains/razorpay_upi_provider.py
::schedule_auto_approval) can call the EXACT same resolve/resume path a
real, externally-verified webhook uses, without re-deriving it. The
simulator runs entirely server-side (never receives an untrusted request),
so it calls this directly instead of signing and POSTing a loopback HTTP
request to itself.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent
from src.monkey_brain.kernel.domains.razorpay_upi_provider import get_default_provider
from src.monkey_brain.kernel.pipeline.payment_store import (
    load_pending_payment_by_reservation,
    resolve_pending_payment,
)

logger = logging.getLogger("agentos.gateway.payments")
router = APIRouter()

_default_planetary_runtime: Any = None


def set_default_planetary_runtime(pr: Any) -> None:
    """Called once from kernel.py::Kernel._phase_planetary, right after
    app.state.planetary_runtime is set — the SAME real PlanetaryRuntime
    instance, reachable here without a Request (this process runs exactly
    one FastAPI app / one PlanetaryRuntime, so a module-level singleton is
    honest, not a shortcut around a genuinely multi-instance case). Needed
    because the auto-approve simulator fires from a background timer
    thread, which has no Request/app.state to read app.state.planetary_
    runtime from the way every real HTTP route does."""
    global _default_planetary_runtime
    _default_planetary_runtime = pr


def get_default_planetary_runtime() -> Any:
    return _default_planetary_runtime


async def require_razorpay_webhook_auth(
    request: Request,
    x_razorpay_signature: str = Header(default="", alias="X-Razorpay-Signature"),
) -> bytes:
    """Trust boundary for Razorpay: HMAC over the raw body, not a user JWT."""
    raw_body = await request.body()
    provider = get_default_provider()
    if not provider.verify_webhook(raw_body, x_razorpay_signature):
        logger.warning("razorpay_webhook: signature verification failed — rejecting")
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    return raw_body


def _get_planetary_runtime(request: Request) -> Any:
    """Same lookup approval.py/negotiation.py already use — kept
    consistent rather than reinvented."""
    selector = getattr(getattr(request.app.state, "kernel", None), "runtime_selector", None)
    if selector is not None:
        try:
            return selector.select("planetary")
        except LookupError:
            logger.debug("_get_planetary_runtime: suppressed exception", exc_info=True)
    return getattr(request.app.state, "planetary_runtime", None)


async def resolve_and_resume_payment(
    order_id: str,
    payment_id: str,
    amount: float,
    event: str,
    pr: Any,
    failure_reason: str = "payment failed",
) -> dict[str, Any]:
    """The real resolve-then-resume logic for a payment.authorized/
    payment.failed confirmation, regardless of whether it arrived via a
    genuine, signature-verified Razorpay webhook or the auto-approve demo
    simulator. Mirrors negotiation.py's /negotiate resume shape — record
    the real outcome, then resume the SAME paused execution via
    meta.resume_execution_id.

    Every non-matching or already-resolved case returns handled=False,
    never an error — "this doesn't correspond to anything we're waiting
    on" (an order this backend never paused on, or a redelivered/duplicate
    confirmation) is a legitimate, non-error outcome, not a fault.
    """
    provider = get_default_provider()

    if event not in ("payment.authorized", "payment.failed"):
        return {
            "handled": False,
            "event": event,
            "reason": "not a pause-resolving event",
        }
    if not order_id:
        return {"handled": False, "event": event, "reason": "no order_id in payload"}

    pending = load_pending_payment_by_reservation(order_id)
    if pending is None:
        return {
            "handled": False,
            "event": event,
            "order_id": order_id,
            "reason": "no pending payment for this order",
        }
    if pending.decided is not None:
        return {
            "handled": False,
            "event": event,
            "order_id": order_id,
            "reason": "already resolved",
        }

    if event == "payment.authorized":
        # The ONLY place this provider instance learns order_id ->
        # payment_id — capture() (called by the resumed capability
        # below) cannot run without this.
        provider.record_authorization(order_id, payment_id, amount or pending.amount)
        resolve_pending_payment(pending.execution_id, captured=True, status="reserved")
    else:  # payment.failed
        resolve_pending_payment(pending.execution_id, captured=False, status="failed", reason=failure_reason)

    if pr is None or not pending.actor_id:
        # Resolved the payment state, but can't resume the tick from
        # here (no runtime wired, or no actor on record) — the state is
        # still correctly recorded for whenever the execution IS
        # resumed (e.g. the actor's next real request re-enters the
        # checkpoint), so this is a partial, not a lost, update.
        return {
            "handled": True,
            "event": event,
            "order_id": order_id,
            "execution_id": pending.execution_id,
            "resumed": False,
        }

    from src.monkey_brain.kernel.models.prompt import PromptRequest

    prompt_request = PromptRequest(
        question=pending.original_question or "Resume after a real payment confirmation.",
        meta={"resume_execution_id": pending.execution_id},
    )
    pr.restore_actor_belief(pending.actor_id)
    await pr.execute_actor_request(pending.actor_id, prompt_request)
    pr.checkpoint_actor_belief(pending.actor_id)

    return {
        "handled": True,
        "event": event,
        "order_id": order_id,
        "execution_id": pending.execution_id,
        "resumed": True,
    }


@router.post("/payments/webhooks/razorpay", tags=["Payments"])
@idempotent("payments.razorpay_webhook")
async def razorpay_webhook(
    request: Request,
    raw_body: bytes = Depends(require_razorpay_webhook_auth),
) -> dict[str, Any]:
    """Confirms a real UPI authorization/capture/failure and, when it
    resolves an execution this backend actually has paused
    (kernel/pipeline/payment_store.py::PendingPayment), resumes that
    SAME execution. See resolve_and_resume_payment() above for the actual
    logic — this route's own job is strictly the untrusted-HTTP-boundary
    part: verify the signature, parse the real payload shape, then hand
    off.

    Every non-matching or already-resolved event still returns 200 —
    Razorpay retries a webhook that doesn't get a 2xx, and "this event
    doesn't correspond to anything we're waiting on" is a legitimate,
    non-error outcome here, not a fault.
    """
    import json
    from src.monkey_brain.kernel.security_boundary import ensure_governed
    from src.monkey_brain.kernel.trusted_auth import (
        bind_trusted_auth,
        evidence_for_service,
    )

    bind_trusted_auth(evidence_for_service("razorpay-webhook"))
    body = json.loads(raw_body.decode("utf-8") or "{}")
    event = body.get("event", "")
    payment_entity = (
        body.get("payload", {}).get("payment", {}).get("entity", {}) if isinstance(body.get("payload"), dict) else {}
    )
    order_id = payment_entity.get("order_id", "")
    payment_id = payment_entity.get("id", "")
    amount_paise = payment_entity.get("amount", 0)
    amount = round(float(amount_paise) / 100, 2) if amount_paise else 0.0

    pr = _get_planetary_runtime(request)
    failure_reason = payment_entity.get("error_description", "payment failed")
    return await ensure_governed(
        "payments.webhook",
        order_id or "razorpay",
        lambda: resolve_and_resume_payment(order_id, payment_id, amount, event, pr, failure_reason),
        skip_authz=True,
    )


@router.post("/payments/{reservation_id}/simulate-capture", tags=["Payments"])
@idempotent("payments.simulate_capture")
async def simulate_capture(
    reservation_id: str,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    """Dev/demo-only: marks a reservation captured LOCALLY."""
    from src.monkey_brain.kernel.production_gates import insecure_dev_mode

    if not insecure_dev_mode():
        raise HTTPException(status_code=403, detail="simulate-capture is insecure-dev only")
    provider = get_default_provider()
    result = provider.force_capture(reservation_id)
    return {
        "success": result.success,
        "reservation_id": reservation_id,
        "status": result.status.value,
        "captured_amount": result.captured_amount,
        "reason": result.reason,
    }


@router.post("/payments/{reservation_id}/dev-complete", tags=["Payments"])
@idempotent("payments.dev_complete_payment")
async def dev_complete_payment(
    reservation_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    """Dev/demo-only: the one-call version of the real payment.authorized
    webhook + simulate-capture + resume sequence."""
    from src.monkey_brain.kernel.production_gates import insecure_dev_mode

    if not insecure_dev_mode():
        raise HTTPException(status_code=403, detail="dev-complete is insecure-dev only")
    provider = get_default_provider()
    pending = load_pending_payment_by_reservation(reservation_id)
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail=f"no pending payment for reservation {reservation_id!r}",
        )
    if pending.decided is not None:
        raise HTTPException(
            status_code=409,
            detail=f"reservation {reservation_id!r} was already resolved",
        )

    fake_payment_id = f"pay_dev_{reservation_id}"
    provider.record_authorization(reservation_id, fake_payment_id, pending.amount)
    capture_result = provider.force_capture(reservation_id)
    if not capture_result.success:
        raise HTTPException(status_code=500, detail=f"force_capture failed: {capture_result.reason}")

    pr = _get_planetary_runtime(request)
    outcome = await resolve_and_resume_payment(
        order_id=reservation_id,
        payment_id=fake_payment_id,
        amount=pending.amount,
        event="payment.authorized",
        pr=pr,
    )
    return {
        "success": True,
        "reservation_id": reservation_id,
        "captured_amount": capture_result.captured_amount,
        **outcome,
    }
