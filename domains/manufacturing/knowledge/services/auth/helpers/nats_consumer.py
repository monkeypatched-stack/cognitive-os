import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from services.auth.helpers import influx_store, nats_store, websocket_broadcast
from services.common.data_transformation import (
    RAW_INTEGRATION_SUBJECT,
    transform_and_persist_ingested_event,
)
from services.common.db import get_database
from services.common.event_reducers import reduce_event_to_state
from services.common.event_types import infer_event_type

log = logging.getLogger("uvicorn.error")
APPROVAL_AUDIT_EVENTS_COLLECTION = "approval_audit_events"
_subscriptions = []
_task = None
_stop_event: asyncio.Event | None = None
_last_connect_error: str | None = None
_consumer_bindings: list[dict[str, Any]] = []
_last_subscribed_at: str | None = None
_approval_audit_stats: dict[str, Any] = {
    "received": 0,
    "persisted": 0,
    "indexed": 0,
    "failed": 0,
    "last_received_at": None,
    "last_persisted_at": None,
    "last_error": None,
}


def _binding(name: str, subject: str, queue: str) -> dict[str, str]:
    return {"name": name, "subject": subject, "queue": queue}


def _expected_consumer_bindings() -> list[dict[str, str]]:
    bindings = [
        _binding(
            "legacy_events",
            nats_store.settings.NATS_EVENTS_SUBJECT,
            nats_store.settings.NATS_EVENTS_QUEUE,
        ),
        _binding(
            "sensor_readings",
            nats_store.sensor_subject_wildcard(),
            nats_store.settings.NATS_SENSOR_QUEUE,
        ),
        _binding(
            "approval_requests",
            nats_store.settings.NATS_APPROVALS_SUBJECT,
            nats_store.settings.NATS_APPROVALS_QUEUE,
        ),
        _binding(
            "change_control",
            nats_store.settings.NATS_CHANGE_CONTROL_SUBJECT,
            nats_store.settings.NATS_CHANGE_CONTROL_QUEUE,
        ),
        _binding(
            "approval_decisions",
            nats_store.settings.NATS_APPROVAL_DECISIONS_SUBJECT,
            nats_store.settings.NATS_APPROVAL_DECISIONS_QUEUE,
        ),
        _binding(
            "approval_audit",
            nats_store.settings.NATS_APPROVAL_AUDIT_SUBJECT,
            nats_store.settings.NATS_APPROVAL_AUDIT_QUEUE,
        ),
        _binding(
            "audit_events",
            nats_store.settings.NATS_AUDIT_SUBJECT,
            nats_store.settings.NATS_AUDIT_QUEUE,
        ),
        _binding(
            "cdc",
            nats_store.settings.NATS_CDC_SUBJECT,
            nats_store.settings.NATS_CDC_QUEUE,
        ),
        _binding(
            "integration_transformer",
            RAW_INTEGRATION_SUBJECT,
            "indus-integration-transformer",
        ),
    ]
    bindings.extend(
        _binding(
            f"typed_event:{event_type}",
            nats_store.subject_for_event_type(event_type),
            nats_store.queue_for_event_type(event_type),
        )
        for event_type in nats_store.configured_event_types()
    )
    return bindings


def consumer_status() -> dict[str, Any]:
    audit_binding = next(
        (item for item in _consumer_bindings if item.get("name") == "approval_audit"),
        None,
    )
    regulated_audit_binding = next(
        (item for item in _consumer_bindings if item.get("name") == "audit_events"),
        None,
    )
    return {
        "running": _task is not None and not _task.done(),
        "last_connect_error": _last_connect_error,
        "last_subscribed_at": _last_subscribed_at,
        "subscription_count": len(_subscriptions),
        "bindings": list(_consumer_bindings),
        "approval_audit_consumer": {
            "expected": True,
            "subscribed": audit_binding is not None,
            "subject": nats_store.settings.NATS_APPROVAL_AUDIT_SUBJECT,
            "queue": nats_store.settings.NATS_APPROVAL_AUDIT_QUEUE,
        },
        "audit_consumer": {
            "expected": True,
            "subscribed": regulated_audit_binding is not None,
            "subject": nats_store.settings.NATS_AUDIT_SUBJECT,
            "queue": nats_store.settings.NATS_AUDIT_QUEUE,
        },
        "approval_audit_persistence": dict(_approval_audit_stats),
    }


def _payload_summary(event: dict) -> dict:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return {"payload_kind": type(payload).__name__}
    return {
        "payload_id": payload.get("id"),
        "category": payload.get("category"),
        "scope": payload.get("scope"),
        "type": payload.get("type"),
        "work_order_id": payload.get("work_order_id"),
    }


async def _handle_event(message) -> None:
    try:
        event = json.loads(message.data.decode("utf-8"))
        if not isinstance(event, dict):
            event = {"payload": event, "payload_type": "json"}
    except (UnicodeDecodeError, json.JSONDecodeError):
        event = {
            "payload": message.data.decode("utf-8", errors="replace"),
            "payload_type": "text",
        }

    event.setdefault(
        "event_type",
        (
            message.subject.rsplit(".", 1)[-1]
            if message.subject.startswith(
                f"{nats_store.settings.NATS_EVENT_TYPE_SUBJECT_PREFIX}."
            )
            else infer_event_type(event)
        ),
    )

    log.info(
        "NATS event received subject=%s event_id=%s source=%s payload_type=%s summary=%s",
        message.subject,
        event.get("event_id"),
        event.get("source"),
        event.get("payload_type"),
        _payload_summary(event),
    )
    try:
        await influx_store.write_websocket_event(event, message.subject)
    except Exception:
        log.exception(
            "Failed to write NATS event to InfluxDB event_id=%s summary=%s",
            event.get("event_id"),
            _payload_summary(event),
        )
    else:
        log.info(
            "NATS event persisted to InfluxDB event_id=%s summary=%s",
            event.get("event_id"),
            _payload_summary(event),
        )

    await websocket_broadcast.broadcast_event(event)


async def _handle_approval_event(message) -> None:
    try:
        event = json.loads(message.data.decode("utf-8"))
        if not isinstance(event, dict):
            event = {"payload": event, "payload_type": "json"}
    except (UnicodeDecodeError, json.JSONDecodeError):
        event = {
            "payload": message.data.decode("utf-8", errors="replace"),
            "payload_type": "text",
        }
    event.setdefault("event_type", "approval-request")
    event.setdefault("source", "approval-nats-queue")
    event["subject"] = message.subject
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    log.info(
        "Approval NATS event received subject=%s source_id=%s approvals=%s",
        message.subject,
        event.get("source_id") or source.get("source_id"),
        len(event.get("notifications") or []),
    )
    try:
        await influx_store.write_websocket_event(event, message.subject)
    except Exception:
        log.exception("Failed to write approval NATS event to InfluxDB")
    await websocket_broadcast.broadcast_event(event)


async def _handle_change_control_event(message) -> None:
    event = await _decode_queue_event(
        message,
        default_event_type="change-control",
        default_source="change-control-nats-queue",
    )
    log.info(
        "Change-control NATS event received subject=%s source_id=%s approvals=%s",
        message.subject,
        event.get("source_id"),
        len(event.get("approval_chain") or []),
    )
    await _persist_and_broadcast_queue_event(event, message.subject)


async def _handle_approval_decision_event(message) -> None:
    event = await _decode_queue_event(
        message,
        default_event_type="approval-decision",
        default_source="approval-decision-nats-queue",
    )
    log.info(
        "Approval decision NATS event received subject=%s approval_id=%s decision=%s",
        message.subject,
        event.get("approval_id"),
        event.get("decision"),
    )
    await _persist_and_broadcast_queue_event(event, message.subject)
    try:
        await nats_store.publish_event(
            json.dumps(event, default=str).encode("utf-8"),
            subject=nats_store.settings.NATS_CANVAS_WEBHOOK_INCOMING_SUBJECT,
        )
    except Exception:
        log.exception(
            "Failed to publish approval decision to canvas webhook NATS subject"
        )


async def _handle_approval_audit_event(message) -> None:
    event = await _decode_queue_event(
        message,
        default_event_type="approval-audit",
        default_source="approval-audit-nats-queue",
    )
    _mark_approval_audit_received()
    log.info(
        "Approval audit NATS event received subject=%s approval_id=%s decision=%s",
        message.subject,
        event.get("approval_id"),
        event.get("decision"),
    )
    await _persist_received_approval_audit_event(event, message.subject)
    await _persist_and_broadcast_queue_event(event, message.subject)


async def _handle_audit_event(message) -> None:
    event = await _decode_queue_event(
        message, default_event_type="audit-event", default_source="audit-nats-queue"
    )
    log.info(
        "Audit NATS event received subject=%s audit_id=%s record_id=%s",
        message.subject,
        event.get("audit_id"),
        event.get("record_id") or event.get("source_id"),
    )
    await _persist_and_broadcast_queue_event(event, message.subject)


async def _handle_cdc_event(message) -> None:
    event = await _decode_queue_event(
        message, default_event_type="cdc-change", default_source="mongodb-cdc"
    )
    log.info(
        "CDC NATS event received subject=%s collection=%s operation=%s record_id=%s",
        message.subject,
        event.get("collection"),
        event.get("operation"),
        event.get("record_id"),
    )
    await _persist_and_broadcast_queue_event(event, message.subject)


async def _handle_integration_raw_event(message) -> None:
    event = await _decode_queue_event(
        message,
        default_event_type="integration-raw",
        default_source="integration-adapter",
    )
    log.info(
        "Integration raw NATS event received subject=%s adapter_id=%s source_object=%s",
        message.subject,
        event.get("adapter_id"),
        event.get("source_object"),
    )
    try:
        transformed = await transform_and_persist_ingested_event(_raw_db(), event)
    except Exception:
        log.exception(
            "Failed to transform integration event subject=%s adapter_id=%s source_object=%s",
            message.subject,
            event.get("adapter_id"),
            event.get("source_object"),
        )
        return
    await _persist_and_broadcast_queue_event(
        transformed, transformed.get("subject") or message.subject
    )


async def _decode_queue_event(
    message, *, default_event_type: str, default_source: str
) -> dict:
    try:
        event = json.loads(message.data.decode("utf-8"))
        if not isinstance(event, dict):
            event = {"payload": event, "payload_type": "json"}
    except (UnicodeDecodeError, json.JSONDecodeError):
        event = {
            "payload": message.data.decode("utf-8", errors="replace"),
            "payload_type": "text",
        }
    event.setdefault("event_type", default_event_type)
    event.setdefault("source", default_source)
    event["subject"] = message.subject
    return event


async def _persist_and_broadcast_queue_event(event: dict, subject: str) -> None:
    try:
        await reduce_event_to_state(_raw_db(), event, subject=subject)
    except Exception:
        log.exception(
            "Failed to reduce queued event to canonical state subject=%s event_id=%s",
            subject,
            event.get("event_id"),
        )
    try:
        await influx_store.write_websocket_event(event, subject)
    except Exception:
        log.exception("Failed to write queued event to InfluxDB subject=%s", subject)
    await websocket_broadcast.broadcast_event(event)


def _mark_approval_audit_received() -> None:
    _approval_audit_stats["received"] += 1
    _approval_audit_stats["last_received_at"] = datetime.now(timezone.utc).isoformat()


def _raw_db():
    db = get_database()
    return getattr(db, "raw_database", getattr(db, "_db", db))


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _approval_audit_event_id(event: dict, subject: str) -> str:
    if event.get("approval_audit_event_id"):
        return str(event["approval_audit_event_id"])
    if event.get("event_id"):
        return f"approval-audit-{event['event_id']}"
    identity = {
        "subject": subject,
        "approval_id": event.get("approval_id"),
        "source_id": event.get("source_id"),
        "decision": event.get("decision"),
        "decided_at": event.get("decided_at"),
        "signature_meaning": event.get("signature_meaning"),
        "decision_source": event.get("decision_source"),
    }
    digest = hashlib.sha256(
        json.dumps(_jsonable(identity), sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"approval-audit-{digest[:32]}"


def _approval_audit_search_text(record: dict) -> str:
    event = record.get("event") if isinstance(record.get("event"), dict) else {}
    parts = [
        record.get("approval_audit_event_id"),
        record.get("subject"),
        record.get("event_type"),
        record.get("approval_id"),
        record.get("source_id"),
        record.get("source_collection"),
        record.get("decision"),
        record.get("assigned_user_id"),
        record.get("assigned_user_name"),
        record.get("assigned_user_email"),
        event.get("source_title"),
        event.get("reason"),
        event.get("signature_meaning"),
    ]
    return "\n".join(str(part) for part in parts if part)


def _approval_audit_elasticsearch_document(record: dict) -> dict:
    event = record.get("event") if isinstance(record.get("event"), dict) else {}
    return {
        "audit_id": record.get("approval_audit_event_id"),
        "approval_audit_event_id": record.get("approval_audit_event_id"),
        "timestamp": _jsonable(record.get("received_at")),
        "received_at": _jsonable(record.get("received_at")),
        "collection": APPROVAL_AUDIT_EVENTS_COLLECTION,
        "record_id": record.get("approval_id"),
        "action": "approval_audit_queue_received",
        "subject": record.get("subject"),
        "event": record.get("event_name"),
        "event_type": record.get("event_type"),
        "approval_id": record.get("approval_id"),
        "source_collection": record.get("source_collection"),
        "source_id": record.get("source_id"),
        "source_title": event.get("source_title"),
        "decision": record.get("decision"),
        "decision_source": event.get("decision_source"),
        "assigned_user_id": record.get("assigned_user_id"),
        "assigned_user_name": record.get("assigned_user_name"),
        "assigned_user_email": record.get("assigned_user_email"),
        "requires_electronic_signature": event.get("requires_electronic_signature"),
        "target_node": event.get("target_node"),
        "signature_meaning": event.get("signature_meaning"),
        "reason": event.get("reason"),
        "payload": _jsonable(event),
        "search_text": _approval_audit_search_text(record),
    }


def _elasticsearch_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if nats_store.settings.AUDIT_ELASTICSEARCH_API_KEY:
        headers["Authorization"] = (
            f"ApiKey {nats_store.settings.AUDIT_ELASTICSEARCH_API_KEY}"
        )
    return headers


def _elasticsearch_auth():
    if nats_store.settings.AUDIT_ELASTICSEARCH_USERNAME:
        return (
            nats_store.settings.AUDIT_ELASTICSEARCH_USERNAME,
            nats_store.settings.AUDIT_ELASTICSEARCH_PASSWORD,
        )
    return None


async def _index_approval_audit_event(record: dict) -> dict:
    if not nats_store.settings.AUDIT_ELASTICSEARCH_ENABLED:
        return {"enabled": False, "indexed": False}
    document = _approval_audit_elasticsearch_document(record)
    document_id = str(record["approval_audit_event_id"])
    async with httpx.AsyncClient(
        base_url=nats_store.settings.AUDIT_ELASTICSEARCH_URL.rstrip("/"),
        timeout=10.0,
        auth=_elasticsearch_auth(),
        headers=_elasticsearch_headers(),
    ) as client:
        response = await client.put(
            f"/{nats_store.settings.AUDIT_ELASTICSEARCH_INDEX}/_doc/{document_id}",
            json=document,
        )
        response.raise_for_status()
        payload = response.json()
    return {
        "enabled": True,
        "indexed": True,
        "index": nats_store.settings.AUDIT_ELASTICSEARCH_INDEX,
        "document_id": document_id,
        "result": payload.get("result"),
    }


async def _persist_received_approval_audit_event(
    event: dict, subject: str
) -> dict | None:
    received_at = datetime.now(timezone.utc)
    event_id = _approval_audit_event_id(event, subject)
    record = {
        "approval_audit_event_id": event_id,
        "received_at": received_at,
        "last_received_at": received_at,
        "subject": subject,
        "source": event.get("source") or "approval-audit-nats-queue",
        "event_name": event.get("event"),
        "event_type": event.get("event_type") or "approval-audit",
        "approval_id": event.get("approval_id"),
        "source_collection": event.get("source_collection"),
        "source_id": event.get("source_id"),
        "decision": event.get("decision"),
        "assigned_user_id": event.get("assigned_user_id"),
        "assigned_user_name": event.get("assigned_user_name"),
        "assigned_user_email": event.get("assigned_user_email"),
        "event": _jsonable(event),
    }
    raw_db = None
    try:
        raw_db = _raw_db()
        insert_record = dict(record)
        insert_record.pop("last_received_at", None)
        await raw_db[APPROVAL_AUDIT_EVENTS_COLLECTION].update_one(
            {"approval_audit_event_id": event_id},
            {
                "$setOnInsert": {
                    **insert_record,
                    "created_at": received_at,
                },
                "$set": {
                    "last_received_at": received_at,
                    "elasticsearch": {
                        "enabled": bool(
                            nats_store.settings.AUDIT_ELASTICSEARCH_ENABLED
                        ),
                        "indexed": False,
                        "pending": True,
                        "index": nats_store.settings.AUDIT_ELASTICSEARCH_INDEX,
                    },
                },
                "$inc": {"receipt_count": 1},
            },
            upsert=True,
        )
        _approval_audit_stats["persisted"] += 1
        _approval_audit_stats["last_persisted_at"] = datetime.now(
            timezone.utc
        ).isoformat()
    except Exception as exc:
        _approval_audit_stats["failed"] += 1
        _approval_audit_stats["last_error"] = f"mongo_persistence_failed: {exc}"
        log.exception(
            "Failed to persist approval audit queue event in Mongo approval_id=%s subject=%s",
            event.get("approval_id"),
            subject,
        )
        return None

    elasticsearch_status: dict
    try:
        elasticsearch_status = await _index_approval_audit_event(record)
        if elasticsearch_status.get("indexed"):
            _approval_audit_stats["indexed"] += 1
    except Exception as exc:
        _approval_audit_stats["failed"] += 1
        _approval_audit_stats["last_error"] = str(exc)
        log.exception(
            "Failed to index approval audit queue event in Elasticsearch approval_id=%s subject=%s",
            event.get("approval_id"),
            subject,
        )
        elasticsearch_status = {
            "enabled": bool(nats_store.settings.AUDIT_ELASTICSEARCH_ENABLED),
            "indexed": False,
            "pending": False,
            "index": nats_store.settings.AUDIT_ELASTICSEARCH_INDEX,
            "error": str(exc),
        }
    try:
        await raw_db[APPROVAL_AUDIT_EVENTS_COLLECTION].update_one(
            {"approval_audit_event_id": event_id},
            {"$set": {"elasticsearch": elasticsearch_status}},
        )
    except Exception:
        log.exception(
            "Failed to update approval audit Elasticsearch status in Mongo approval_id=%s subject=%s",
            event.get("approval_id"),
            subject,
        )
    return {**record, "elasticsearch": elasticsearch_status}


async def _handle_sensor_reading(message) -> None:
    try:
        reading = json.loads(message.data.decode("utf-8"))
        if not isinstance(reading, dict):
            reading = {"value": reading}
    except (UnicodeDecodeError, json.JSONDecodeError):
        reading = {"raw_value": message.data.decode("utf-8", errors="replace")}

    reading.setdefault("sensor_id", message.subject.rsplit(".", 1)[-1])
    reading.setdefault("source", "mqtt")
    log.info(
        "NATS sensor reading received subject=%s sensor_id=%s value=%s",
        message.subject,
        reading.get("sensor_id"),
        reading.get("value", reading.get("raw_value")),
    )
    try:
        await influx_store.write_sensor_reading(reading, message.subject)
    except Exception:
        log.exception(
            "Failed to write NATS sensor reading to InfluxDB subject=%s sensor_id=%s",
            message.subject,
            reading.get("sensor_id"),
        )


async def _run_consumer() -> None:
    global _last_connect_error, _subscriptions, _consumer_bindings, _last_subscribed_at
    if _stop_event is None:
        return

    while not _stop_event.is_set():
        try:
            _subscriptions = [await nats_store.subscribe_events(_handle_event)]
            _subscriptions.append(
                await nats_store.subscribe_sensor_readings(_handle_sensor_reading)
            )
            _subscriptions.append(
                await nats_store.subscribe_approval_events(_handle_approval_event)
            )
            _subscriptions.append(
                await nats_store.subscribe_change_control_events(
                    _handle_change_control_event
                )
            )
            _subscriptions.append(
                await nats_store.subscribe_approval_decision_events(
                    _handle_approval_decision_event
                )
            )
            _subscriptions.append(
                await nats_store.subscribe_approval_audit_events(
                    _handle_approval_audit_event
                )
            )
            _subscriptions.append(
                await nats_store.subscribe_audit_events(_handle_audit_event)
            )
            _subscriptions.append(
                await nats_store.subscribe_cdc_events(_handle_cdc_event)
            )
            _subscriptions.append(
                await nats_store.subscribe_integration_raw_events(
                    _handle_integration_raw_event
                )
            )
            for event_type in nats_store.configured_event_types():
                _subscriptions.append(
                    await nats_store.subscribe_events(
                        _handle_event,
                        subject=nats_store.subject_for_event_type(event_type),
                        queue=nats_store.queue_for_event_type(event_type),
                    )
                )
            _consumer_bindings = _expected_consumer_bindings()
            _last_subscribed_at = datetime.now(timezone.utc).isoformat()
            log.info(
                "NATS event consumers subscribed legacy_subject=%s legacy_queue=%s typed_event_types=%s",
                nats_store.settings.NATS_EVENTS_SUBJECT,
                nats_store.settings.NATS_EVENTS_QUEUE,
                nats_store.configured_event_types(),
            )
            log.info(
                "NATS approval consumer subscribed subject=%s queue=%s",
                nats_store.settings.NATS_APPROVALS_SUBJECT,
                nats_store.settings.NATS_APPROVALS_QUEUE,
            )
            log.info(
                "NATS compliance queue consumers subscribed change_control=%s approval_decisions=%s approval_audit=%s audit=%s",
                nats_store.settings.NATS_CHANGE_CONTROL_SUBJECT,
                nats_store.settings.NATS_APPROVAL_DECISIONS_SUBJECT,
                nats_store.settings.NATS_APPROVAL_AUDIT_SUBJECT,
                nats_store.settings.NATS_AUDIT_SUBJECT,
            )
            log.info(
                "NATS CDC consumer subscribed subject=%s queue=%s",
                nats_store.settings.NATS_CDC_SUBJECT,
                nats_store.settings.NATS_CDC_QUEUE,
            )
            if _last_connect_error is not None:
                log.info("Connected NATS event consumers")
                _last_connect_error = None
            await _stop_event.wait()
        except Exception:
            message = "Failed to subscribe to NATS events; retrying"
            if message != _last_connect_error:
                log.warning(message)
                _last_connect_error = message
            try:
                await asyncio.wait_for(_stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
        finally:
            for subscription in _subscriptions:
                try:
                    await subscription.unsubscribe()
                except Exception:
                    log.exception("Failed to unsubscribe from NATS events")
            _subscriptions = []
            _consumer_bindings = []


async def start_influx_event_consumer() -> None:
    global _task, _stop_event
    if _task is not None and not _task.done():
        return
    _stop_event = asyncio.Event()
    _task = asyncio.create_task(_run_consumer())


async def stop_influx_event_consumer() -> None:
    global _task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _task is not None:
        await _task
        _task = None
    _stop_event = None
    await influx_store.close_influx_client()
