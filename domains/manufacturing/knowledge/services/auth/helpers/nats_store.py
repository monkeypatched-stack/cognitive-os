from importlib import import_module

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder

from services.common.config import settings
from services.common.event_types import infer_event_type, slugify_event_type

_client = None
_publish_client = None
_subscribe_client = None


async def _ignore_nats_error(_error: Exception) -> None:
    return None


async def _connect_client():
    try:
        nats = import_module("nats")
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NATS client is not installed. Install project requirements.",
        ) from exc
    return await nats.connect(
        settings.NATS_URL,
        allow_reconnect=False,
        connect_timeout=1,
        error_cb=_ignore_nats_error,
    )


async def _get_publish_client():
    global _client, _publish_client
    if _publish_client is None or _publish_client.is_closed:
        _publish_client = await _connect_client()
        _client = _publish_client
    return _publish_client


async def _get_subscribe_client():
    global _subscribe_client
    if _subscribe_client is None or _subscribe_client.is_closed:
        _subscribe_client = await _connect_client()
    return _subscribe_client


async def _get_client():
    global _client
    if _client is None or _client.is_closed:
        try:
            _client = await _get_publish_client()
        except ImportError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="NATS client is not installed. Install project requirements.",
            ) from exc
    return _client


async def publish_event(payload: bytes, subject: str | None = None) -> None:
    client = await _get_publish_client()
    await client.publish(subject or settings.NATS_EVENTS_SUBJECT, payload)
    await client.flush()


def _event_bytes(event: dict) -> bytes:
    return __import__("json").dumps(jsonable_encoder(event)).encode("utf-8")


def subject_for_event_type(event_type: str) -> str:
    return f"{settings.NATS_EVENT_TYPE_SUBJECT_PREFIX}.{slugify_event_type(event_type)}"


def queue_for_event_type(event_type: str) -> str:
    return f"{settings.NATS_EVENT_TYPE_QUEUE_PREFIX}-{slugify_event_type(event_type)}"


def event_type_subject_wildcard() -> str:
    return f"{settings.NATS_EVENT_TYPE_SUBJECT_PREFIX}.*"


def sensor_subject(sensor_id: str) -> str:
    token = str(sensor_id or "unknown").strip().replace(".", "-").replace(" ", "-")
    return f"{settings.NATS_SENSOR_SUBJECT_PREFIX}.{token or 'unknown'}"


def sensor_subject_wildcard() -> str:
    return f"{settings.NATS_SENSOR_SUBJECT_PREFIX}.*"


def configured_event_types() -> list[str]:
    return [
        slugify_event_type(event_type)
        for event_type in str(settings.INFLUXDB_EVENT_TYPE_MEASUREMENTS or "").split(
            ","
        )
        if event_type.strip()
    ]


async def publish_typed_event(event: dict, event_type: str | None = None) -> str:
    resolved_event_type = slugify_event_type(event_type or infer_event_type(event))
    subject = subject_for_event_type(resolved_event_type)
    event["event_type"] = resolved_event_type
    event["subject"] = subject
    await publish_event(_event_bytes(event), subject=subject)
    return subject


async def publish_approval_event(event: dict) -> str:
    event["event_type"] = str(event.get("event_type") or "approval-request")
    event["subject"] = settings.NATS_APPROVALS_SUBJECT
    await publish_event(_event_bytes(event), subject=settings.NATS_APPROVALS_SUBJECT)
    return settings.NATS_APPROVALS_SUBJECT


async def publish_change_control_event(event: dict) -> str:
    event["event_type"] = str(event.get("event_type") or "change-control")
    event["subject"] = settings.NATS_CHANGE_CONTROL_SUBJECT
    await publish_event(
        _event_bytes(event), subject=settings.NATS_CHANGE_CONTROL_SUBJECT
    )
    return settings.NATS_CHANGE_CONTROL_SUBJECT


async def publish_approval_decision_event(event: dict) -> str:
    event["event_type"] = str(event.get("event_type") or "approval-decision")
    event["subject"] = settings.NATS_APPROVAL_DECISIONS_SUBJECT
    await publish_event(
        _event_bytes(event), subject=settings.NATS_APPROVAL_DECISIONS_SUBJECT
    )
    return settings.NATS_APPROVAL_DECISIONS_SUBJECT


async def publish_approval_audit_event(event: dict) -> str:
    event["event_type"] = str(event.get("event_type") or "approval-audit")
    event["subject"] = settings.NATS_APPROVAL_AUDIT_SUBJECT
    await publish_event(
        _event_bytes(event), subject=settings.NATS_APPROVAL_AUDIT_SUBJECT
    )
    return settings.NATS_APPROVAL_AUDIT_SUBJECT


async def publish_audit_event(event: dict) -> str:
    event["event_type"] = str(event.get("event_type") or "audit-event")
    event["subject"] = settings.NATS_AUDIT_SUBJECT
    await publish_event(_event_bytes(event), subject=settings.NATS_AUDIT_SUBJECT)
    return settings.NATS_AUDIT_SUBJECT


async def subscribe_events(
    callback, subject: str | None = None, queue: str | None = None
):
    client = await _get_subscribe_client()
    return await client.subscribe(
        subject or settings.NATS_EVENTS_SUBJECT,
        queue=queue or settings.NATS_EVENTS_QUEUE,
        cb=callback,
    )


async def subscribe_sensor_readings(callback):
    return await subscribe_events(
        callback,
        subject=sensor_subject_wildcard(),
        queue=settings.NATS_SENSOR_QUEUE,
    )


async def subscribe_canvas_webhook_incoming(callback):
    return await subscribe_events(
        callback,
        subject=settings.NATS_CANVAS_WEBHOOK_INCOMING_SUBJECT,
        queue=settings.NATS_CANVAS_WEBHOOK_INCOMING_QUEUE,
    )


async def subscribe_canvas_webhook_outgoing(callback):
    return await subscribe_events(
        callback,
        subject=settings.NATS_CANVAS_WEBHOOK_OUTGOING_SUBJECT,
        queue=settings.NATS_CANVAS_WEBHOOK_OUTGOING_QUEUE,
    )


async def subscribe_approval_events(callback):
    return await subscribe_events(
        callback,
        subject=settings.NATS_APPROVALS_SUBJECT,
        queue=settings.NATS_APPROVALS_QUEUE,
    )


async def subscribe_change_control_events(callback):
    return await subscribe_events(
        callback,
        subject=settings.NATS_CHANGE_CONTROL_SUBJECT,
        queue=settings.NATS_CHANGE_CONTROL_QUEUE,
    )


async def subscribe_approval_decision_events(callback):
    return await subscribe_events(
        callback,
        subject=settings.NATS_APPROVAL_DECISIONS_SUBJECT,
        queue=settings.NATS_APPROVAL_DECISIONS_QUEUE,
    )


async def subscribe_approval_audit_events(callback):
    return await subscribe_events(
        callback,
        subject=settings.NATS_APPROVAL_AUDIT_SUBJECT,
        queue=settings.NATS_APPROVAL_AUDIT_QUEUE,
    )


async def subscribe_audit_events(callback):
    return await subscribe_events(
        callback,
        subject=settings.NATS_AUDIT_SUBJECT,
        queue=settings.NATS_AUDIT_QUEUE,
    )


async def subscribe_cdc_events(callback):
    return await subscribe_events(
        callback,
        subject=settings.NATS_CDC_SUBJECT,
        queue=settings.NATS_CDC_QUEUE,
    )


async def subscribe_integration_raw_events(callback):
    return await subscribe_events(
        callback,
        subject=settings.NATS_INTEGRATIONS_RAW_SUBJECT,
        queue=settings.NATS_INTEGRATIONS_RAW_QUEUE,
    )


async def close_nats_client() -> None:
    global _client, _publish_client, _subscribe_client
    for client in (_publish_client, _subscribe_client):
        if client is not None and not client.is_closed:
            await client.drain()
    _client = None
    _publish_client = None
    _subscribe_client = None
