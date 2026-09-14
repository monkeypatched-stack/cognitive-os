import base64
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, WebSocket, WebSocketDisconnect, status
from jose import JWTError

from services.auth.helpers import nats_store, websocket_broadcast
from services.auth.helpers.revocation import is_jti_revoked
from services.auth.helpers.tokens import decode_access_token
from services.common.auth import get_current_user
from services.common.config import settings
from services.common.event_types import infer_event_type

router = APIRouter()
log = logging.getLogger("uvicorn.error")


def _payload_summary(payload) -> dict:
    if not isinstance(payload, dict):
        return {"payload_kind": type(payload).__name__}
    return {
        "payload_id": payload.get("id"),
        "category": payload.get("category"),
        "scope": payload.get("scope"),
        "type": payload.get("type"),
        "work_order_id": payload.get("work_order_id"),
    }


def _token_from_websocket(websocket: WebSocket) -> str | None:
    query_token = websocket.query_params.get("token")
    if query_token:
        return query_token

    authorization = websocket.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def _authenticate(websocket: WebSocket) -> dict:
    token = _token_from_websocket(websocket)
    if not token:
        log.warning("WebSocket connection rejected: missing bearer token")
        await websocket.accept()
        await websocket.send_json(
            {"status": "error", "detail": "Bearer token required"}
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return {}

    try:
        payload = decode_access_token(token)
    except JWTError:
        log.warning("WebSocket connection rejected: invalid or expired token")
        await websocket.accept()
        await websocket.send_json(
            {"status": "error", "detail": "Invalid or expired token"}
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return {}
    # Security audit P1-5: same live jti-revocation check as the HTTP auth
    # path (services.common.auth.get_current_user) — a WebSocket connection
    # is a separate authentication chokepoint and was silently exempt.
    if await is_jti_revoked(payload.get("jti")):
        log.warning("WebSocket connection rejected: token has been revoked")
        await websocket.accept()
        await websocket.send_json(
            {"status": "error", "detail": "Token has been revoked"}
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return {}
    return payload


def _event_payload(message: dict, current_user: dict) -> dict:
    payload_type = "text"
    payload = None

    if message.get("text") is not None:
        text = message["text"]
        try:
            payload = json.loads(text)
            payload_type = "json"
        except json.JSONDecodeError:
            payload = text
    elif message.get("bytes") is not None:
        payload = base64.b64encode(message["bytes"]).decode("ascii")
        payload_type = "binary_base64"

    if isinstance(payload, dict) and _looks_like_event_envelope(payload):
        return _normalize_event_envelope(payload, current_user, source="websocket")

    return _new_event_envelope(
        payload, current_user, source="websocket", payload_type=payload_type
    )


def _api_event_payload(payload: dict, current_user: dict) -> dict:
    if _looks_like_event_envelope(payload):
        return _normalize_event_envelope(
            payload, current_user, source=str(payload.get("source") or "api")
        )
    if payload.get("text") is not None or payload.get("bytes") is not None:
        return _event_payload(payload, current_user)
    return _new_event_envelope(payload, current_user, source="api", payload_type="json")


def _looks_like_event_envelope(payload: dict) -> bool:
    return (
        isinstance(payload, dict)
        and "payload" in payload
        and (
            "payload_type" in payload
            or "event_id" in payload
            or "received_at" in payload
            or "source" in payload
        )
    )


def _new_event_envelope(
    payload,
    current_user: dict,
    *,
    source: str,
    payload_type: str,
) -> dict:
    return {
        "event_id": str(uuid4()),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "event_type": infer_event_type({"payload": payload}),
        "subject": settings.NATS_EVENTS_SUBJECT,
        "user_id": current_user.get("sub") or current_user.get("user_id"),
        "payload_type": payload_type,
        "payload": payload,
    }


def _normalize_event_envelope(
    payload: dict, current_user: dict, *, source: str
) -> dict:
    event = dict(payload)
    event["event_id"] = str(event.get("event_id") or uuid4())
    event["received_at"] = str(
        event.get("received_at") or datetime.now(timezone.utc).isoformat()
    )
    event["source"] = str(event.get("source") or source)
    event["event_type"] = str(event.get("event_type") or infer_event_type(event))
    event["subject"] = str(event.get("subject") or settings.NATS_EVENTS_SUBJECT)
    event["user_id"] = str(
        event.get("user_id")
        or current_user.get("sub")
        or current_user.get("user_id")
        or ""
    )
    event["payload_type"] = str(
        event.get("payload_type")
        or ("json" if isinstance(event.get("payload"), dict) else "text")
    )
    return event


async def _publish_event(event: dict) -> None:
    log.info(
        "Publishing scan event to NATS event_id=%s source=%s subject=%s payload_type=%s summary=%s",
        event.get("event_id"),
        event.get("source"),
        event.get("subject"),
        event.get("payload_type"),
        _payload_summary(event.get("payload")),
    )
    subject = await nats_store.publish_typed_event(event)
    log.info(
        "Published scan event to NATS event_id=%s subject=%s summary=%s",
        event.get("event_id"),
        subject,
        _payload_summary(event.get("payload")),
    )


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def publish_event_from_api(
    payload: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    event = _api_event_payload(payload, current_user)
    log.info(
        "HTTP scan event received event_id=%s user_id=%s summary=%s",
        event["event_id"],
        event["user_id"],
        _payload_summary(payload),
    )
    await _publish_event(event)
    return {
        "status": "published",
        "event_id": event["event_id"],
        "subject": event.get("subject"),
    }


@router.websocket("/events")
async def websocket_event_listener(websocket: WebSocket):
    current_user = await _authenticate(websocket)
    if not current_user:
        return

    user_id = current_user.get("sub") or current_user.get("user_id")
    await websocket.accept()
    log.info(
        "WebSocket connected user_id=%s client=%s subject=%s",
        user_id,
        websocket.client,
        settings.NATS_EVENTS_SUBJECT,
    )
    await websocket.send_json(
        {
            "status": "connected",
            "subject_prefix": settings.NATS_EVENT_TYPE_SUBJECT_PREFIX,
        }
    )
    await websocket_broadcast.add_connection(websocket)

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            event = _event_payload(message, current_user)
            payload_size = len(message.get("text") or message.get("bytes") or b"")
            log.info(
                "WebSocket scan event received event_id=%s user_id=%s payload_type=%s payload_size=%s summary=%s",
                event["event_id"],
                user_id,
                event["payload_type"],
                payload_size,
                _payload_summary(event.get("payload")),
            )
            try:
                await _publish_event(event)
            except Exception as exc:
                log.exception(
                    "WebSocket event publish failed event_id=%s user_id=%s subject=%s",
                    event["event_id"],
                    user_id,
                    event.get("subject"),
                )
                await websocket.send_json(
                    {
                        "status": "nack",
                        "event_id": event["event_id"],
                        "subject": event.get("subject"),
                        "detail": f"Failed to publish event to NATS: {exc}",
                    }
                )
                continue
            log.info(
                "WebSocket event published event_id=%s user_id=%s subject=%s",
                event["event_id"],
                user_id,
                event.get("subject"),
            )
            await websocket.send_json(
                {
                    "status": "published",
                    "event_id": event["event_id"],
                    "subject": event.get("subject"),
                }
            )
    except WebSocketDisconnect:
        log.info(
            "WebSocket disconnected user_id=%s client=%s", user_id, websocket.client
        )
        await websocket_broadcast.remove_connection(websocket)
        return
    log.info("WebSocket disconnected user_id=%s client=%s", user_id, websocket.client)
    await websocket_broadcast.remove_connection(websocket)
