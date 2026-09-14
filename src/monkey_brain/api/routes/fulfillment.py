"""Fulfillment API — picking, packing, shipping, delivery.

World Graph Builder / Cognitive Reasoning separation (see commerce.py's
module docstring) — deterministic mutations of PlanetaryRuntime.
knowledge_graph, no reasoning.

POST /fulfillment/pick        — assign a picker (or autonomous cart) at a store
POST /fulfillment/pack        — pack an order's items into real packages
POST /shipments                — create a real, tracked shipment
GET  /shipments/{id}          — shipment detail
POST /shipments/{id}/transit  — mark a shipment in transit
POST /shipments/{id}/deliver  — mark a shipment delivered
POST /shipments/{id}/lost     — carrier reports a shipment lost
POST /shipments/{id}/replace  — issue a replacement for a lost shipment
POST /shipments/{id}/delay    — report a carrier delay
GET  /orders/{id}/tracking    — track every shipment for an order
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import (
    PackRequest,
    PickRequest,
    ShipmentCreateRequest,
    ShipmentDelayRequest,
    ShipmentLostRequest,
    ShipmentResponse,
)
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.gateway.fulfillment")
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


@router.post("/fulfillment/pick", tags=["Fulfillment"])
@idempotent("fulfillment.pick")
async def pick_route(
    body: PickRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.logistics import assign_picker

    return _result(assign_picker(_kg(request), body.store_id))


@router.post("/fulfillment/pack", tags=["Fulfillment"])
@idempotent("fulfillment.pack")
async def pack_route(
    body: PackRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.logistics import pack_order

    return _result(pack_order(body.items, box_capacity=body.box_capacity))


@router.post("/shipments", tags=["Fulfillment"], response_model=ShipmentResponse)
@idempotent("shipments.create")
async def create_shipment_route(
    body: ShipmentCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.logistics import create_shipment

    return _result(
        create_shipment(
            _kg(request),
            body.order_id,
            body.packages,
            rider_id=body.rider_id,
            carrier=body.carrier,
        )
    )


@router.get("/shipments/{shipment_id}", tags=["Fulfillment"])
async def get_shipment_route(
    shipment_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.logistics import get_shipment

    return _result(get_shipment(_kg(request), shipment_id))


@router.post("/shipments/{shipment_id}/transit", tags=["Fulfillment"])
@idempotent("shipments.transit")
async def mark_transit_route(
    shipment_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.logistics import mark_shipment_in_transit

    return _result(mark_shipment_in_transit(_kg(request), shipment_id))


@router.post("/shipments/{shipment_id}/deliver", tags=["Fulfillment"])
@idempotent("shipments.deliver")
async def mark_delivered_route(
    shipment_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.logistics import mark_shipment_delivered

    return _result(mark_shipment_delivered(_kg(request), shipment_id))


@router.post("/shipments/{shipment_id}/lost", tags=["Fulfillment"])
@idempotent("shipments.lost")
async def mark_lost_route(
    shipment_id: str,
    body: ShipmentLostRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.logistics import mark_shipment_lost

    return _result(mark_shipment_lost(_kg(request), shipment_id, reported_by=body.reported_by))


@router.post("/shipments/{shipment_id}/replace", tags=["Fulfillment"])
@idempotent("shipments.replace")
async def replace_shipment_route(
    shipment_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.logistics import issue_replacement_shipment

    return _result(issue_replacement_shipment(_kg(request), shipment_id))


@router.post("/shipments/{shipment_id}/delay", tags=["Fulfillment"])
@idempotent("shipments.delay")
async def delay_shipment_route(
    shipment_id: str,
    body: ShipmentDelayRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.logistics import report_shipment_delay

    return _result(report_shipment_delay(_kg(request), shipment_id, body.reason, new_eta=body.new_eta))


@router.get("/orders/{order_id}/tracking", tags=["Fulfillment"])
async def track_order_route(
    order_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.logistics import track_order

    return _result(track_order(_kg(request), order_id))
