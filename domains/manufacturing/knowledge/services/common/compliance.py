import hashlib
import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from bson import ObjectId

log = logging.getLogger("uvicorn.error")
AUDIT_COLLECTION = "part11_audit_trail"
AUDIT_OUTBOX_COLLECTION = "part11_audit_outbox"
SIGNATURE_COLLECTION = "part11_electronic_signatures"
VERSION_COLLECTION = "part11_record_versions"
RETENTION_COLLECTION = "part11_retention_policies"
LOGIN_EVENT_COLLECTION = "part11_login_events"
EXPORT_COLLECTION = "part11_export_jobs"
ACCESS_REVIEW_COLLECTION = "gxp_access_reviews"
CHANGE_CONTROL_COLLECTION = "gxp_change_controls"
DATA_INTEGRITY_REVIEW_COLLECTION = "gxp_data_integrity_reviews"
RISK_ASSESSMENT_COLLECTION = "gxp_risk_assessments"
VALIDATION_EVIDENCE_COLLECTION = "gxp_validation_evidence"
USER_REQUIREMENT_COLLECTION = "gxp_user_requirements"
TRACEABILITY_MATRIX_COLLECTION = "gxp_traceability_matrices"
SUPPORTED_GXP_STANDARDS = [
    {
        "id": "21-cfr-part-11",
        "name": "21 CFR Part 11",
        "scope": "Electronic records and electronic signatures",
        "authority": "FDA",
        "source_url": "https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11",
    },
    {
        "id": "21-cfr-210-211",
        "name": "21 CFR Parts 210/211 cGMP",
        "scope": "Drug manufacturing, processing, packing, holding, production and process controls",
        "authority": "FDA",
        "source_url": "https://www.ecfr.gov/current/title-21/chapter-I/subchapter-C/part-211",
    },
    {
        "id": "eu-gmp-annex-11",
        "name": "EU GMP Annex 11",
        "scope": "Computerised systems used as part of GMP regulated activities",
        "authority": "European Commission",
        "source_url": "https://health.ec.europa.eu/medicinal-products/eudralex/eudralex-volume-4_en",
    },
    {
        "id": "eu-gmp-annex-15",
        "name": "EU GMP Annex 15",
        "scope": "Qualification and validation lifecycle",
        "authority": "European Commission",
        "source_url": "https://health.ec.europa.eu/medicinal-products/eudralex/eudralex-volume-4_en",
    },
    {
        "id": "ich-q9-r1",
        "name": "ICH Q9(R1)",
        "scope": "Quality risk management",
        "authority": "ICH/FDA",
        "source_url": "https://www.fda.gov/regulatory-information/search-fda-guidance-documents/q9r1-quality-risk-management",
    },
    {
        "id": "ich-q10",
        "name": "ICH Q10",
        "scope": "Pharmaceutical quality system",
        "authority": "ICH/FDA",
        "source_url": "https://www.fda.gov/regulatory-information/search-fda-guidance-documents/q10-pharmaceutical-quality-system",
    },
    {
        "id": "mhra-gxp-data-integrity",
        "name": "MHRA GxP Data Integrity",
        "scope": "Data governance and data integrity across GxP",
        "authority": "MHRA",
        "source_url": "https://www.gov.uk/government/publications/guidance-on-gxp-data-integrity",
    },
    {
        "id": "gamp-5",
        "name": "GAMP 5",
        "scope": "Risk-based validation of GxP computerised systems",
        "authority": "ISPE",
        "source_url": "https://ispe.org/publications/guidance-documents/gamp-5",
    },
]
GXP_STANDARD_NAMES = [standard["name"] for standard in SUPPORTED_GXP_STANDARDS]
DATA_INTEGRITY_PRINCIPLES = {
    "attributable": "Actor identity is captured for regulated actions.",
    "legible": "Records are stored as readable structured data.",
    "contemporaneous": "Server-side UTC timestamp is captured at write/sign time.",
    "original": "Original before/after hashes and version snapshots are retained.",
    "accurate": "Hashing and audit verification detect tampering.",
    "complete": "Audit entries include action, actor, record, before/after, and result summary.",
    "consistent": "Timestamps, identifiers, and hash-chain order are deterministic.",
    "enduring": "Retention policy blocks premature deletion where configured.",
    "available": "Audit/version/signature endpoints support inspection and export workflows.",
}
COMPLIANCE_CONTROL_BASELINE = [
    "unique_user_identity",
    "role_based_access_control",
    "server_utc_timestamps",
    "hash_chained_audit_trail",
    "record_version_history",
    "electronic_signature_reauthentication",
    "signature_record_hash_linking",
    "retention_policy_enforcement",
    "sensitive_value_redaction",
    "audit_trail_verification",
]
COMPLIANCE_EXCLUDED_COLLECTIONS = {
    AUDIT_COLLECTION,
    VERSION_COLLECTION,
}
AUDIT_VALUE_MAX_JSON_CHARS = 200_000
AUDIT_SAMPLE_ITEMS = 5

_current_user: ContextVar[dict[str, Any] | None] = ContextVar(
    "part11_current_user", default=None
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def set_compliance_user(user: dict[str, Any] | None) -> None:
    _current_user.set(user)


def get_compliance_user() -> dict[str, Any]:
    user = _current_user.get()
    if isinstance(user, dict):
        return {
            "user_id": user.get("user_id") or user.get("sub") or "unknown",
            "email": user.get("email"),
            "role": user.get("role"),
        }
    return {"user_id": "system", "email": None, "role": "system"}


def json_default(value: Any) -> str:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        utc_value = value.astimezone(timezone.utc)
        mongo_precision = utc_value.replace(
            microsecond=(utc_value.microsecond // 1000) * 1000
        )
        return mongo_precision.isoformat()
    return str(value)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(
                token in lowered
                for token in ("password", "secret", "token", "authorization", "api_key")
            ):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def stable_json(value: Any) -> str:
    return json.dumps(
        redact_sensitive(value),
        sort_keys=True,
        default=json_default,
        separators=(",", ":"),
    )


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _json_size(value: Any) -> int:
    return len(stable_json(value))


def _compact_audit_value(
    value: Any, *, max_chars: int = AUDIT_VALUE_MAX_JSON_CHARS
) -> Any:
    redacted = redact_sensitive(value)
    if redacted is None or _json_size(redacted) <= max_chars:
        return redacted

    if isinstance(redacted, list):
        sample = [
            _compact_audit_value(
                item, max_chars=max(max_chars // max(AUDIT_SAMPLE_ITEMS, 1), 1)
            )
            for item in redacted[:AUDIT_SAMPLE_ITEMS]
        ]
        return {
            "_audit_compacted": True,
            "original_type": "list",
            "item_count": len(redacted),
            "sample_count": len(sample),
            "sample": sample,
        }

    if isinstance(redacted, dict):
        summary: dict[str, Any] = {
            "_audit_compacted": True,
            "original_type": "dict",
            "key_count": len(redacted),
            "keys": sorted(str(key) for key in redacted)[:50],
        }
        for key, item in redacted.items():
            if isinstance(item, list):
                summary[f"{key}_count"] = len(item)
            elif isinstance(item, dict):
                summary[f"{key}_key_count"] = len(item)
            elif key in {
                "id",
                "_id",
                "layout_id",
                "page_id",
                "workflow_id",
                "owner_id",
                "record_id",
            }:
                summary[key] = item

        samples = {}
        for key, item in redacted.items():
            if key in {
                "nodes",
                "edges",
                "links",
                "connections",
                "visual_nodes",
                "node_snapshots",
            } and isinstance(item, list):
                samples[key] = [
                    _compact_audit_value(sample_item, max_chars=max(max_chars // 20, 1))
                    for sample_item in item[:AUDIT_SAMPLE_ITEMS]
                ]
        if samples:
            summary["samples"] = samples
        return summary

    return {
        "_audit_compacted": True,
        "original_type": type(redacted).__name__,
        "repr": str(redacted)[:max_chars],
    }


def compliance_metadata() -> dict[str, Any]:
    return {
        "standards": GXP_STANDARD_NAMES,
        "standard_ids": [standard["id"] for standard in SUPPORTED_GXP_STANDARDS],
        "data_integrity_principles": list(DATA_INTEGRITY_PRINCIPLES.keys()),
        "controls": COMPLIANCE_CONTROL_BASELINE,
    }


def compliance_readiness() -> dict[str, Any]:
    return {
        "status": "technical-controls-enabled",
        "qualification_status": "requires-site-validation-and-qa-approval",
        "standards": SUPPORTED_GXP_STANDARDS,
        "data_integrity_principles": DATA_INTEGRITY_PRINCIPLES,
        "technical_controls": COMPLIANCE_CONTROL_BASELINE,
        "required_site_controls": [
            "approved_sops",
            "validation_plan",
            "risk_assessment",
            "iq_oq_pq_evidence",
            "traceability_matrix",
            "training_records",
            "periodic_review",
            "backup_restore_testing",
            "business_continuity_testing",
            "qa_release_approval",
        ],
    }


def extract_change_reason(update: Any) -> str | None:
    if not isinstance(update, dict):
        return None
    candidates = [update]
    for value in update.values():
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        for key in (
            "change_reason",
            "reason_for_change",
            "reason",
            "reason_code",
            "rejected_reason",
        ):
            value = candidate.get(key)
            if value:
                return str(value)
    return None


RECORD_ID_KEYS = (
    "change_control_id",
    "access_review_id",
    "risk_assessment_id",
    "validation_evidence_id",
    "id",
    "document_id",
    "record_id",
    "layout_id",
    "node_id",
    "edge_id",
    "user_id",
    "team_id",
    "department_id",
    "role_id",
    "permission_id",
    "work_order_id",
    "workflow_id",
    "step_id",
    "task_id",
    "checklist_id",
    "constraint_id",
    "constraints_id",
    "sop_id",
    "recipe_id",
    "plant_id",
    "line_id",
    "stage_id",
    "workstation_id",
    "machine_id",
    "equipment_id",
    "equipment_group_id",
    "device_id",
    "server_id",
    "sensor_id",
    "sensor_group_id",
    "plc_id",
    "queue_id",
    "asset_id",
    "event_id",
    "alert_id",
    "document_metadata_id",
    "document_file_id",
    "file_id",
    "customer_id",
    "supplier_id",
    "product_id",
    "order_id",
    "shipment_id",
    "_id",
)


def document_record_id(document: Any, fallback: Any = None) -> str | None:
    if isinstance(document, dict):
        for key in RECORD_ID_KEYS:
            value = document.get(key)
            if value is not None:
                return str(value)
    if fallback is not None:
        return str(fallback)
    return None


def compliance_record_envelope(
    collection: str, record: dict[str, Any] | None = None, *, action: str | None = None
) -> dict[str, Any]:
    now = utc_now()
    record_id = document_record_id(record) if isinstance(record, dict) else None
    existing = (
        record.get("compliance")
        if isinstance(record, dict) and isinstance(record.get("compliance"), dict)
        else {}
    )
    signatures = (
        existing.get("signatures")
        if isinstance(existing.get("signatures"), list)
        else []
    )
    change_controls = (
        existing.get("change_controls")
        if isinstance(existing.get("change_controls"), list)
        else []
    )
    envelope = {
        **existing,
        "standard": "21 CFR Part 11",
        "standards": GXP_STANDARD_NAMES,
        "audit_enabled": True,
        "signature_enabled": True,
        "change_management_enabled": True,
        "collection": collection,
        "record_id": record_id,
        "signatures": signatures,
        "change_controls": change_controls,
        "last_write_action": action,
        "updated_at": now,
    }
    envelope.setdefault("created_at", now)
    return envelope


def attach_entity_compliance_metadata(
    collection: str, document: Any, *, action: str | None = None
) -> Any:
    if collection in COMPLIANCE_EXCLUDED_COLLECTIONS or not isinstance(document, dict):
        return document
    document["compliance"] = compliance_record_envelope(
        collection, document, action=action
    )
    return document


def attach_entity_compliance_update(
    collection: str, update: Any, *, action: str | None = None
) -> Any:
    if collection in COMPLIANCE_EXCLUDED_COLLECTIONS or not isinstance(update, dict):
        return update
    if not any(str(key).startswith("$") for key in update):
        return attach_entity_compliance_metadata(
            collection, dict(update), action=action
        )
    now = utc_now()
    next_update = dict(update)
    set_fields = dict(next_update.get("$set") or {})
    set_fields.update(
        {
            "compliance.standard": "21 CFR Part 11",
            "compliance.standards": GXP_STANDARD_NAMES,
            "compliance.audit_enabled": True,
            "compliance.signature_enabled": True,
            "compliance.change_management_enabled": True,
            "compliance.collection": collection,
            "compliance.last_write_action": action,
            "compliance.updated_at": now,
        }
    )
    set_on_insert = dict(next_update.get("$setOnInsert") or {})
    set_on_insert.setdefault("compliance.created_at", now)
    next_update["$set"] = set_fields
    next_update["$setOnInsert"] = set_on_insert
    return next_update


def entity_compliance_reference(
    record: dict[str, Any], *, reference_type: str
) -> dict[str, Any]:
    if reference_type == "signature":
        keys = (
            "signature_id",
            "action",
            "meaning",
            "user_id",
            "email",
            "role",
            "signed_at",
            "signature_hash",
            "record_hash",
        )
    elif reference_type == "change_control":
        keys = (
            "change_control_id",
            "change_control_number",
            "title",
            "change_type",
            "status",
            "risk_score",
            "created_at",
            "created_by",
        )
    else:
        keys = tuple(record.keys())
    return {key: record.get(key) for key in keys if record.get(key) is not None}


async def append_record_version(
    raw_db,
    *,
    collection: str,
    record: dict[str, Any],
    action: str,
) -> None:
    if collection in COMPLIANCE_EXCLUDED_COLLECTIONS:
        return
    record_id = document_record_id(record)
    now = utc_now()
    previous = await raw_db[VERSION_COLLECTION].find_one(
        {"collection": collection, "record_id": record_id},
        sort=[("version_number", -1)],
    )
    version_number = int(previous.get("version_number", 0)) + 1 if previous else 1
    version = {
        "version_id": f"version-{uuid4().hex}",
        "standard": "21 CFR Part 11",
        **compliance_metadata(),
        "collection": collection,
        "record_id": record_id,
        "version_number": version_number,
        "action": action,
        "record_hash": sha256_hex(record),
        "record": _compact_audit_value(record),
        "created_at": now,
        "created_by": get_compliance_user(),
    }
    await raw_db[VERSION_COLLECTION].insert_one(version)


async def enforce_retention(
    raw_db, *, collection: str, record: dict[str, Any] | None
) -> None:
    if not record:
        return
    policy = await raw_db[RETENTION_COLLECTION].find_one(
        {"collection": collection, "enabled": True}
    )
    if not policy:
        return

    now = utc_now()
    retain_until = record.get("retain_until") or record.get("retention_until")
    if isinstance(retain_until, str):
        try:
            retain_until = datetime.fromisoformat(retain_until)
        except ValueError:
            retain_until = None
    if isinstance(retain_until, datetime):
        if retain_until.tzinfo is None:
            retain_until = retain_until.replace(tzinfo=timezone.utc)
        if retain_until > now and not policy.get(
            "allow_delete_before_retention_expiry", False
        ):
            raise PermissionError(
                f"Record is retained until {retain_until.isoformat()}"
            )

    minimum_days = int(policy.get("minimum_retention_days") or 0)
    created_at = record.get("created_at")
    if isinstance(created_at, datetime) and minimum_days > 0:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        elapsed_days = (now - created_at).days
        if elapsed_days < minimum_days and not policy.get(
            "allow_delete_before_retention_expiry", False
        ):
            raise PermissionError(
                f"Record retention policy requires {minimum_days} days before deletion"
            )


async def append_audit_entry(
    raw_db,
    *,
    collection: str,
    action: str,
    record_id: str | None = None,
    before: Any = None,
    after: Any = None,
    filter: Any = None,
    update: Any = None,
    result: Any = None,
) -> None:
    if collection in COMPLIANCE_EXCLUDED_COLLECTIONS:
        return

    audit_collection = raw_db[AUDIT_COLLECTION]
    previous = await audit_collection.find_one({}, sort=[("_id", -1)])
    previous_hash = previous.get("entry_hash") if previous else None
    actor = get_compliance_user()
    entry = {
        "audit_id": f"audit-{uuid4().hex}",
        "standard": "21 CFR Part 11",
        **compliance_metadata(),
        "collection": collection,
        "record_id": record_id,
        "action": action,
        "actor": actor,
        "timestamp": utc_now(),
        "filter": _compact_audit_value(filter),
        "update": _compact_audit_value(update),
        "before_hash": sha256_hex(before) if before is not None else None,
        "after_hash": sha256_hex(after) if after is not None else None,
        "before": _compact_audit_value(before),
        "after": _compact_audit_value(after),
        "result_summary": _compact_audit_value(result),
        "previous_hash": previous_hash,
        "change_reason": extract_change_reason(update) or extract_change_reason(after),
    }
    entry["entry_hash"] = sha256_hex(
        {key: value for key, value in entry.items() if key != "entry_hash"}
    )
    await audit_collection.insert_one(entry)
    queue_event = json.loads(
        stable_json(
            {
                "event": "regulated_audit_entry_appended",
                "audit_id": entry["audit_id"],
                "collection": collection,
                "record_id": record_id,
                "action": action,
                "actor": actor,
                "timestamp": entry["timestamp"],
                "entry_hash": entry["entry_hash"],
                "previous_hash": previous_hash,
                "target_node": "Receive Regulated Event",
                "audit_entry": entry,
            }
        )
    )
    try:
        from services.auth.helpers import nats_store

        subject = await nats_store.publish_audit_event(queue_event)
        await raw_db[AUDIT_OUTBOX_COLLECTION].update_one(
            {"audit_id": entry["audit_id"]},
            {
                "$setOnInsert": {"created_at": utc_now()},
                "$set": {
                    "audit_id": entry["audit_id"],
                    "status": "published",
                    "subject": subject,
                    "published_at": utc_now(),
                    "last_error": None,
                    "event": queue_event,
                },
            },
            upsert=True,
        )
    except Exception as exc:
        log.exception(
            "Failed to publish Part 11 audit entry to NATS audit queue audit_id=%s",
            entry["audit_id"],
        )
        await raw_db[AUDIT_OUTBOX_COLLECTION].update_one(
            {"audit_id": entry["audit_id"]},
            {
                "$setOnInsert": {"created_at": utc_now()},
                "$set": {
                    "audit_id": entry["audit_id"],
                    "status": "pending",
                    "subject": "indus.audit.queue",
                    "last_error": str(exc),
                    "last_attempted_at": utc_now(),
                    "event": queue_event,
                },
                "$inc": {"attempts": 1},
            },
            upsert=True,
        )


def signature_payload_hash(payload: dict[str, Any]) -> str:
    return sha256_hex(payload)
