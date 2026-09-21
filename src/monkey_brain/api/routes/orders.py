"""Orders API — checkout, payment, cancellation, returns, refunds.

World Graph Builder / Cognitive Reasoning separation (see commerce.py's
module docstring) — deterministic mutations of PlanetaryRuntime.
knowledge_graph, no reasoning.

Pre-commit negotiation gate (kernel/society/transition_gate.py) — KNOWN,
INTENTIONAL BYPASS: this route calls OrderCreationCapability/
PaymentCapability.handle() directly, not through ActionExecutor, so
TransitionGate.evaluate() never runs here. That is consistent with this
router's own stated scope (deterministic admin/benchmark world-building,
not actor-driven cognitive purchasing) and it stays behind
perm-manage-actors. If this route is ever exposed as a stand-in for real
actor checkout (rather than test/admin world setup), it must route
through kernel/domains/vertical_router.py::build_execution_engine's
ActionExecutor instead, so the same gate applies.

Actor-identity bypass (Doot audit BYPASS-02) — FIXED: every route below
that carries a customer-facing actor_id (create_order, pay_for_order,
cancel_order_route, confirm_receipt_route, request_return) used to trust
body.actor_id outright — any caller holding only perm-manage-actors
(which every route here already requires) could silently act as ANY
actor_id. Each now also calls dependencies.authorize_acting_for(), which
requires the authenticated caller to BE that actor_id, or to hold the
separate, explicit ACT_ON_BEHALF_PERMISSION. perm-manage-actors alone is
deliberately no longer sufficient to impersonate an actor in a real
commerce transaction. approve_return_route (approved_by) and
refund_order_route (refunded_by) are unchanged — those identify the
ADMIN/merchant performing an action on an order, not a customer being
impersonated, so there is no actor_id to check self-or-behalf against.

POST /orders                       — checkout (creates a real order)
GET  /orders/{id}                  — order detail
POST /orders/{id}/payment          — authorize + charge payment
POST /orders/{id}/cancel           — cancel a paid order before it ships
POST /orders/{id}/confirm-receipt  — customer confirms delivery
POST /orders/{id}/return           — customer requests a return
POST /orders/{id}/return/approve   — merchant approves a return
POST /orders/{id}/refund           — standalone partial/goodwill refund
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.dependencies import authorize_acting_for, require_permission
from src.monkey_brain.api.gateway_models import (
    OrderCancelRequest,
    OrderConfirmReceiptRequest,
    OrderCreateRequest,
    OrderPaymentRequest,
    OrderRefundRequest,
    OrderResponse,
    OrderReturnApproveRequest,
    OrderReturnRequest,
)
from src.monkey_brain.api.audit_decorator import audited
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.gateway.orders")
router = APIRouter()


def _get_planetary_runtime(request: Request) -> Any:
    return getattr(request.app.state, "planetary_runtime", None)


def _kg(request: Request) -> Any:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    return pr.knowledge_graph


def _result(d: dict[str, Any]) -> dict[str, Any]:
    if not d.get("success", True):
        error = d.get("error", "request failed")
        status = 404 if "no such" in error or "not found" in error else 400
        raise HTTPException(status_code=status, detail=error)
    return d


async def _commit_order(action: str, resource: str, effect):
    from src.monkey_brain.kernel.security_boundary import ensure_governed

    return await ensure_governed(action, resource, effect, skip_authz=True)


@router.post("/orders", tags=["Orders"], response_model=OrderResponse)
@idempotent("orders.create")
async def create_order(
    body: OrderCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.grocery import OrderCreationCapability

    await authorize_acting_for(request, user_id, body.actor_id)

    def _mutate():
        return OrderCreationCapability().handle(
            {
                "context": {
                    "knowledge_graph": _kg(request),
                    "actor_id": body.actor_id,
                    "selected_product": body.items,
                    "question": body.question,
                    "resume_order_id": body.resume_order_id,
                }
            }
        )

    result = await _commit_order("orders.create", body.actor_id, _mutate)
    return _result(result)


@router.get("/orders/{order_id}", tags=["Orders"])
async def get_order(
    order_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    order = _kg(request).get_entity(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"no such order {order_id!r}")
    return {"order_id": order_id, "name": order.name, **order.attributes}


@router.post("/orders/{order_id}/payment", tags=["Orders"])
@audited("orders.payment")
@idempotent("orders.payment")
async def pay_for_order(
    order_id: str,
    body: OrderPaymentRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.grocery import (
        PaymentCapability,
        PaymentConfirmationCapability,
    )

    await authorize_acting_for(request, user_id, body.actor_id)
    kg = _kg(request)
    order_entity = kg.get_entity(order_id)
    if order_entity is None:
        raise HTTPException(status_code=404, detail=f"no such order {order_id!r}")

    order_view = {"order_id": order_id, **order_entity.attributes}
    # P0 (ADR-020): the authoritative amount is the ORDER's own stored
    # total, never a client-supplied one. Previously a caller could pass
    # body.total and charge an arbitrary amount unrelated to the real
    # order. The client may still echo the total, but it must MATCH the
    # order (a mismatch is a 400, not a silent override); omitting it is
    # always fine. Only when the order carries no total at all (a
    # world-building/test order built without one) does body.total act as
    # a fallback -- there is no authoritative value to protect in that case.
    authoritative_total = order_entity.attributes.get("total")
    if authoritative_total is None:
        total = body.total if body.total is not None else 0
    else:
        if body.total is not None and float(body.total) != float(authoritative_total):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"payment total {body.total!r} does not match order {order_id!r} "
                    f"total {authoritative_total!r}"
                ),
            )
        total = authoritative_total
    context = {
        "knowledge_graph": kg,
        "actor_id": body.actor_id,
        "total": total,
        "order": order_view,
        "selected_product": [
            {"id": item.get("product_id"), "qty": item.get("qty", 1)}
            for item in order_entity.attributes.get("items", [])
        ],
    }
    confirmation = PaymentConfirmationCapability().handle({"context": context})
    if not confirmation.get("success"):
        raise HTTPException(status_code=400, detail=confirmation.get("error", "payment not confirmed"))

    def _mutate():
        return PaymentCapability().handle({"context": context})

    payment = await _commit_order("orders.payment", order_id, _mutate)
    return _result(payment)


@router.post("/orders/{order_id}/cancel", tags=["Orders"])
@audited("orders.cancel")
@idempotent("orders.cancel")
async def cancel_order_route(
    order_id: str,
    body: OrderCancelRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.grocery import cancel_order

    await authorize_acting_for(request, user_id, body.actor_id)
    return _result(
        await _commit_order(
            "orders.cancel",
            order_id,
            lambda: cancel_order(_kg(request), order_id, actor_id=body.actor_id),
        )
    )


@router.post("/orders/{order_id}/confirm-receipt", tags=["Orders"])
@idempotent("orders.confirm_receipt")
async def confirm_receipt_route(
    order_id: str,
    body: OrderConfirmReceiptRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.logistics import confirm_receipt
    from src.monkey_brain.kernel.society.context_stream import (
        ContextEvent,
        ContextEventType,
    )

    await authorize_acting_for(request, user_id, body.actor_id)
    result = _result(
        await _commit_order(
            "orders.confirm_receipt",
            order_id,
            lambda: confirm_receipt(_kg(request), order_id, actor_id=body.actor_id),
        )
    )

    # True Multi-Actor Coordination: this is the real point an order's
    # items become eligible for a loyalty award or a review
    # (award_points()/leave_review() both require status=="completed",
    # which only confirm_receipt() ever sets — "delivered" alone isn't
    # enough) — matching the spec's "ShipmentDelivered" event vocabulary
    # even though it fires here rather than at the earlier
    # mark_shipment_delivered() step, since that's genuinely when the
    # real precondition for both reactions is first met. Same direct-
    # propagation pattern as events.py's WarehouseClosed.
    pr = _get_planetary_runtime(request)
    if pr is not None:
        context_events_before = pr.context_stream.event_count
        pr.context_stream.publish(
            ContextEvent(
                event_type=ContextEventType.WORLD_UPDATE,
                description=f"Order {order_id} receipt confirmed",
                payload={"order_id": order_id, "domain_event": "ShipmentDelivered"},
            )
        )
        coordination_trace: list[dict[str, Any]] = []
        async with pr._tick_lock:
            (
                propagated_actors,
                propagated_societies,
                termination_reason,
                domain_events_seen,
            ) = await pr._propagate_coordination(
                from_version=context_events_before,
                trace=coordination_trace,
                already_visited=set(),
            )
        result["coordination_trace"] = coordination_trace
        result["execution_scope"] = {
            "societies_coordinated": len(propagated_societies),
            "actors_coordinated": len(propagated_actors),
            "propagation_steps": len(coordination_trace),
            "propagation_depth": max((s["depth"] for s in coordination_trace), default=0),
            "termination_reason": termination_reason,
            "domain_events_seen": sorted(domain_events_seen),
        }
    return result


@router.post("/orders/{order_id}/return", tags=["Orders"])
@idempotent("orders.return_request")
async def request_return(
    order_id: str,
    body: OrderReturnRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.grocery import return_order

    await authorize_acting_for(request, user_id, body.actor_id)
    return _result(
        await _commit_order(
            "orders.return_request",
            order_id,
            lambda: return_order(_kg(request), order_id, actor_id=body.actor_id, reason=body.reason),
        )
    )


@router.post("/orders/{order_id}/return/approve", tags=["Orders"])
@audited("orders.return_approve")
@idempotent("orders.return_approve")
async def approve_return_route(
    order_id: str,
    body: OrderReturnApproveRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.grocery import approve_return

    return _result(
        await _commit_order(
            "orders.return_approve",
            order_id,
            lambda: approve_return(_kg(request), order_id, approved_by=body.approved_by),
        )
    )


@router.post("/orders/{order_id}/refund", tags=["Orders"])
@audited("orders.refund")
@idempotent("orders.refund")
async def refund_order_route(
    order_id: str,
    body: OrderRefundRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.grocery import refund_order

    return _result(
        await _commit_order(
            "orders.refund",
            order_id,
            lambda: refund_order(
                _kg(request),
                order_id,
                amount=body.amount,
                reason=body.reason,
                refunded_by=body.refunded_by,
            ),
        )
    )
