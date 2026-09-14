from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.compliance import sha256_hex

REDUCED_STATE_COLLECTION = "event_reduced_state"
REDUCER_AUDIT_COLLECTION = "event_reducer_audits"
CANONICAL_SNAPSHOT_COLLECTION = "canonical_state_snapshots"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _compact_subject(subject: str | None) -> str:
    return str(subject or "unknown").strip() or "unknown"


def _event_hash(event: dict[str, Any], subject: str | None) -> str:
    stable_event = {
        key: value
        for key, value in event.items()
        if key not in {"reducer", "broadcast", "websocket_delivery"}
    }
    return sha256_hex({"subject": subject, "event": stable_event})


def _event_id(event: dict[str, Any], subject: str | None) -> str:
    return str(
        event.get("event_id")
        or event.get("id")
        or f"event-{_event_hash(event, subject)[:24]}"
    )


def _event_time(event: dict[str, Any]) -> Any:
    return (
        event.get("occurred_at")
        or event.get("created_at")
        or event.get("updated_at")
        or event.get("timestamp")
        or _now()
    )


def _aggregate_parts(
    event: dict[str, Any], subject: str | None
) -> tuple[str, str, str]:
    event_type = str(
        event.get("event_type") or _compact_subject(subject).rsplit(".", 1)[-1]
    )
    collection = str(
        event.get("target_collection")
        or event.get("collection")
        or event.get("source_collection")
        or ""
    )
    record_id = str(
        event.get("target_id")
        or event.get("record_id")
        or event.get("source_id")
        or event.get("approval_id")
        or ""
    )
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if not record_id and isinstance(payload, dict):
        record_id = str(
            payload.get("id")
            or payload.get("work_order_id")
            or payload.get("document_id")
            or payload.get("equipment_id")
            or payload.get("machine_id")
            or payload.get("transaction_id")
            or ""
        )
    if not collection:
        collection = str(
            event.get("target_entity") or event.get("source") or event_type
        )
    if not record_id:
        record_id = _event_id(event, subject)
    return event_type, collection, record_id


def _state_from_event(event: dict[str, Any]) -> tuple[dict[str, Any], str]:
    operation = str(event.get("operation") or "").lower()
    if event.get("event_type") == "cdc-change":
        if operation == "delete":
            before = (
                event.get("before") if isinstance(event.get("before"), dict) else {}
            )
            return {**before, "reducer_record_status": "deleted"}, "delete"
        after = event.get("after") if isinstance(event.get("after"), dict) else {}
        if after:
            return after, operation or "upsert"
    if isinstance(event.get("payload"), dict):
        return dict(event["payload"]), operation or "upsert"
    return {
        key: value
        for key, value in event.items()
        if key not in {"before", "after", "filter", "update", "result"}
    }, operation or "event"


def canonical_snapshot_for_state(
    *,
    aggregate_key: str,
    state: dict[str, Any],
    source_event_hash: str,
    event_type: str,
    subject: str | None,
) -> dict[str, Any]:
    snapshot = {
        "snapshot_id": f"canonical-snapshot-{uuid4().hex}",
        "aggregate_key": aggregate_key,
        "event_type": event_type,
        "subject": subject,
        "state": state,
        "source_event_hash": source_event_hash,
        "created_at": _now(),
    }
    snapshot["state_hash"] = sha256_hex(state)
    snapshot["snapshot_hash"] = sha256_hex(snapshot)
    return snapshot


async def reduce_event_to_state(
    db: AsyncIOMotorDatabase,
    event: dict[str, Any],
    *,
    subject: str | None = None,
) -> dict[str, Any]:
    event_type, collection, record_id = _aggregate_parts(event, subject)
    aggregate_key = f"{collection}:{record_id}"
    event_hash = _event_hash(event, subject)
    existing_audit = await db[REDUCER_AUDIT_COLLECTION].find_one(
        {"event_hash": event_hash}, {"_id": 0}
    )
    if existing_audit:
        return {**existing_audit, "idempotent_replay": True}

    reduced_state, operation = _state_from_event(event)
    prior = await db[REDUCED_STATE_COLLECTION].find_one(
        {"aggregate_key": aggregate_key}, {"_id": 0}
    )
    previous_state = (
        prior.get("state")
        if isinstance(prior, dict) and isinstance(prior.get("state"), dict)
        else {}
    )
    if operation == "delete":
        state = {**previous_state, **reduced_state}
    else:
        state = {**previous_state, **reduced_state}
        state.pop("_id", None)
        state["reducer_record_status"] = "active"

    state_hash = sha256_hex(state)
    snapshot = canonical_snapshot_for_state(
        aggregate_key=aggregate_key,
        state=state,
        source_event_hash=event_hash,
        event_type=event_type,
        subject=subject,
    )
    await db[CANONICAL_SNAPSHOT_COLLECTION].insert_one(snapshot.copy())

    now = _now()
    state_document = {
        "aggregate_key": aggregate_key,
        "collection": collection,
        "record_id": record_id,
        "event_type": event_type,
        "subject": subject,
        "state": state,
        "state_hash": state_hash,
        "last_event_id": _event_id(event, subject),
        "last_event_hash": event_hash,
        "last_event_at": _event_time(event),
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_hash": snapshot["snapshot_hash"],
        "updated_at": now,
    }
    await db[REDUCED_STATE_COLLECTION].update_one(
        {"aggregate_key": aggregate_key},
        {
            "$set": state_document,
            "$setOnInsert": {"created_at": now},
            "$inc": {"version": 1},
        },
        upsert=True,
    )
    stored = await db[REDUCED_STATE_COLLECTION].find_one(
        {"aggregate_key": aggregate_key}, {"_id": 0}
    )
    audit = {
        "reducer_audit_id": f"event-reducer-audit-{uuid4().hex}",
        "event_id": _event_id(event, subject),
        "event_hash": event_hash,
        "event_type": event_type,
        "subject": subject,
        "aggregate_key": aggregate_key,
        "collection": collection,
        "record_id": record_id,
        "operation": operation,
        "previous_state_hash": (
            prior.get("state_hash") if isinstance(prior, dict) else None
        ),
        "state_hash": state_hash,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_hash": snapshot["snapshot_hash"],
        "reduced_state_version": stored.get("version") if stored else 1,
        "created_at": now,
    }
    audit["audit_hash"] = sha256_hex(audit)
    await db[REDUCER_AUDIT_COLLECTION].insert_one(audit)
    return {**audit, "idempotent_replay": False}
