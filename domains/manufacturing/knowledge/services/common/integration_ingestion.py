from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.auth.helpers import nats_store
from services.common.data_transformation import RAW_INTEGRATION_SUBJECT
from services.common.ontology_registry import load_integration_adapter

EXTERNAL_INGESTION_AUDIT_COLLECTION = "external_ingestion_audits"
IngestionMode = Literal["spark_mini_batch", "kafka_realtime", "flink_realtime", "batch"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_object_names(adapter: dict[str, Any]) -> set[str]:
    return {
        str(mapping.get("source_object"))
        for mapping in adapter.get("mappings") or []
        if mapping.get("source_object")
    }


def validate_ingestion_mapping(adapter_id: str, source_object: str) -> dict[str, Any]:
    try:
        adapter = load_integration_adapter(adapter_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown integration adapter '{adapter_id}'.",
        ) from exc
    source_objects = _source_object_names(adapter)
    if source_object not in source_objects:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Adapter '{adapter_id}' does not define a direct transformation for source object '{source_object}'.",
        )
    return adapter


def _normalize_records(records: Any) -> list[dict[str, Any]]:
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list) or not records:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Ingestion payload requires at least one record.",
        )
    normalized: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Record at index {index} must be a JSON object.",
            )
        normalized.append(record)
    return normalized


def _ingestion_source(
    mode: IngestionMode, source: dict[str, Any] | None
) -> dict[str, Any]:
    details = dict(source or {})
    details.setdefault("mode", mode)
    if mode == "spark_mini_batch":
        details.setdefault("engine", "spark")
        details.setdefault("cadence", "mini_batch")
    elif mode == "kafka_realtime":
        details.setdefault("engine", "kafka")
        details.setdefault("cadence", "real_time")
    elif mode == "flink_realtime":
        details.setdefault("engine", "flink")
        details.setdefault("cadence", "real_time")
    else:
        details.setdefault("engine", "batch")
        details.setdefault("cadence", "batch")
    return details


def build_raw_integration_event(
    *,
    adapter_id: str,
    source_object: str,
    payload: dict[str, Any],
    ingestion_mode: IngestionMode,
    ingestion_batch_id: str,
    record_index: int,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_id": f"external-ingest-{uuid4().hex[:12]}",
        "event_type": "integration-raw",
        "source": "external-data-ingestion",
        "adapter_id": adapter_id,
        "source_object": source_object,
        "ingestion_mode": ingestion_mode,
        "ingestion_batch_id": ingestion_batch_id,
        "record_index": record_index,
        "payload_hash": _payload_hash(payload),
        "payload": payload,
        "upstream": _ingestion_source(ingestion_mode, source),
        "received_at": _now(),
    }


async def publish_external_ingestion_records(
    db: AsyncIOMotorDatabase,
    *,
    adapter_id: str,
    source_object: str,
    records: Any,
    ingestion_mode: IngestionMode,
    source: dict[str, Any] | None = None,
    requested_by: str | None = None,
) -> dict[str, Any]:
    validate_ingestion_mapping(adapter_id, source_object)
    normalized_records = _normalize_records(records)
    ingestion_batch_id = f"ing_{uuid4().hex[:16]}"
    events = [
        build_raw_integration_event(
            adapter_id=adapter_id,
            source_object=source_object,
            payload=record,
            ingestion_mode=ingestion_mode,
            ingestion_batch_id=ingestion_batch_id,
            record_index=index,
            source=source,
        )
        for index, record in enumerate(normalized_records)
    ]

    published = 0
    errors: list[dict[str, Any]] = []
    for event in events:
        try:
            await nats_store.publish_event(
                nats_store._event_bytes(event), subject=RAW_INTEGRATION_SUBJECT
            )
            published += 1
        except Exception as exc:
            errors.append(
                {
                    "event_id": event["event_id"],
                    "record_index": event["record_index"],
                    "error": str(exc),
                }
            )

    audit = {
        "ingestion_batch_id": ingestion_batch_id,
        "event_type": "external-ingestion-queued",
        "adapter_id": adapter_id,
        "source_object": source_object,
        "ingestion_mode": ingestion_mode,
        "record_count": len(events),
        "published_count": published,
        "failed_count": len(errors),
        "subject": RAW_INTEGRATION_SUBJECT,
        "source": _ingestion_source(ingestion_mode, source),
        "requested_by": requested_by,
        "event_ids": [event["event_id"] for event in events],
        "errors": errors,
        "created_at": _now(),
    }
    await db[EXTERNAL_INGESTION_AUDIT_COLLECTION].insert_one(audit)
    return {
        "queued": published == len(events),
        "subject": RAW_INTEGRATION_SUBJECT,
        "ingestion_batch_id": ingestion_batch_id,
        "adapter_id": adapter_id,
        "source_object": source_object,
        "ingestion_mode": ingestion_mode,
        "record_count": len(events),
        "published_count": published,
        "failed_count": len(errors),
        "event_ids": audit["event_ids"],
        "errors": errors,
    }
