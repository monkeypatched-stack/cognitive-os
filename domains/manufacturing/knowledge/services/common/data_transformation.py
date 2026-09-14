from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.auth.helpers import nats_store
from services.common.embeddings import build_embedding
from services.common.event_reducers import reduce_event_to_state
from services.common.module_control import require_module_control_allowed
from services.common.neo4j_mirror import mirror_document, safe_mirror
from services.common.ontology_registry import (
    load_integration_adapter,
    load_ontology_registry,
)
from services.common.config import settings

TRANSFORMATION_AUDIT_COLLECTION = "integration_transformation_audits"
RAW_INTEGRATION_SUBJECT = settings.NATS_INTEGRATIONS_RAW_SUBJECT
TRANSFORMED_INTEGRATION_SUBJECT = settings.NATS_INTEGRATIONS_TRANSFORMED_SUBJECT


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _value_at_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in str(path).split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _coerce_mapping_value(
    raw_payload: dict[str, Any], source: Any, separator: str = "-"
) -> Any:
    if isinstance(source, list):
        values = [_value_at_path(raw_payload, item) for item in source]
        return separator.join(str(value) for value in values if value not in (None, ""))
    if isinstance(source, str):
        return _value_at_path(raw_payload, source)
    return source


def _source_paths(source: Any) -> set[str]:
    if isinstance(source, list):
        return {str(item).split(".", 1)[0] for item in source}
    if isinstance(source, str):
        return {source.split(".", 1)[0]}
    return set()


def _mapped_source_roots(mapping: dict[str, Any]) -> set[str]:
    roots: set[str] = set()
    for source in (mapping.get("fields") or {}).values():
        roots.update(_source_paths(source))
    id_mapping = mapping.get("id") or {}
    roots.update(_source_paths(id_mapping.get("source_fields") or []))
    return roots


def _mapping_for_source(adapter: dict[str, Any], source_object: str) -> dict[str, Any]:
    for mapping in adapter.get("mappings") or []:
        if mapping.get("source_object") == source_object:
            return mapping
    raise ValueError(
        f"Adapter {adapter.get('adapter_id')} does not define source object {source_object}."
    )


def _payload_hash(raw_payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        raw_payload, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def transform_ingested_payload(
    adapter_id: str, source_object: str, raw_payload: dict[str, Any]
) -> dict[str, Any]:
    adapter = load_integration_adapter(adapter_id)
    mapping = _mapping_for_source(adapter, source_object)
    ontology = load_ontology_registry()
    if mapping.get("target_entity") not in (ontology.get("entity_types") or {}):
        raise ValueError(
            f"Mapping target entity is not in ontology registry: {mapping.get('target_entity')}"
        )

    id_mapping = mapping.get("id") or {}
    separator = str(id_mapping.get("separator") or "-")
    target_document: dict[str, Any] = {}
    for target_field, value in (mapping.get("defaults") or {}).items():
        if value is not None:
            target_document[target_field] = value
    for target_field, source in (mapping.get("fields") or {}).items():
        value = _coerce_mapping_value(raw_payload, source, separator=separator)
        if value is not None:
            target_document[target_field] = value

    unmapped = {
        key: value
        for key, value in raw_payload.items()
        if key not in _mapped_source_roots(mapping)
        and not str(key).startswith("$")
        and "." not in str(key)
    }
    if unmapped:
        target_document["source_unmapped_fields"] = unmapped

    target_id_field = id_mapping.get("target_field")
    if target_id_field and not target_document.get(target_id_field):
        target_document[target_id_field] = _coerce_mapping_value(
            raw_payload,
            id_mapping.get("source_fields") or [],
            separator=separator,
        )
    if target_id_field and target_document.get(target_id_field):
        target_document.setdefault("id", target_document[target_id_field])

    target_document.update(
        {
            "source_adapter_id": adapter_id,
            "source_object": source_object,
            "source_system": adapter.get("vendor"),
            "source_payload_hash": _payload_hash(raw_payload),
            "updated_at": _now(),
        }
    )
    return {
        "adapter_id": adapter_id,
        "source_object": source_object,
        "target_entity": mapping["target_entity"],
        "target_collection": mapping["target_collection"],
        "target_id_field": target_id_field,
        "document": target_document,
    }


async def transform_and_persist_ingested_event(
    db: AsyncIOMotorDatabase, event: dict[str, Any]
) -> dict[str, Any]:
    adapter_id = str(event.get("adapter_id") or "")
    source_object = str(event.get("source_object") or "")
    raw_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if not adapter_id or not source_object:
        raise ValueError(
            "Ingested integration event requires adapter_id and source_object."
        )

    await require_module_control_allowed(
        db, module_id="data_layer", integration_id=adapter_id
    )
    transformed = transform_ingested_payload(adapter_id, source_object, raw_payload)
    document = transformed["document"]
    collection = transformed["target_collection"]
    target_id_field = transformed["target_id_field"] or "id"
    target_id = document.get(target_id_field) or document.get("id")
    if not target_id:
        raise ValueError("Transformed document did not produce a target id.")

    existing = await db[collection].find_one({target_id_field: target_id})
    document["created_at"] = existing.get("created_at") if existing else _now()
    document["embedding"] = build_embedding(collection, document)
    await db[collection].update_one(
        {target_id_field: target_id}, {"$set": document}, upsert=True
    )
    await safe_mirror(
        mirror_document(collection, document, "update" if existing else "create")
    )

    audit = {
        "transformation_id": f"xfm_{uuid4().hex[:12]}",
        "adapter_id": adapter_id,
        "source_object": source_object,
        "target_collection": collection,
        "target_entity": transformed["target_entity"],
        "target_id": str(target_id),
        "source_event_id": event.get("event_id"),
        "created_at": _now(),
    }
    await db[TRANSFORMATION_AUDIT_COLLECTION].insert_one(audit)

    envelope = {
        "event_id": f"integration-transformed-{uuid4().hex[:12]}",
        "event_type": "integration-data-transformed",
        "source": "nats-data-transformation-layer",
        "adapter_id": adapter_id,
        "source_object": source_object,
        "target_collection": collection,
        "target_entity": transformed["target_entity"],
        "target_id": str(target_id),
        "transformation_id": audit["transformation_id"],
        "payload": {
            key: value for key, value in document.items() if key != "embedding"
        },
        "created_at": _now(),
    }
    try:
        await nats_store.publish_event(
            nats_store._event_bytes(envelope), subject=TRANSFORMED_INTEGRATION_SUBJECT
        )
        envelope["published"] = True
        envelope["subject"] = TRANSFORMED_INTEGRATION_SUBJECT
    except Exception as exc:
        envelope["published"] = False
        envelope["publish_error"] = str(exc)
    try:
        reducer = await reduce_event_to_state(
            db, envelope, subject=TRANSFORMED_INTEGRATION_SUBJECT
        )
        envelope["reducer"] = {
            "aggregate_key": reducer["aggregate_key"],
            "state_hash": reducer["state_hash"],
            "snapshot_hash": reducer["snapshot_hash"],
            "idempotent_replay": reducer["idempotent_replay"],
        }
    except Exception as exc:
        envelope["reducer"] = {"error": str(exc)}
    return envelope
