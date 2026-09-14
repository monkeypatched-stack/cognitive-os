import asyncio
import base64
import hashlib
import os
import re
import subprocess
import sys
import hmac
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
from bson import ObjectId
from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    Cookie,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
import httpx
from jose import JWTError
from pydantic import BaseModel, Field

from services.auth.helpers.permissions import get_by_permission_id
from services.auth.helpers.roles import (
    get_by_name as get_role_by_name,
    get_by_id as get_role_by_id,
)  # ← import get_by_id
from services.auth.models.login import LoginRequest, RefreshTokenRequest, TokenResponse
from services.auth.helpers.tokens import (
    create_access_token,
    create_mfa_challenge_token,
    create_refresh_token,
    decode_access_token,
    decode_mfa_challenge_token,
    decode_refresh_token,
)

from services.common.auth import get_current_user
from services.common.tenant_scope import user_tenant
from services.common.compliance import (
    ACCESS_REVIEW_COLLECTION,
    AUDIT_COLLECTION,
    append_audit_entry,
    CHANGE_CONTROL_COLLECTION,
    entity_compliance_reference,
    compliance_metadata,
    DATA_INTEGRITY_REVIEW_COLLECTION,
    EXPORT_COLLECTION,
    LOGIN_EVENT_COLLECTION,
    RISK_ASSESSMENT_COLLECTION,
    compliance_readiness,
    RECORD_ID_KEYS,
    RETENTION_COLLECTION,
    SIGNATURE_COLLECTION,
    TRACEABILITY_MATRIX_COLLECTION,
    USER_REQUIREMENT_COLLECTION,
    VALIDATION_EVIDENCE_COLLECTION,
    VERSION_COLLECTION,
    sha256_hex,
    signature_payload_hash,
    utc_now,
)
from services.common.config import settings
from services.common.approval_chains import (
    approval_notification_event,
    approval_graph_relationships,
    approval_step_email,
    create_approval_tracking_and_notifications,
    resolve_named_approval_chain,
    validate_named_approval_chain,
)
from services.common.neo4j_mirror import mirror_document, safe_mirror
from services.common.n8n_auth import n8n_webhook_auth_headers
from services.auth.helpers.revocation import block_jti, is_jti_revoked
from services.auth.helpers.store import (
    revoke_refresh_token,
    save_refresh_token,
    token_exists,
)
from services.auth.helpers import nats_store
from services.auth.helpers.approval_decisions import (
    ApprovalAlreadyDecidedError,
    PROPOSED_CHANGE_COLLECTION,
    record_approval_decision,
)
from services.auth.helpers.users import get_by_email, verify_password, get_by_id
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.db import get_database

router = APIRouter()

COOKIE_OPTIONS = dict(
    key=settings.REFRESH_COOKIE_NAME,
    httponly=True,
    secure=False,  # set True in production (HTTPS only)
    samesite="strict",
    max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
)


def _as_aware_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bson_safe(value):
    return jsonable_encoder(value, custom_encoder={ObjectId: str})


class ElectronicSignatureRequest(BaseModel):
    password: str = Field(..., min_length=1)
    meaning: str = Field(..., min_length=1, max_length=255)
    collection: str = Field(..., min_length=1, max_length=128)
    record_id: str = Field(..., min_length=1, max_length=255)
    action: str = Field(default="approve", min_length=1, max_length=128)


class ManualAuditTrailRequest(BaseModel):
    collection: str = Field(..., min_length=1, max_length=128)
    record_id: str = Field(..., min_length=1, max_length=255)
    action: str = Field(..., min_length=1, max_length=128)
    reason: str = Field(..., min_length=1, max_length=1000)
    detail: str | None = Field(default=None, max_length=4000)


class ComplianceTestExecutionRequest(BaseModel):
    scope: str = Field(
        default="all", pattern="^(all|iq|oq|pq|gqa|test-execution-reports)$"
    )


class ComplianceAgentRunRequest(BaseModel):
    scopes: list[str] = Field(default_factory=lambda: ["iq", "oq", "pq", "gqa"])
    continue_on_failure: bool = False


class ComplianceAgentReportRequest(BaseModel):
    summary: dict = Field(default_factory=dict)
    reports: list[dict] = Field(default_factory=list)
    failed_tests: list[dict] = Field(default_factory=list)
    scope_results: list[dict] = Field(default_factory=list)
    message: str | None = Field(default=None, max_length=2000)
    source: str = Field(default="n8n", max_length=128)
    raw_response: dict = Field(default_factory=dict)


class ElectronicSignatureResponse(BaseModel):
    signature_id: str
    user_id: str
    collection: str
    record_id: str
    action: str
    meaning: str
    record_hash: str | None = None
    signature_hash: str | None = None
    signed_at: str


def _audit_elasticsearch_auth():
    if settings.AUDIT_ELASTICSEARCH_USERNAME:
        return (
            settings.AUDIT_ELASTICSEARCH_USERNAME,
            settings.AUDIT_ELASTICSEARCH_PASSWORD,
        )
    return None


def _audit_elasticsearch_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.AUDIT_ELASTICSEARCH_API_KEY:
        headers["Authorization"] = f"ApiKey {settings.AUDIT_ELASTICSEARCH_API_KEY}"
    return headers


async def _search_audit_trail_elasticsearch(
    *,
    q: str | None,
    collection: str | None,
    record_id: str | None,
    page: int,
    page_size: int,
) -> dict | None:
    if not settings.AUDIT_ELASTICSEARCH_ENABLED:
        return None

    must: list[dict] = []
    filters: list[dict] = []
    if q:
        must.append(
            {
                "multi_match": {
                    "query": q,
                    "fields": [
                        "search_text",
                        "audit_id",
                        "collection",
                        "record_id",
                        "action",
                        "actor_user_id",
                        "actor_email",
                        "change_control_id",
                        "change_reason",
                    ],
                    "lenient": True,
                }
            }
        )
    else:
        must.append({"match_all": {}})
    if collection:
        filters.append({"term": {"collection": collection}})
    if record_id:
        filters.append({"term": {"record_id": record_id}})

    payload = {
        "from": (page - 1) * page_size,
        "size": page_size,
        "query": {"bool": {"must": must, "filter": filters}},
        "sort": [{"timestamp": {"order": "desc", "unmapped_type": "date"}}],
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.AUDIT_ELASTICSEARCH_URL.rstrip("/"),
            timeout=10.0,
            auth=_audit_elasticsearch_auth(),
            headers=_audit_elasticsearch_headers(),
        ) as client:
            response = await client.post(
                f"/{settings.AUDIT_ELASTICSEARCH_INDEX}/_search", json=payload
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError:
        return None

    total_value = data.get("hits", {}).get("total", 0)
    total = (
        total_value.get("value", 0)
        if isinstance(total_value, dict)
        else int(total_value or 0)
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "source": "elasticsearch",
        "index": settings.AUDIT_ELASTICSEARCH_INDEX,
        "results": [
            hit.get("_source", {}) for hit in data.get("hits", {}).get("hits", [])
        ],
    }


class RetentionPolicyRequest(BaseModel):
    collection: str = Field(..., min_length=1, max_length=128)
    minimum_retention_days: int = Field(default=0, ge=0)
    allow_delete_before_retention_expiry: bool = False
    enabled: bool = True
    reason: str | None = Field(default=None, max_length=500)


class GxpEvidenceRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    standard_id: str | None = Field(default=None, max_length=128)
    scope: str | None = Field(default=None, max_length=255)
    status: str = Field(default="Open", min_length=1, max_length=80)
    owner: str | None = Field(default=None, max_length=255)
    summary: str | None = Field(default=None, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list)
    reviewed_by: str | None = Field(default=None, max_length=255)
    review_due_at: datetime | None = None


class RiskAssessmentRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    process: str | None = Field(default=None, max_length=255)
    hazard: str = Field(..., min_length=1, max_length=1000)
    impact: str | None = Field(default=None, max_length=1000)
    severity: int = Field(..., ge=1, le=5)
    occurrence: int = Field(..., ge=1, le=5)
    detectability: int = Field(..., ge=1, le=5)
    mitigation: str | None = Field(default=None, max_length=2000)
    owner: str | None = Field(default=None, max_length=255)
    status: str = Field(default="Open", min_length=1, max_length=80)


class UserRequirementInput(BaseModel):
    requirement_id: str | None = Field(default=None, max_length=128)
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=4000)
    category: str = Field(default="Compliance", min_length=1, max_length=128)
    priority: str = Field(default="High", pattern=r"^(Critical|High|Medium|Low)$")
    source: str = Field(
        default="User Requirement Specification", min_length=1, max_length=255
    )
    acceptance_criteria: list[str] = Field(default_factory=list)
    linked_controls: list[str] = Field(default_factory=list)
    linked_tests: list[str] = Field(default_factory=list)
    linked_evidence: list[str] = Field(default_factory=list)
    owner: str | None = Field(default=None, max_length=255)
    status: str = Field(default="Draft", min_length=1, max_length=80)


class UserRequirementsAssessmentRequest(BaseModel):
    scope: str = Field(default="pharma-compliance-proof", min_length=1, max_length=255)
    requirements: list[UserRequirementInput] = Field(default_factory=list)


class TraceabilityMatrixRequest(BaseModel):
    scope: str = Field(default="pharma-compliance-proof", min_length=1, max_length=255)
    requirement_ids: list[str] = Field(default_factory=list)


class ChangeControlRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    change_type: str = Field(..., pattern=r"^(Major|Minor|Emergency|Temporary)$")
    reason: str = Field(..., min_length=1, max_length=2000)
    justification: str | None = Field(default=None, max_length=2000)
    initiating_facility: str | None = Field(default=None, max_length=255)
    affected_entity_class: str | None = Field(default=None, max_length=128)
    affected_entities: list[dict] = Field(default_factory=list)
    impact_assessment: str | None = Field(default=None, max_length=8000)
    graph_impact_summary: dict | None = None
    affected_collections: list[str] = Field(default_factory=list)
    proposed_values: dict = Field(default_factory=dict)
    proposed_update: dict = Field(default_factory=dict)
    proposed_changes: list[dict] = Field(default_factory=list)
    attachments: list[dict] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)
    risk_assessment_id: str | None = Field(default=None, max_length=255)
    risk_score: int | None = Field(default=None, ge=1, le=125)
    approval_tier: str | None = Field(default=None, max_length=128)
    approval_mode: str | None = Field(default=None, max_length=128)
    approval_deadline_at: datetime | None = None
    validation_required: bool = True
    revalidation_required: bool | None = None
    requalification_required: bool | None = None
    sop_revision_required: bool | None = None
    retraining_required: bool | None = None
    regulatory_notification_required: bool | None = None
    stability_study_required: bool | None = None
    implementation_tasks: list[dict] = Field(default_factory=list)
    training_plan: list[dict] = Field(default_factory=list)
    capa_references: list[str] = Field(default_factory=list)
    deviation_references: list[str] = Field(default_factory=list)
    approval_chain: list[dict] = Field(default_factory=list)
    manual_approval_chain: list[dict] = Field(default_factory=list)
    escalation_policy: str | None = Field(default=None, max_length=2000)
    effectiveness_check_days: int | None = Field(default=None, ge=0, le=3650)
    status: str = Field(default="Open", min_length=1, max_length=80)


class ExternalApprovalDecisionRequest(BaseModel):
    token: str = Field(..., min_length=1)
    decision: str = Field(..., min_length=1, max_length=80)
    reason: str | None = Field(default=None, max_length=2000)
    signature_meaning: str | None = Field(default=None, max_length=255)
    approver_email: str | None = Field(default=None, max_length=255)
    approver_user_id: str | None = Field(default=None, max_length=255)
    source: str = Field(default="external_approval_form", max_length=128)


class ReviewRequest(BaseModel):
    scope: str = Field(..., min_length=1, max_length=255)
    outcome: str = Field(..., min_length=1, max_length=255)
    findings: list[str] = Field(default_factory=list)
    corrective_actions: list[str] = Field(default_factory=list)
    next_review_due_at: datetime | None = None


class MfaEnrollRequest(BaseModel):
    password: str = Field(..., min_length=1)


class MfaEnableRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=32)


class MfaDisableRequest(BaseModel):
    password: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=32)


class MfaVerifyRequest(BaseModel):
    mfa_challenge_token: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=32)


class MfaEnrollResponse(BaseModel):
    secret: str
    otpauth_uri: str
    backup_codes: list[str]


async def _find_signed_record(db, collection: str, record_id: str) -> dict | None:
    query = {"$or": [{key: record_id} for key in RECORD_ID_KEYS]}
    try:
        if ObjectId.is_valid(record_id):
            query["$or"].append({"_id": ObjectId(record_id)})
    except Exception:
        pass
    return await db[collection].find_one(query)


def _record_lookup_query(record_id: str) -> dict:
    query = {"$or": [{key: record_id} for key in RECORD_ID_KEYS]}
    try:
        if ObjectId.is_valid(record_id):
            query["$or"].append({"_id": ObjectId(record_id)})
    except Exception:
        pass
    return query


async def _link_signature_to_entity(db, signature: dict) -> None:
    collection = signature.get("collection")
    record_id = signature.get("record_id")
    if not collection or not record_id:
        return
    reference = entity_compliance_reference(signature, reference_type="signature")
    await db[collection].update_one(
        _record_lookup_query(str(record_id)),
        {
            "$set": {
                "compliance.signature_enabled": True,
                "compliance.last_signature_id": signature.get("signature_id"),
                "compliance.last_signed_at": signature.get("signed_at"),
            },
            "$addToSet": {
                "compliance.signatures": reference,
                "electronic_signatures": reference,
            },
        },
    )


async def _link_change_control_to_entities(db, change_control: dict) -> None:
    reference = entity_compliance_reference(
        change_control, reference_type="change_control"
    )
    for entity in change_control.get("affected_entities") or []:
        collection = entity.get("collection") or _entity_collection(entity)
        record_id = entity.get("entity_id") or _entity_id(entity)
        if not collection or not record_id:
            continue
        await db[collection].update_one(
            _record_lookup_query(str(record_id)),
            {
                "$set": {
                    "compliance.change_management_enabled": True,
                    "compliance.last_change_control_id": change_control.get(
                        "change_control_id"
                    ),
                    "compliance.last_change_control_status": change_control.get(
                        "status"
                    ),
                },
                "$addToSet": {
                    "compliance.change_controls": reference,
                    "change_controls": reference,
                },
            },
        )


def _proposed_values_from_payload(payload: dict) -> dict:
    proposed_values = payload.get("proposed_values")
    if isinstance(proposed_values, dict) and proposed_values:
        return dict(proposed_values)
    proposed_update = payload.get("proposed_update")
    if isinstance(proposed_update, dict):
        set_values = proposed_update.get("$set")
        if isinstance(set_values, dict) and set_values:
            return dict(set_values)
        if proposed_update and not any(
            str(key).startswith("$") for key in proposed_update
        ):
            return dict(proposed_update)
    return {}


def _record_key_values(record: dict) -> dict:
    keys = [
        "_id",
        *RECORD_ID_KEYS,
    ]
    return {key: record.get(key) for key in keys if record.get(key) is not None}


async def _create_proposed_change_nodes(
    db, change_control: dict, current_user: dict
) -> list[dict]:
    proposed_nodes: list[dict] = []
    now = utc_now()
    requested_changes = change_control.get("proposed_changes")
    proposed_values = _proposed_values_from_payload(change_control)
    affected_entities = change_control.get("affected_entities") or []
    if isinstance(requested_changes, list) and requested_changes:
        proposed_specs = requested_changes
    else:
        proposed_specs = [
            {
                "collection": entity.get("collection"),
                "entity_id": entity.get("entity_id"),
                "entity_name": entity.get("name"),
                "proposed_values": proposed_values,
            }
            for entity in affected_entities
            if entity.get("collection") and entity.get("entity_id")
        ]

    for index, spec in enumerate(proposed_specs, start=1):
        if not isinstance(spec, dict):
            continue
        collection = spec.get("collection")
        entity_id = spec.get("entity_id") or spec.get("id") or spec.get("record_id")
        if not collection or not entity_id:
            continue
        live_record = await db[str(collection)].find_one(
            _record_lookup_query(str(entity_id)), {"_id": 0, "embedding": 0}
        )
        pending_values = (
            spec.get("proposed_values")
            if isinstance(spec.get("proposed_values"), dict)
            else proposed_values
        )
        node = {
            "proposed_change_id": f"proposed_change-{uuid4().hex}",
            "change_control_id": change_control.get("change_control_id"),
            "change_control_number": change_control.get("change_control_number"),
            "sequence": index,
            "status": "Pending Approval",
            "lifecycle_state": "proposed",
            "source_collection": collection,
            "source_id": str(entity_id),
            "source_name": spec.get("entity_name")
            or (live_record or {}).get("name")
            or (live_record or {}).get("title")
            or str(entity_id),
            "source_key": _record_key_values(live_record or {"id": entity_id}),
            "source_snapshot": live_record or {},
            "pending_properties": dict(pending_values or {}),
            "proposed_values": dict(pending_values or {}),
            "archived": False,
            "rolled_back": False,
            "live_node_touched": False,
            "approval_chain": change_control.get("approval_chain") or [],
            "approval_assignment_basis": change_control.get(
                "approval_assignment_basis"
            ),
            "created_at": now,
            "updated_at": now,
            "created_by": _current_user_id(current_user),
            "updated_by": _current_user_id(current_user),
            "effective_at": None,
        }
        node["record_hash"] = sha256_hex(node)
        await db[PROPOSED_CHANGE_COLLECTION].insert_one(node)
        proposed_nodes.append(_bson_safe(node))
    return proposed_nodes


def _request_metadata(request: Request | None) -> dict:
    if request is None:
        return {}
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


async def _record_login_event(
    db,
    *,
    email: str,
    status_value: str,
    request: Request | None = None,
    user_id: str | None = None,
    reason: str | None = None,
) -> None:
    event = {
        "event_id": f"login-{uuid4().hex}",
        "standard": "21 CFR Part 11",
        "standards": compliance_readiness()["standards"],
        "email": email.strip().lower(),
        "user_id": user_id,
        "status": status_value,
        "reason": reason,
        "timestamp": utc_now(),
        **_request_metadata(request),
    }
    await db[LOGIN_EVENT_COLLECTION].insert_one(event)


def _current_user_id(current_user: dict) -> str:
    return current_user.get("user_id") or current_user.get("sub")


async def _insert_gxp_record(
    db, collection: str, prefix: str, payload: dict, current_user: dict
) -> dict:
    now = utc_now()
    record_id = f"{prefix}-{uuid4().hex}"
    record = {
        f"{prefix}_id": record_id,
        "standard": "GxP",
        "standards": compliance_readiness()["standards"],
        **payload,
        "created_at": now,
        "updated_at": now,
        "created_by": _current_user_id(current_user),
        "updated_by": _current_user_id(current_user),
    }
    record["record_hash"] = sha256_hex(record)
    await db[collection].insert_one(record)
    return jsonable_encoder(record)


async def _list_gxp_records(
    db, collection: str, page: int, page_size: int, status_value: str | None = None
) -> dict:
    query = {"status": status_value} if status_value else {}
    total = await db[collection].count_documents(query)
    cursor = (
        db[collection]
        .find(query, {"_id": 0})
        .sort("_id", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "results": [item async for item in cursor],
    }


OPEN_CHANGE_STATUSES = {
    "Open",
    "Draft",
    "Submitted",
    "Under Review",
    "Approved",
    "Implementation",
    "Verification",
}


def _change_control_number(now: datetime, sequence: int) -> str:
    return f"SX-CHG-{now:%Y}-{sequence:04d}"


def _entity_collection(entity: dict) -> str | None:
    collection = entity.get("collection") or entity.get("entity_collection")
    if collection:
        collection_name = str(collection)
        return {
            "stages": "industrial_stages",
            "stage": "industrial_stages",
            "lines": "industrial_lines",
            "line": "industrial_lines",
            "plants": "industrial_plants",
            "plant": "industrial_plants",
            "machines": "pharmaceutical_machines",
            "equipment": "pharmaceutical_equipment",
        }.get(collection_name, collection_name)
    entity_type = (
        str(entity.get("type") or entity.get("entity_type") or "").strip().lower()
    )
    return {
        "machine": "pharmaceutical_machines",
        "equipment": "pharmaceutical_equipment",
        "workstation": "workstations",
        "sop": "sops",
        "process": "industrial_stages",
        "stage": "industrial_stages",
        "line": "industrial_lines",
        "plant": "industrial_plants",
        "material": "inventory_items",
        "recipe": "recipe_routes",
    }.get(entity_type)


def _entity_id(entity: dict) -> str | None:
    for key in (
        "entity_id",
        "id",
        "machine_id",
        "equipment_id",
        "workstation_id",
        "sop_id",
        "stage_id",
        "line_id",
        "plant_id",
        "recipe_id",
    ):
        value = entity.get(key)
        if value:
            return str(value)
    return None


async def _open_change_for_affected_entities(
    db, affected_entities: list[dict]
) -> dict | None:
    entity_ids = [_entity_id(entity) for entity in affected_entities]
    entity_ids = [entity_id for entity_id in entity_ids if entity_id]
    if not entity_ids:
        return None
    return await db[CHANGE_CONTROL_COLLECTION].find_one(
        {
            "status": {"$in": list(OPEN_CHANGE_STATUSES)},
            "affected_entities.entity_id": {"$in": entity_ids},
        },
        {"_id": 0},
    )


async def _resolve_affected_entities(db, affected_entities: list[dict]) -> list[dict]:
    resolved: list[dict] = []
    for entity in affected_entities:
        entity_id = _entity_id(entity)
        collection = _entity_collection(entity)
        record = None
        if collection and entity_id:
            entity_name = str(entity.get("name") or "").strip()
            name_query = []
            if entity_name:
                name_query = [
                    {
                        "name": {
                            "$regex": f"^{re.escape(entity_name)}$",
                            "$options": "i",
                        }
                    },
                    {
                        "title": {
                            "$regex": f"^{re.escape(entity_name)}$",
                            "$options": "i",
                        }
                    },
                ]
            record = await db[collection].find_one(
                {
                    "$or": [
                        {"id": entity_id},
                        {"entity_id": entity_id},
                        {"machine_id": entity_id},
                        {"equipment_id": entity_id},
                        {"workstation_id": entity_id},
                        {"sop_id": entity_id},
                        {"stage_id": entity_id},
                        {"line_id": entity_id},
                        {"plant_id": entity_id},
                    ]
                    + name_query
                },
                {"_id": 0, "embedding": 0},
            )
            if record:
                entity_id = (
                    record.get("id")
                    or record.get("entity_id")
                    or record.get("machine_id")
                    or record.get("equipment_id")
                    or record.get("workstation_id")
                    or record.get("sop_id")
                    or record.get("stage_id")
                    or record.get("line_id")
                    or record.get("plant_id")
                    or entity_id
                )
        resolved.append(
            {
                **entity,
                "entity_id": entity_id,
                "collection": collection,
                "name": entity.get("name")
                or (record or {}).get("name")
                or (record or {}).get("title")
                or entity_id,
                "status": entity.get("status") or (record or {}).get("status"),
                "resolved": bool(record),
            }
        )
    return resolved


async def _change_impact_assessment(db, affected_entities: list[dict]) -> dict:
    entity_ids = [
        str(entity.get("entity_id"))
        for entity in affected_entities
        if entity.get("entity_id")
    ]
    machine_ids = [
        entity["entity_id"]
        for entity in affected_entities
        if entity.get("collection") == "pharmaceutical_machines"
        and entity.get("entity_id")
    ]
    equipment_ids = [
        entity["entity_id"]
        for entity in affected_entities
        if entity.get("collection") == "pharmaceutical_equipment"
        and entity.get("entity_id")
    ]
    workstation_ids = [
        entity["entity_id"]
        for entity in affected_entities
        if entity.get("collection") == "workstations" and entity.get("entity_id")
    ]
    stage_ids = [
        entity["entity_id"]
        for entity in affected_entities
        if entity.get("collection") == "industrial_stages" and entity.get("entity_id")
    ]

    sops = (
        await db["sops"]
        .find(
            {
                "$or": [
                    {"entity_id": {"$in": entity_ids}},
                    {"machine_id": {"$in": machine_ids}},
                    {"equipment_id": {"$in": equipment_ids}},
                    {"workstation_id": {"$in": workstation_ids}},
                    {"stage_id": {"$in": stage_ids}},
                ]
            },
            {
                "_id": 0,
                "id": 1,
                "sop_id": 1,
                "title": 1,
                "name": 1,
                "entity_name": 1,
                "workstation_id": 1,
                "stage_id": 1,
                "line_id": 1,
                "plant_id": 1,
            },
        )
        .to_list(length=100)
    )
    work_orders = (
        await db["work_orders"]
        .find(
            {
                "$or": [
                    {"machine_id": {"$in": machine_ids}},
                    {"equipment_id": {"$in": equipment_ids}},
                    {"workstation_id": {"$in": workstation_ids}},
                    {"status": {"$in": ["Open", "In Progress", "Scheduled"]}},
                ]
            },
            {"_id": 0, "work_order_id": 1, "title": 1, "status": 1},
        )
        .to_list(length=100)
    )
    operators = (
        await db["users"]
        .find(
            {
                "$or": [
                    {"workstation_id": {"$in": workstation_ids}},
                    {"stage_id": {"$in": stage_ids}},
                ],
                "title": "Production Worker",
            },
            {"_id": 0, "user_id": 1, "name": 1, "workstation_id": 1, "stage_id": 1},
        )
        .to_list(length=100)
    )
    open_batches = (
        await db["batch_records"]
        .find(
            {"status": {"$in": ["Open", "In Progress", "Released"]}},
            {"_id": 0, "batch_id": 1, "status": 1},
        )
        .to_list(length=100)
    )

    return {
        "directly_affected_entities": affected_entities,
        "connected_sops": sops,
        "linked_work_orders": work_orders,
        "qualified_operators": operators,
        "open_batches": open_batches,
        "downstream_compliance_records": [],
        "flags": {
            "validated_system_review_required": any(
                entity.get("collection")
                in {"pharmaceutical_machines", "pharmaceutical_equipment", "sops"}
                for entity in affected_entities
            ),
            "quality_manager_signoff_required": True,
            "asset_hold_required": bool(machine_ids or equipment_ids),
        },
    }


def _change_decisions(payload: dict, impact: dict) -> dict:
    change_type = payload.get("change_type")
    risk_score = payload.get("risk_score") or 0
    major_or_risky = change_type in {"Major", "Emergency"} or risk_score >= 40
    sop_related = any(
        entity.get("collection") == "sops"
        for entity in payload.get("affected_entities") or []
    )
    process_related = any(
        entity.get("collection") in {"industrial_stages", "recipe_routes"}
        for entity in payload.get("affected_entities") or []
    )
    return {
        "revalidation_required": (
            payload.get("revalidation_required")
            if payload.get("revalidation_required") is not None
            else major_or_risky
        ),
        "requalification_required": (
            payload.get("requalification_required")
            if payload.get("requalification_required") is not None
            else impact["flags"]["asset_hold_required"]
        ),
        "sop_revision_required": (
            payload.get("sop_revision_required")
            if payload.get("sop_revision_required") is not None
            else sop_related or process_related
        ),
        "retraining_required": (
            payload.get("retraining_required")
            if payload.get("retraining_required") is not None
            else sop_related or process_related
        ),
        "regulatory_notification_required": (
            payload.get("regulatory_notification_required")
            if payload.get("regulatory_notification_required") is not None
            else change_type == "Major" and risk_score >= 60
        ),
        "stability_study_required": (
            payload.get("stability_study_required")
            if payload.get("stability_study_required") is not None
            else False
        ),
    }


def _implementation_tasks(payload: dict, impact: dict, decisions: dict) -> list[dict]:
    tasks = [
        {
            "task_id": f"task-{uuid4().hex}",
            "title": "Review graph-generated impact assessment",
            "owner_role": "Reviewer",
            "status": "Pending",
            "evidence_required": False,
        },
        {
            "task_id": f"task-{uuid4().hex}",
            "title": "Implement approved change",
            "owner_role": "Implementer",
            "status": "Pending",
            "evidence_required": True,
        },
        {
            "task_id": f"task-{uuid4().hex}",
            "title": "Verify implementation effectiveness",
            "owner_role": "Verifier",
            "status": "Pending",
            "evidence_required": True,
        },
    ]
    if decisions.get("sop_revision_required"):
        tasks.append(
            {
                "task_id": f"task-{uuid4().hex}",
                "title": "Revise affected SOP/document",
                "owner_role": "Document Owner",
                "status": "Pending",
                "evidence_required": True,
            }
        )
    if decisions.get("retraining_required"):
        tasks.append(
            {
                "task_id": f"task-{uuid4().hex}",
                "title": "Complete affected-operator retraining",
                "owner_role": "Training Coordinator",
                "status": "Pending",
                "evidence_required": True,
            }
        )
    if decisions.get("requalification_required"):
        tasks.append(
            {
                "task_id": f"task-{uuid4().hex}",
                "title": "Complete equipment requalification or calibration evidence",
                "owner_role": "Engineering",
                "status": "Pending",
                "evidence_required": True,
            }
        )
    return tasks


SYSTEM_APPROVAL_USERS = {
    "document_controller": {
        "user_id": "USER-AHM-DOCUMENT-CONTROLLER-001",
        "employee_id": "EMP-AHM-DOC-CTRL-001",
        "name": "Dev Mehta",
        "title": "Document Controller",
        "role": "document-controller",
        "department": "DEPT-QA-AHM",
        "team": "TEAM-AHM-DOC-CONTROL",
        "email": "dev.mehta@ahmedabad.example",
        "plant_id": "PLT-IND-AHM-004",
    },
    "quality_control": {
        "user_id": "USER-AHM-QUALITY-CONTROL-001",
        "employee_id": "EMP-AHM-QC-001",
        "name": "Meera Nair",
        "title": "Quality Control",
        "role": "quality-control",
        "department": "DEPT-QC-AHM",
        "team": "TEAM-AHM-QC",
        "email": "meera.nair@ahmedabad.example",
        "plant_id": "PLT-IND-AHM-004",
    },
    "change_control": {
        "user_id": "USER-AHM-CHANGE-CONTROL-001",
        "employee_id": "EMP-AHM-CC-001",
        "name": "Sanjay Rao",
        "title": "Change Control",
        "role": "change-control",
        "department": "DEPT-QA-AHM",
        "team": "TEAM-AHM-CHANGE-CONTROL",
        "email": "sanjay.rao@ahmedabad.example",
        "plant_id": "PLT-IND-AHM-004",
    },
}


async def _ensure_system_approval_user(db, key: str) -> dict:
    base = dict(SYSTEM_APPROVAL_USERS[key])
    existing = await db["users"].find_one(
        {"user_id": base["user_id"]}, {"embedding": 0}
    )
    now = utc_now()
    document = {
        **base,
        "is_active": True,
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    await db["users"].update_one(
        {"user_id": document["user_id"]}, {"$set": document}, upsert=True
    )
    # Project OUT the credential fields: this document is both mirrored to Neo4j and returned
    # to the caller. Re-reading the full user record pulled hashed_password / refresh_tokens
    # back in (the same fields the worker query at ~3535 already knows to exclude).
    stored = await db["users"].find_one(
        {"user_id": document["user_id"]},
        {
            "_id": 0,
            "embedding": 0,
            "password": 0,
            "hashed_password": 0,
            "refresh_tokens": 0,
        },
    )
    await safe_mirror(mirror_document("users", stored or document, "update"))
    return stored or document


def _approval_user_summary(user: dict | None, fallback_role: str) -> dict:
    if not user:
        return {
            "user_id": None,
            "name": None,
            "title": fallback_role,
            "role": fallback_role,
            "email": None,
        }
    fallback_email = (
        f"{str(user.get('user_id')).lower()}@internal.example"
        if user.get("user_id")
        else None
    )
    return {
        "user_id": user.get("user_id"),
        "name": user.get("name") or user.get("email") or user.get("user_id"),
        "title": user.get("title") or fallback_role,
        "role": user.get("role") or fallback_role,
        "email": user.get("email") or fallback_email,
    }


async def _approval_user_by_id(db, user_id: str | None) -> dict | None:
    if not user_id:
        return None
    return await db["users"].find_one({"user_id": user_id}, {"_id": 0, "embedding": 0})


async def _approval_user_by_dialog_step(db, step: dict) -> dict | None:
    assigned_user = (
        step.get("assigned_user") if isinstance(step.get("assigned_user"), dict) else {}
    )
    user_id = (
        step.get("assigned_user_id")
        or step.get("user_id")
        or step.get("approver_id")
        or assigned_user.get("user_id")
    )
    user = await _approval_user_by_id(db, str(user_id) if user_id else None)
    if user:
        return user
    email = (
        step.get("assigned_user_email")
        or step.get("email")
        or assigned_user.get("email")
    )
    name = (
        step.get("assigned_user_name")
        or step.get("name")
        or step.get("approver_name")
        or assigned_user.get("name")
    )
    clauses = []
    if email:
        clauses.append(
            {"email": {"$regex": f"^{re.escape(str(email).strip())}$", "$options": "i"}}
        )
    if name:
        clauses.append(
            {"name": {"$regex": f"^{re.escape(str(name).strip())}$", "$options": "i"}}
        )
    if not clauses:
        return None
    return await db["users"].find_one({"$or": clauses}, {"_id": 0, "embedding": 0})


def _manual_chain_from_payload(payload: dict) -> list[dict]:
    chain = (
        payload.get("approval_chain")
        if isinstance(payload.get("approval_chain"), list)
        else []
    )
    if not chain:
        chain = (
            payload.get("manual_approval_chain")
            if isinstance(payload.get("manual_approval_chain"), list)
            else []
        )
    return chain


async def _resolve_manual_approval_chain(
    db, chain: list[dict], basis: dict
) -> list[dict]:
    normalized: list[dict] = []
    for index, step in enumerate(chain, start=1):
        if not isinstance(step, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Approval step {index} must be an object.",
            )
        user = await _approval_user_by_dialog_step(db, step)
        if not user:
            label = step.get("stage") or step.get("step") or step.get("role") or index
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Approval step '{label}' must resolve to an existing named worker node.",
            )
        role = (
            step.get("role")
            or step.get("required_role")
            or step.get("assigned_user_title")
            or user.get("title")
            or "Approver"
        )
        stage = step.get("stage") or step.get("step") or f"{role} Approval"
        assigned_user = _approval_user_summary(user, str(role))
        normalized.append(
            {
                **step,
                "sequence": int(step.get("sequence") or index),
                "stage": stage,
                "step": stage,
                "status": step.get("status") or "Pending",
                "role": role,
                "required_role": step.get("required_role") or role,
                "required_roles": step.get("required_roles") or [role],
                "assigned_user": assigned_user,
                "assigned_user_id": assigned_user.get("user_id"),
                "assigned_user_name": assigned_user.get("name"),
                "assigned_user_title": assigned_user.get("title"),
                "assigned_user_email": assigned_user.get("email")
                or step.get("assigned_user_email"),
                "minimum_approvals": step.get("minimum_approvals") or 1,
                "routing": step.get("routing") or "sequential",
                "source": step.get("source") or "manual_dialog",
                "context": step.get("context") or basis,
            }
        )
    normalized.sort(key=lambda item: int(item.get("sequence") or 0))
    for index, step in enumerate(normalized, start=1):
        step["sequence"] = index
    validate_named_approval_chain(normalized)
    missing_email = [
        step.get("assigned_user_name") or step.get("assigned_user_id")
        for step in normalized
        if not approval_step_email(step)
    ]
    if missing_email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Approval step approvers must have email addresses: {', '.join(str(item) for item in missing_email)}",
        )
    return normalized


def _approval_email_payload(notification: dict) -> dict:
    approval_url = notification.get("approval_form_url")
    approval_id = notification.get("approval_id")
    external_decision_url = (
        f"{settings.APPROVAL_FORM_BASE_URL.rstrip('/')}/{approval_id}/external-decision"
        if approval_id
        else None
    )
    source_title = (
        notification.get("source_title")
        or notification.get("source_id")
        or "Approval request"
    )
    stage = notification.get("stage") or "Approval"
    role = notification.get("role") or "Approver"
    approver = (
        notification.get("assigned_user_name")
        or notification.get("assigned_user_id")
        or "Assigned approver"
    )
    expires_at = notification.get("approval_token_expires_at")
    lines = [
        notification.get("body") or f"Approval is pending for {source_title}.",
        "",
        f"Approval Stage: {stage}",
        f"Role: {role}",
        f"Approver: {approver}",
        f"Source: {source_title}",
        (
            f"Link Expires: {expires_at}"
            if expires_at
            else "Link Expires: 24 hours from issue"
        ),
        f"Approval Link: {approval_url}" if approval_url else "",
    ]
    body = "\n".join(line for line in lines if line is not None).strip()
    safe_url = _escape_html(approval_url)
    html = f"""
<div style="margin:0;padding:0;background:#f6f8fb;color:#172033;font-family:Inter,Segoe UI,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#f6f8fb;padding:24px 0;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;border-collapse:collapse;background:#ffffff;border:1px solid #d9e2ec;border-radius:8px;overflow:hidden;">
          <tr>
            <td style="padding:18px 24px;border-bottom:1px solid #d9e2ec;background:#ffffff;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
                <tr>
                  <td style="font-size:18px;font-weight:700;color:#172033;">Sentinel X</td>
                  <td align="right" style="font-size:13px;color:#667085;">GxP Approval</td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:26px 24px 10px;">
              <h1 style="margin:0;font-size:22px;line-height:1.3;color:#172033;font-weight:700;">Approval Required</h1>
              <p style="margin:8px 0 0;font-size:14px;line-height:1.5;color:#667085;">A controlled change request is waiting for your review and electronic decision.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:12px 24px 8px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:separate;border-spacing:0 10px;">
                <tr>
                  <td style="padding:12px;border:1px solid #d9e2ec;border-radius:6px;background:#fbfcfe;">
                    <div style="font-size:12px;color:#667085;margin-bottom:4px;">Record</div>
                    <div style="font-size:14px;font-weight:700;color:#172033;">{_escape_html(source_title)}</div>
                  </td>
                </tr>
                <tr>
                  <td style="padding:12px;border:1px solid #d9e2ec;border-radius:6px;background:#fbfcfe;">
                    <div style="font-size:12px;color:#667085;margin-bottom:4px;">Approval Stage</div>
                    <div style="font-size:14px;font-weight:700;color:#172033;">{_escape_html(stage)}</div>
                  </td>
                </tr>
                <tr>
                  <td style="padding:12px;border:1px solid #d9e2ec;border-radius:6px;background:#fbfcfe;">
                    <div style="font-size:12px;color:#667085;margin-bottom:4px;">Assigned Approver</div>
                    <div style="font-size:14px;font-weight:700;color:#172033;">{_escape_html(approver)} · {_escape_html(role)}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 24px 6px;">
              <a href="{safe_url}" style="display:inline-block;background:#1457d9;color:#ffffff;text-decoration:none;font-weight:700;font-size:14px;border-radius:6px;padding:12px 18px;">Open Approval Form</a>
            </td>
          </tr>
          <tr>
            <td style="padding:10px 24px 24px;">
              <p style="margin:0 0 8px;font-size:13px;line-height:1.5;color:#667085;">This link is unique to this notification and expires in 24 hours. You will be asked to sign in before approving or rejecting.</p>
              <p style="margin:0;font-size:12px;line-height:1.5;color:#667085;word-break:break-all;">{safe_url}</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</div>
"""
    return {
        "to": notification.get("assigned_user_email"),
        "subject": notification.get("subject") or f"Approval required: {source_title}",
        "body": body,
        "text": body,
        "html": html,
        "type": "approval_request",
        "source": "change_control_approval",
        "notification_id": notification.get("notification_id"),
        "approval_id": notification.get("approval_id"),
        "approval_form_url": approval_url,
        "external_decision_url": external_decision_url,
        "source_collection": notification.get("source_collection"),
        "source_id": notification.get("source_id"),
        "source_title": notification.get("source_title"),
        "assigned_user_id": notification.get("assigned_user_id"),
        "assigned_user_name": notification.get("assigned_user_name"),
        "assigned_user_email": notification.get("assigned_user_email"),
    }


async def _post_approval_notifications_to_n8n(event: dict) -> dict:
    webhook_url = str(settings.N8N_EMAIL_WEBHOOK_URL or "").strip()
    notifications = [
        item
        for item in (event.get("notifications") or [])
        if isinstance(item, dict) and item.get("assigned_user_email")
    ]
    status_payload = {
        "enabled": bool(webhook_url),
        "webhook_url_configured": bool(webhook_url),
        "attempted": 0,
        "queued": 0,
        "failed": 0,
        "recipients": [item.get("assigned_user_email") for item in notifications],
        "errors": [],
    }
    if not webhook_url or not notifications:
        return status_payload
    async with httpx.AsyncClient(timeout=10.0) as client:
        for notification in notifications:
            status_payload["attempted"] += 1
            try:
                response = await client.post(
                    webhook_url,
                    json=_approval_email_payload(notification),
                    headers=n8n_webhook_auth_headers(),
                )
                if 200 <= response.status_code < 300:
                    status_payload["queued"] += 1
                else:
                    status_payload["failed"] += 1
                    status_payload["errors"].append(
                        {
                            "recipient": notification.get("assigned_user_email"),
                            "status_code": response.status_code,
                            "response": response.text[:500],
                        }
                    )
            except httpx.HTTPError as exc:
                status_payload["failed"] += 1
                status_payload["errors"].append(
                    {
                        "recipient": notification.get("assigned_user_email"),
                        "error": str(exc),
                    }
                )
    return status_payload


async def _workstation_context_for_change(
    db, payload: dict, impact: dict
) -> tuple[dict | None, dict | None, dict | None]:
    entity_ids = [
        str(entity.get("entity_id"))
        for entity in payload.get("affected_entities") or []
        if entity.get("entity_id")
    ]
    connected_sops = impact.get("connected_sops") or []
    connected_sop_ids = [
        sop.get("id") or sop.get("sop_id")
        for sop in connected_sops
        if sop.get("id") or sop.get("sop_id")
    ]
    sop_query = {
        "$or": [
            {"id": {"$in": entity_ids}},
            {"sop_id": {"$in": entity_ids}},
            {"id": {"$in": connected_sop_ids}},
            {"sop_id": {"$in": connected_sop_ids}},
        ]
    }
    sop = (
        await db["sops"].find_one(sop_query, {"_id": 0, "embedding": 0})
        if entity_ids or connected_sop_ids
        else None
    )
    sop_workstation_ids = (
        [str(sop.get("workstation_id"))] if sop and sop.get("workstation_id") else []
    )
    workstation_ids = [
        str(entity.get("entity_id"))
        for entity in payload.get("affected_entities") or []
        if entity.get("collection") == "workstations" and entity.get("entity_id")
    ] + sop_workstation_ids
    stage_ids = [
        str(entity.get("entity_id"))
        for entity in payload.get("affected_entities") or []
        if entity.get("collection") == "industrial_stages" and entity.get("entity_id")
    ]
    workstation = (
        await db["workstations"].find_one(
            {
                "$or": [
                    {"id": {"$in": workstation_ids}},
                    {"workstation_id": {"$in": workstation_ids}},
                ]
            },
            {"_id": 0, "embedding": 0},
        )
        if workstation_ids
        else None
    )
    if not workstation and sop and sop.get("workstation_id"):
        workstation = await db["workstations"].find_one(
            {
                "$or": [
                    {"id": sop.get("workstation_id")},
                    {"workstation_id": sop.get("workstation_id")},
                ]
            },
            {"_id": 0, "embedding": 0},
        )
    if not workstation and stage_ids:
        workstation = await db["workstations"].find_one(
            {"stage_id": {"$in": stage_ids}}, {"_id": 0, "embedding": 0}
        )
    operator = None
    if workstation:
        operator = await db["users"].find_one(
            {
                "$or": [
                    {"user_id": workstation.get("operator_id")},
                    {"workstation_id": workstation.get("id")},
                ],
                "title": "Production Worker",
            },
            {"_id": 0, "embedding": 0},
        )
    if not operator and impact.get("qualified_operators"):
        operator = impact["qualified_operators"][0]
    return sop, workstation, operator


async def _approval_workflow(
    db, payload: dict, impact: dict
) -> tuple[list[dict], dict]:
    document_controller = await _ensure_system_approval_user(db, "document_controller")
    quality_control = await _ensure_system_approval_user(db, "quality_control")
    change_control = await _ensure_system_approval_user(db, "change_control")
    sop, workstation, operator = await _workstation_context_for_change(
        db, payload, impact
    )
    line_manager = await _approval_user_by_id(
        db, (workstation or {}).get("line_manager_id")
    )
    plant_manager = await _approval_user_by_id(
        db, (workstation or {}).get("site_manager_id")
    )

    context = {
        "sop_id": (sop or {}).get("id") or (sop or {}).get("sop_id"),
        "sop_title": (sop or {}).get("title") or (sop or {}).get("name"),
        "workstation_id": (workstation or {}).get("id"),
        "workstation_name": (workstation or {}).get("name"),
        "line_id": (workstation or {}).get("line_id"),
        "stage_id": (workstation or {}).get("stage_id"),
        "plant_id": (workstation or {}).get("plant_id") or (sop or {}).get("plant_id"),
    }
    steps = [
        (
            "Document Review",
            "Document Controller",
            document_controller,
            "document_control",
        ),
        ("Workstation Impact Review", "Production Worker", operator, "workstation"),
        (
            "Quality Control Review",
            "Quality Control",
            quality_control,
            "quality_control",
        ),
        ("Change Control Approval", "Change Control", change_control, "change_control"),
        ("Line Manager Approval", "Line Manager", line_manager, "line"),
        ("Plant Manager Approval", "Plant Manager", plant_manager, "plant"),
    ]
    workflow: list[dict] = []
    for sequence, (stage, required_role, user, source) in enumerate(steps, start=1):
        assigned_user = _approval_user_summary(user, required_role)
        workflow.append(
            {
                "sequence": sequence,
                "stage": stage,
                "step": stage,
                "status": "Pending",
                "role": required_role,
                "required_role": required_role,
                "required_roles": [required_role],
                "assigned_user": assigned_user,
                "assigned_user_id": assigned_user.get("user_id"),
                "assigned_user_name": assigned_user.get("name"),
                "assigned_user_title": assigned_user.get("title"),
                "minimum_approvals": 1,
                "routing": "sequential",
                "source": source,
                "context": context,
            }
        )
    assignment_basis = {
        "mode": "document_and_process_controls",
        "sop_id": context.get("sop_id"),
        "sop_title": context.get("sop_title"),
        "document_node_collection": "sops" if context.get("sop_id") else None,
        "document_node_id": context.get("sop_id"),
        "workstation_id": context.get("workstation_id"),
        "workstation_name": context.get("workstation_name"),
        "line_id": context.get("line_id"),
        "stage_id": context.get("stage_id"),
        "plant_id": context.get("plant_id"),
        "uses_actual_worker_nodes": True,
    }
    return workflow, assignment_basis


def _change_control_graph_relationships(
    resolved_entities: list[dict], workflow: list[dict], basis: dict
) -> list[dict]:
    relationships = [
        {
            "type": "AFFECTS",
            "target_collection": entity.get("collection"),
            "target_id": entity.get("entity_id"),
        }
        for entity in resolved_entities
        if entity.get("entity_id")
    ]
    relationships.extend(approval_graph_relationships(workflow, basis))
    return relationships


async def _build_change_control_record(db, payload: dict, current_user: dict) -> dict:
    now = utc_now()
    manual_chain = _manual_chain_from_payload(payload)
    existing = await _open_change_for_affected_entities(
        db, payload.get("affected_entities") or []
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Open change already exists for an affected entity: {existing.get('change_control_number') or existing.get('change_control_id')}",
        )
    resolved_entities = await _resolve_affected_entities(
        db, payload.get("affected_entities") or []
    )
    payload["affected_entities"] = resolved_entities
    impact = await _change_impact_assessment(db, resolved_entities)
    decisions = _change_decisions(payload, impact)
    sequence = await db[CHANGE_CONTROL_COLLECTION].count_documents({}) + 1
    record_id = f"change_control-{uuid4().hex}"
    approval_workflow, approval_assignment_basis = await resolve_named_approval_chain(
        db,
        source_collection=CHANGE_CONTROL_COLLECTION,
        source_id=record_id,
        source_document={**payload, "change_control_id": record_id},
        affected_entities=resolved_entities,
        current_user_id=_current_user_id(current_user),
    )
    if manual_chain:
        approval_workflow = await _resolve_manual_approval_chain(
            db, manual_chain, approval_assignment_basis
        )
        approval_assignment_basis = {
            **approval_assignment_basis,
            "mode": "manual_dialog_assignment",
            "manual_dialog_assignment": True,
            "auto_chain_available": True,
            "manual_chain_count": len(approval_workflow),
        }
    record = {
        "change_control_id": record_id,
        "change_control_number": _change_control_number(now, sequence),
        "standard": "GxP",
        "standards": compliance_readiness()["standards"],
        "standard_id": "ich-q10",
        **payload,
        **decisions,
        "initiating_date": now,
        "initiating_user": _current_user_id(current_user),
        "impact_assessment_generated": impact,
        "implementation_tasks": _implementation_tasks(payload, impact, decisions),
        "training_records": [
            {
                "training_record_id": f"training-{uuid4().hex}",
                "worker_id": operator.get("user_id"),
                "worker_name": operator.get("name"),
                "status": "Pending",
            }
            for operator in impact.get("qualified_operators", [])
        ],
        "approval_chain": approval_workflow,
        "approval_workflow": approval_workflow,
        "approval_process_definition": approval_workflow,
        "approval_assignment_basis": approval_assignment_basis,
        "approval_chain_resolved": True,
        "approval_chain_resolved_at": now,
        "electronic_signatures": [],
        "effectiveness_check": {
            "required": True,
            "window_days": payload.get("effectiveness_check_days")
            or (30 if payload.get("change_type") == "Emergency" else 14),
            "status": "Pending",
        },
        "graph_relationships": _change_control_graph_relationships(
            resolved_entities, approval_workflow, approval_assignment_basis
        ),
        "status": payload.get("status") or "Open",
        "created_at": now,
        "updated_at": now,
        "created_by": _current_user_id(current_user),
        "updated_by": _current_user_id(current_user),
    }
    record["record_hash"] = sha256_hex(record)
    return record


def _new_mfa_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _normalize_mfa_code(code: str) -> str:
    return "".join(
        character
        for character in code.strip().replace(" ", "").replace("-", "")
        if character.isalnum()
    )


def _totp(secret: str, counter: int, digits: int = 6) -> str:
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def _verify_totp(secret: str, code: str, window: int = 1) -> bool:
    normalized = _normalize_mfa_code(code)
    if not normalized.isdigit():
        return False
    counter = int(time.time() // 30)
    return any(
        hmac.compare_digest(_totp(secret, counter + drift), normalized)
        for drift in range(-window, window + 1)
    )


def _new_backup_codes(count: int = 10) -> list[str]:
    return [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(count)]


def _hash_backup_code(code: str) -> str:
    return hashlib.sha256(_normalize_mfa_code(code).lower().encode("utf-8")).hexdigest()


def _otpauth_uri(user: dict, secret: str) -> str:
    issuer = "Sentinel X"
    label = quote(f"{issuer}:{user.get('email') or user.get('user_id')}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def _escape_html(value) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


APPROVAL_ACCESS_COOKIE_NAME = "indus_approval_access"


def _approval_page_shell(title: str, body: str) -> str:
    return f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{_escape_html(title)}</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f6f8fb;
        --panel: #ffffff;
        --ink: #172033;
        --muted: #667085;
        --line: #d9e2ec;
        --field: #f9fbfd;
        --primary: #1457d9;
        --primary-dark: #0f3f9e;
        --danger: #b42318;
        --danger-bg: #fff1f0;
        --ok: #067647;
        --ok-bg: #ecfdf3;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        min-height: 100vh;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: var(--bg);
        color: var(--ink);
      }}
      .topbar {{
        height: 56px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0 24px;
        background: #ffffff;
        border-bottom: 1px solid var(--line);
      }}
      .brand {{
        font-weight: 700;
        letter-spacing: 0;
      }}
      .eyebrow {{
        color: var(--muted);
        font-size: 13px;
      }}
      main {{
        width: min(860px, calc(100vw - 32px));
        margin: 32px auto;
      }}
      .panel {{
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 12px 32px rgba(16, 24, 40, 0.08);
      }}
      .panel-header {{
        padding: 24px 28px 18px;
        border-bottom: 1px solid var(--line);
      }}
      h1 {{
        margin: 0;
        font-size: 24px;
        line-height: 1.25;
        letter-spacing: 0;
      }}
      .subtitle {{
        margin: 8px 0 0;
        color: var(--muted);
        font-size: 14px;
        line-height: 1.5;
      }}
      .content {{
        padding: 24px 28px 28px;
      }}
      .meta {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
        margin-bottom: 24px;
      }}
      .meta-item {{
        border: 1px solid var(--line);
        border-radius: 6px;
        padding: 12px;
        background: #fbfcfe;
      }}
      .meta-label {{
        display: block;
        color: var(--muted);
        font-size: 12px;
        margin-bottom: 4px;
      }}
      .meta-value {{
        display: block;
        font-size: 14px;
        font-weight: 650;
        overflow-wrap: anywhere;
      }}
      label {{
        display: block;
        margin: 14px 0 6px;
        font-size: 13px;
        font-weight: 650;
      }}
      input, select, textarea {{
        width: 100%;
        min-height: 40px;
        border: 1px solid #c7d2df;
        border-radius: 6px;
        background: var(--field);
        color: var(--ink);
        font: inherit;
        padding: 9px 11px;
      }}
      textarea {{ min-height: 112px; resize: vertical; }}
      input:focus, select:focus, textarea:focus {{
        outline: 2px solid rgba(20, 87, 217, 0.18);
        border-color: var(--primary);
        background: #ffffff;
      }}
      .actions {{
        display: flex;
        gap: 12px;
        align-items: center;
        justify-content: flex-end;
        margin-top: 22px;
      }}
      button {{
        min-height: 40px;
        border: 0;
        border-radius: 6px;
        background: var(--primary);
        color: #ffffff;
        font-weight: 700;
        padding: 10px 16px;
        cursor: pointer;
      }}
      button:hover {{ background: var(--primary-dark); }}
      .alert {{
        border-radius: 6px;
        padding: 12px 14px;
        margin-bottom: 18px;
        font-size: 14px;
      }}
      .alert-error {{
        color: var(--danger);
        background: var(--danger-bg);
        border: 1px solid #fecdca;
      }}
      .alert-ok {{
        color: var(--ok);
        background: var(--ok-bg);
        border: 1px solid #abefc6;
      }}
      .hint {{
        margin: 12px 0 0;
        color: var(--muted);
        font-size: 13px;
        line-height: 1.45;
      }}
      @media (max-width: 720px) {{
        .topbar {{ padding: 0 16px; }}
        main {{ margin: 18px auto; }}
        .panel-header, .content {{ padding-left: 18px; padding-right: 18px; }}
        .meta {{ grid-template-columns: 1fr; }}
        .actions {{ justify-content: stretch; }}
        button {{ width: 100%; }}
      }}
    </style>
  </head>
  <body>
    <header class="topbar">
      <div class="brand">Sentinel X</div>
      <div class="eyebrow">GxP Approval</div>
    </header>
    <main>{body}</main>
  </body>
</html>
"""


def _approval_meta_html(approval: dict) -> str:
    title = _escape_html(
        approval.get("source_title") or approval.get("source_id") or "Approval"
    )
    stage = _escape_html(approval.get("stage") or "Approval")
    approver = _escape_html(
        approval.get("assigned_user_name") or approval.get("assigned_user_id")
    )
    return f"""
      <div class="meta">
        <div class="meta-item">
          <span class="meta-label">Record</span>
          <span class="meta-value">{title}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Stage</span>
          <span class="meta-value">{stage}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Assigned Approver</span>
          <span class="meta-value">{approver}</span>
        </div>
      </div>
"""


def _approval_login_html(approval: dict, token: str, error: str | None = None) -> str:
    error_block = (
        f'<div class="alert alert-error">{_escape_html(error)}</div>' if error else ""
    )
    body = f"""
      <section class="panel">
        <div class="panel-header">
          <h1>Sign In To Review Approval</h1>
          <p class="subtitle">Authentication is required before this electronic approval can be opened.</p>
        </div>
        <div class="content">
          {error_block}
          {_approval_meta_html(approval)}
          <form method="post">
            <input type="hidden" name="token" value="{_escape_html(token)}" />
            <input type="hidden" name="action" value="login" />
            <label>Email</label>
            <input name="email" type="email" autocomplete="email" required />
            <label>Password</label>
            <input name="password" type="password" autocomplete="current-password" required />
            <p class="hint">Only the assigned approver can open and sign this approval request.</p>
            <div class="actions">
              <button type="submit">Sign In</button>
            </div>
          </form>
        </div>
      </section>
"""
    return _approval_page_shell("Sign In To Review Approval", body)


def _approval_form_html(
    approval: dict,
    token: str,
    error: str | None = None,
    current_user: dict | None = None,
) -> str:
    error_block = (
        f'<div class="alert alert-error">{_escape_html(error)}</div>' if error else ""
    )
    signed_in_as = _escape_html(
        (current_user or {}).get("email")
        or (current_user or {}).get("sub")
        or "Authenticated user"
    )
    body = f"""
      <section class="panel">
        <div class="panel-header">
          <h1>Approval Required</h1>
          <p class="subtitle">Review the controlled change request and submit an electronic decision.</p>
        </div>
        <div class="content">
          {error_block}
          {_approval_meta_html(approval)}
          <p class="hint">Signed in as {signed_in_as}</p>
          <form method="post">
            <input type="hidden" name="token" value="{_escape_html(token)}" />
            <input type="hidden" name="action" value="decide" />
            <label>Decision</label>
            <select name="decision" required>
              <option value="Approved">Approve</option>
              <option value="Rejected">Reject</option>
            </select>
            <label>Reason / Comment</label>
            <textarea name="reason" rows="5" placeholder="Required for rejection"></textarea>
            <label>Signature Meaning</label>
            <input name="meaning" value="I reviewed and approve/reject this approval request." />
            <div class="actions">
              <button type="submit">Submit Decision</button>
            </div>
          </form>
        </div>
      </section>
"""
    return _approval_page_shell("Approval Required", body)


def _approval_complete_html(approval: dict, decision: str) -> str:
    body = f"""
      <section class="panel">
        <div class="panel-header">
          <h1>Decision Submitted</h1>
          <p class="subtitle">The electronic approval decision has been recorded.</p>
        </div>
        <div class="content">
          <div class="alert alert-ok">{_escape_html(decision)} recorded for {_escape_html(approval.get("source_title") or approval.get("source_id"))}.</div>
          {_approval_meta_html(approval)}
        </div>
      </section>
"""
    return _approval_page_shell("Decision Submitted", body)


def _legacy_approval_form_html(
    approval: dict, token: str, error: str | None = None
) -> str:
    error_block = f"<p style='color:#b91c1c'>{_escape_html(error)}</p>" if error else ""
    title = _escape_html(
        approval.get("source_title") or approval.get("source_id") or "Approval"
    )
    stage = _escape_html(approval.get("stage") or "Approval")
    approver = _escape_html(
        approval.get("assigned_user_name") or approval.get("assigned_user_id")
    )
    return f"""
<!doctype html>
<html>
  <head><title>Approval Required</title></head>
  <body>
    {error_block}
    <p><strong>Record:</strong> {title}</p>
    <p><strong>Stage:</strong> {stage}</p>
    <p><strong>Approver:</strong> {approver}</p>
  </body>
</html>
"""


def _approval_user_matches(approval: dict, user: dict | None) -> bool:
    if not user:
        return False
    user_id = user.get("sub") or user.get("user_id")
    email = str(user.get("email") or "").strip().lower()
    assigned_id = str(approval.get("assigned_user_id") or "").strip()
    assigned_email = str(approval.get("assigned_user_email") or "").strip().lower()
    return bool(
        (assigned_id and user_id == assigned_id)
        or (assigned_email and email == assigned_email)
    )


def _approval_token_error(approval: dict | None, token: str) -> str | None:
    if not approval or approval.get("approval_token") != token:
        return "Invalid or expired approval link."
    terminal_status = (
        str(approval.get("status") or approval.get("decision") or "").strip().title()
    )
    if terminal_status in {"Approved", "Rejected"}:
        return "Approval has already been decided."
    expires_at = _as_aware_utc(approval.get("approval_token_expires_at"))
    if expires_at and expires_at <= utc_now():
        return "This approval link has expired. Request a new approval notification."
    return None


async def _approval_user_from_request(request: Request | None) -> dict | None:
    if request is None:
        return None
    auth_header = request.headers.get("authorization", "")
    token = None
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get(APPROVAL_ACCESS_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except JWTError:
        return None
    # Security audit P1-5: a GxP compliance approval must not be
    # signable/viewable with a token that's already been revoked (logout,
    # or an explicit revocation) — same live jti check as the main auth
    # chokepoint (services.common.auth.get_current_user).
    if await is_jti_revoked(payload.get("jti")):
        return None
    return payload


async def _approval_login_user(
    db, email: str | None, password: str | None
) -> dict | None:
    if not email or not password:
        return None
    result = await get_by_email(db, email)
    user = result[0] if result else None
    if not user or not user.get("is_active", False) or user.get("mfa_enabled"):
        return None
    candidate_hash = user.get("password")
    if not candidate_hash:
        return None
    try:
        if not verify_password(password, candidate_hash):
            return None
    except Exception:
        return None
    return user


def _set_approval_access_cookie(response: HTMLResponse, user: dict) -> None:
    token = create_access_token(
        user["user_id"],
        user.get("email") or "",
        user.get("role") or "",
        [],
        tenant=user_tenant(user),
    )
    response.set_cookie(
        key=APPROVAL_ACCESS_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


async def _issue_login_tokens(db, response: Response, user: dict) -> TokenResponse:
    # Prefer role_id (unambiguous) over role (a name, and by convention not
    # meant to double as an id) — falls back to role only for the handful of
    # legacy user documents that predate the role_id field. Passing the name
    # here let _get_permissions's get_role_by_id() silently match an unrelated
    # role document whose role_id happens to equal that same string, so
    # get_role_by_name() — the fallback that exists for exactly this case —
    # never ran, and the wrong role's permissions were resolved into the token.
    access = await _get_permissions(db, user.get("role_id") or user["role"])
    access_token = create_access_token(
        user["user_id"],
        user["email"],
        user["role"],
        access,
        tenant=user_tenant(user),
        mfa_status="satisfied" if user.get("mfa_enabled") else "not_satisfied",
    )
    refresh_token = create_refresh_token(user["user_id"])
    await save_refresh_token(refresh_token)
    response.set_cookie(value=refresh_token, **COOKIE_OPTIONS)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


async def _verify_mfa_or_backup_code(db, user: dict, code: str) -> bool:
    secret = user.get("mfa_secret")
    if secret and _verify_totp(secret, code):
        return True

    code_hash = _hash_backup_code(code)
    backup_hashes = list(user.get("mfa_backup_code_hashes") or [])
    if code_hash in backup_hashes:
        backup_hashes.remove(code_hash)
        await db["users"].update_one(
            {"user_id": user["user_id"]},
            {
                "$set": {
                    "mfa_backup_code_hashes": backup_hashes,
                    "mfa_backup_code_used_at": utc_now(),
                }
            },
        )
        return True
    return False


async def _get_permissions(db, role_id: str) -> list:
    """
    Look up a role by its ID and resolve its permissions.
    Raises HTTP 500 if the role document is missing.
    """
    role = await get_role_by_id(db, role_id)
    if role is None:
        role = await get_role_by_name(db, role_id)

    if role is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Role '{role_id}' not found — check that the roles collection is seeded",
        )

    permissions = await asyncio.gather(
        *[get_by_permission_id(db, p) for p in role["permissions"]]
    )
    return [
        p
        for sublist in permissions
        for p in (sublist if isinstance(sublist, list) else [sublist])
        if p
    ]


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------
@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def login(
    request: Request,
    response: Response,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    """
    Authenticate with email + password.
    Accepts both JSON body and form-urlencoded (OAuth2 compatibility).
    Returns a short-lived access token in the body and sets an
    httpOnly refresh token cookie.
    """
    content_type = request.headers.get("content-type", "")
    email = None
    pwd = None

    if "application/x-www-form-urlencoded" in content_type:
        form_data = await request.form()
        email = form_data.get("username") or form_data.get("email")
        pwd = form_data.get("password")
    elif "application/json" in content_type:
        try:
            body_data = await request.json()
            email = body_data.get("email")
            pwd = body_data.get("password")
        except Exception:
            pass

    if not email or not pwd:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Email and password are required",
        )

    result = await get_by_email(db, email)
    user = result[0] if result else None
    now = utc_now()
    locked_until = _as_aware_utc(user.get("locked_until") if user else None)
    if locked_until and locked_until > now:
        await _record_login_event(
            db,
            email=email,
            status_value="locked",
            request=request,
            user_id=user.get("user_id"),
            reason=f"Account locked until {locked_until.isoformat()}",
        )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED, detail="Account is locked"
        )
    if user and not user.get("is_active", False):
        await _record_login_event(
            db,
            email=email,
            status_value="failed",
            request=request,
            user_id=user.get("user_id"),
            reason="Inactive account",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive"
        )

    # Always run bcrypt to prevent timing-based user enumeration
    _DUMMY = (
        "$2b$12$eImiTXuWVxfM37uY4JANjQ=="  # invalid hash — compare will always fail
    )
    candidate_hash = user.get("password") if user else _DUMMY
    if not candidate_hash:
        candidate_hash = _DUMMY
    try:
        password_ok = verify_password(pwd, candidate_hash)
    except Exception:
        password_ok = False

    if not user or not password_ok:
        if user:
            failed_count = int(user.get("failed_login_count") or 0) + 1
            fields = {
                "failed_login_count": failed_count,
                "last_failed_login_at": now,
            }
            reason = "Invalid credentials"
            if failed_count >= settings.PART11_FAILED_LOGIN_LIMIT:
                locked_until = now + timedelta(minutes=settings.PART11_LOCKOUT_MINUTES)
                fields["locked_until"] = locked_until
                reason = f"Account locked after {failed_count} failed login attempts"
            await db["users"].update_one({"user_id": user["user_id"]}, {"$set": fields})
            await _record_login_event(
                db,
                email=email,
                status_value="failed",
                request=request,
                user_id=user.get("user_id"),
                reason=reason,
            )
        else:
            await _record_login_event(
                db,
                email=email,
                status_value="failed",
                request=request,
                reason="Unknown user",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    await db["users"].update_one(
        {"user_id": user["user_id"]},
        {"$set": {"failed_login_count": 0, "locked_until": None, "last_login_at": now}},
    )
    await _record_login_event(
        db,
        email=email,
        status_value="success",
        request=request,
        user_id=user.get("user_id"),
    )
    if user.get("mfa_enabled"):
        challenge_token = create_mfa_challenge_token(user["user_id"])
        await _record_login_event(
            db,
            email=email,
            status_value="mfa_required",
            request=request,
            user_id=user.get("user_id"),
        )
        return TokenResponse(mfa_required=True, mfa_challenge_token=challenge_token)

    return await _issue_login_tokens(db, response, user)


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------
@router.post("/refresh", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def refresh(
    response: Response,
    body: RefreshTokenRequest | None = None,
    refresh_token: str | None = Cookie(
        default=None, alias=settings.REFRESH_COOKIE_NAME
    ),
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    token = body.refresh_token if body and body.refresh_token else refresh_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Refresh token missing"
        )

    exists = await token_exists(token)
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Refresh token revoked"
        )

    try:
        payload = decode_refresh_token(token)
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    user = await get_by_id(db, payload["sub"])

    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User not found"
        )

    # See _issue_login_tokens for why role_id, not role, must be passed here.
    access = await _get_permissions(db, user.get("role_id") or user["role"])

    access_token = create_access_token(
        user["user_id"],
        user["email"],
        user["role"],
        access,
        tenant=user_tenant(user),
        mfa_status="satisfied",
    )
    refresh_token = create_refresh_token(user["user_id"])
    await save_refresh_token(refresh_token)
    response.set_cookie(value=refresh_token, **COOKIE_OPTIONS)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/mfa/verify", response_model=TokenResponse)
async def verify_mfa_challenge(
    body: MfaVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    try:
        payload = decode_mfa_challenge_token(body.mfa_challenge_token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    user = await get_by_id(db, payload["sub"])
    if not user or not user.get("mfa_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="MFA is not enabled"
        )

    if not await _verify_mfa_or_backup_code(db, user, body.code):
        await _record_login_event(
            db,
            email=user.get("email") or "",
            status_value="mfa_failed",
            request=request,
            user_id=user.get("user_id"),
            reason="Invalid MFA code",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA code"
        )

    await db["users"].update_one(
        {"user_id": user["user_id"]}, {"$set": {"mfa_last_verified_at": utc_now()}}
    )
    await _record_login_event(
        db,
        email=user.get("email") or "",
        status_value="mfa_success",
        request=request,
        user_id=user.get("user_id"),
    )
    return await _issue_login_tokens(db, response, user)


@router.post("/mfa/enroll", response_model=MfaEnrollResponse)
async def enroll_mfa(
    body: MfaEnrollRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    user_id = _current_user_id(current_user)
    user = await get_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    if not verify_password(body.password, user.get("password") or ""):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Password invalid"
        )

    secret = _new_mfa_secret()
    backup_codes = _new_backup_codes()
    await db["users"].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "mfa_secret": secret,
                "mfa_enabled": False,
                "mfa_enrolled_at": utc_now(),
                "mfa_backup_code_hashes": [
                    _hash_backup_code(code) for code in backup_codes
                ],
            }
        },
    )
    next_user = {**user, "mfa_secret": secret}
    return MfaEnrollResponse(
        secret=secret,
        otpauth_uri=_otpauth_uri(next_user, secret),
        backup_codes=backup_codes,
    )


@router.post("/mfa/enable")
async def enable_mfa(
    body: MfaEnableRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    user_id = _current_user_id(current_user)
    user = await get_by_id(db, user_id)
    if not user or not user.get("mfa_secret"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA enrollment is not started",
        )
    if not _verify_totp(user["mfa_secret"], body.code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA code"
        )
    await db["users"].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "mfa_enabled": True,
                "mfa_enabled_at": utc_now(),
                "mfa_last_verified_at": utc_now(),
            }
        },
    )
    return {"mfa_enabled": True}


@router.post("/mfa/disable")
async def disable_mfa(
    body: MfaDisableRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    user_id = _current_user_id(current_user)
    user = await get_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    if not verify_password(body.password, user.get("password") or ""):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Password invalid"
        )
    if user.get("mfa_enabled") and not await _verify_mfa_or_backup_code(
        db, user, body.code
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA code"
        )
    await db["users"].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "mfa_enabled": False,
                "mfa_disabled_at": utc_now(),
                "mfa_secret": None,
                "mfa_backup_code_hashes": [],
            }
        },
    )
    return {"mfa_enabled": False}


@router.get("/mfa/status")
async def mfa_status(
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    user = await get_by_id(db, _current_user_id(current_user))
    return {
        "mfa_enabled": bool(user and user.get("mfa_enabled")),
        "mfa_enrolled": bool(user and user.get("mfa_secret")),
        "backup_codes_remaining": (
            len(user.get("mfa_backup_code_hashes") or []) if user else 0
        ),
    }


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------
@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    response: Response,
    request: Request,
    refresh_token: str | None = Cookie(
        default=None, alias=settings.REFRESH_COOKIE_NAME
    ),
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    """
    Revoke the refresh token and clear the cookie.

    Security audit P1-5: also revokes the caller's own ACCESS token (by
    jti) if one was presented — previously logout only revoked the
    refresh token, leaving the short-lived access token that
    get_current_user/require_permission actually validate on every
    request usable until its natural expiry. The bearer token is
    optional here (unlike a normal protected route) so a client whose
    access token already expired can still log out via the refresh
    cookie alone.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        access_token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = decode_access_token(access_token)
        except JWTError:
            payload = None
        if payload and payload.get("jti"):
            await block_jti(payload["jti"])
    if refresh_token:
        await revoke_refresh_token(refresh_token)
    await _record_login_event(db, email="", status_value="logout", request=request)

    response.delete_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="strict",
    )
    return {"message": "Logged out"}


@router.post(
    "/electronic-signatures",
    response_model=ElectronicSignatureResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_electronic_signature(
    body: ElectronicSignatureRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    user_id = _current_user_id(current_user)
    user = await get_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )

    candidate_hash = user.get("password")
    if not candidate_hash or not verify_password(body.password, candidate_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Electronic signature password invalid",
        )

    record = await _find_signed_record(db, body.collection, body.record_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Record '{body.record_id}' not found in collection '{body.collection}'",
        )

    record_hash = signature_payload_hash(record)
    signed_at = utc_now()
    signer = {
        "user_id": user_id,
        "email": user.get("email") or current_user.get("email"),
        "role": user.get("role") or current_user.get("role"),
        "name": user.get("name"),
    }
    reauthentication = {
        "method": "password",
        "status": "verified",
        "verified_at": signed_at,
        "verified_user_id": user_id,
    }
    signature_payload = {
        "signature_id": f"esign-{uuid4().hex}",
        "signer": signer,
        "meaning": body.meaning,
        "timestamp": signed_at,
        "collection": body.collection,
        "record_id": body.record_id,
        "action": body.action,
        "record_hash": record_hash,
        "reauthentication": reauthentication,
    }
    signature_hash = signature_payload_hash(signature_payload)
    signature = {
        "signature_id": signature_payload["signature_id"],
        "standard": "21 CFR Part 11",
        "standards": compliance_readiness()["standards"],
        **compliance_metadata(),
        "user_id": user_id,
        "email": signer["email"],
        "role": signer["role"],
        "signer": signer,
        "collection": body.collection,
        "record_id": body.record_id,
        "action": body.action,
        "meaning": body.meaning,
        "record_hash": record_hash,
        "reauthentication": reauthentication,
        "signature_payload": signature_payload,
        "signature_hash": signature_hash,
        "signed_at": signed_at,
    }
    await db[SIGNATURE_COLLECTION].insert_one(signature)
    await _link_signature_to_entity(db, signature)
    return ElectronicSignatureResponse(
        signature_id=signature["signature_id"],
        user_id=signature["user_id"],
        collection=signature["collection"],
        record_id=signature["record_id"],
        action=signature["action"],
        meaning=signature["meaning"],
        record_hash=record_hash,
        signature_hash=signature_hash,
        signed_at=signed_at.isoformat(),
    )


@router.post("/audit-trail", status_code=status.HTTP_201_CREATED)
async def create_manual_audit_trail_entry(
    body: ManualAuditTrailRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    now = utc_now()
    payload = {
        "manual_audit_event": True,
        "reason": body.reason,
        "detail": body.detail,
        "created_at": now,
        "created_by": _current_user_id(current_user),
    }
    await append_audit_entry(
        db,
        collection=body.collection,
        action=body.action,
        record_id=body.record_id,
        after=payload,
        result={"source": "manual_audit_entry"},
    )
    entry = await db[AUDIT_COLLECTION].find_one(
        {
            "collection": body.collection,
            "record_id": body.record_id,
            "action": body.action,
        },
        {"_id": 0},
        sort=[("_id", -1)],
    )
    return _bson_safe(entry or payload)


@router.get("/audit-trail")
async def list_audit_trail(
    q: str | None = Query(default=None, min_length=1),
    collection: str | None = Query(default=None),
    record_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    elasticsearch_result = await _search_audit_trail_elasticsearch(
        q=q,
        collection=collection,
        record_id=record_id,
        page=page,
        page_size=page_size,
    )
    if elasticsearch_result is not None:
        return _bson_safe(elasticsearch_result)

    query: dict = {}
    if collection:
        query["collection"] = collection
    if record_id:
        query["record_id"] = record_id
    if q:
        query["$or"] = [
            {"audit_id": {"$regex": q, "$options": "i"}},
            {"collection": {"$regex": q, "$options": "i"}},
            {"record_id": {"$regex": q, "$options": "i"}},
            {"action": {"$regex": q, "$options": "i"}},
            {"change_reason": {"$regex": q, "$options": "i"}},
            {"actor.user_id": {"$regex": q, "$options": "i"}},
            {"actor.email": {"$regex": q, "$options": "i"}},
        ]
    total = await db[AUDIT_COLLECTION].count_documents(query)
    cursor = (
        db[AUDIT_COLLECTION]
        .find(query, {"_id": 0})
        .sort("_id", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return _bson_safe(
        {
            "total": total,
            "page": page,
            "page_size": page_size,
            "source": "mongodb",
            "results": [entry async for entry in cursor],
        }
    )


@router.get("/audit-trail/verify")
async def verify_audit_trail(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    cursor = db[AUDIT_COLLECTION].find({}).sort("_id", 1)
    previous_hash = None
    checked = 0
    async for entry in cursor:
        checked += 1
        stored_hash = entry.get("entry_hash")
        payload = {
            key: value
            for key, value in entry.items()
            if key not in {"_id", "entry_hash"}
        }
        expected_hash = sha256_hex(payload)
        if stored_hash != expected_hash or entry.get("previous_hash") != previous_hash:
            return {
                "valid": False,
                "checked": checked,
                "audit_id": entry.get("audit_id"),
                "reason": "Audit hash chain mismatch",
            }
        previous_hash = stored_hash
    return {"valid": True, "checked": checked, "last_hash": previous_hash}


@router.put("/retention-policies/{collection}")
async def upsert_retention_policy(
    collection: str,
    body: RetentionPolicyRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    now = utc_now()
    policy = {
        "collection": collection,
        "minimum_retention_days": body.minimum_retention_days,
        "allow_delete_before_retention_expiry": body.allow_delete_before_retention_expiry,
        "enabled": body.enabled,
        "reason": body.reason,
        "updated_at": now,
        "updated_by": _current_user_id(current_user),
    }
    policy.setdefault("created_at", now)
    await db[RETENTION_COLLECTION].update_one(
        {"collection": collection},
        {
            "$set": policy,
            "$setOnInsert": {
                "policy_id": f"retention-{uuid4().hex}",
                "created_at": now,
            },
        },
        upsert=True,
    )
    return policy


@router.get("/retention-policies")
async def list_retention_policies(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    cursor = db[RETENTION_COLLECTION].find({}, {"_id": 0}).sort("collection", 1)
    return [policy async for policy in cursor]


@router.get("/records/{collection}/{record_id}/compliance-bundle")
async def export_compliance_bundle(
    collection: str,
    record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    record = await _find_signed_record(db, collection, record_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Record not found"
        )

    audit_cursor = (
        db[AUDIT_COLLECTION]
        .find({"collection": collection, "record_id": record_id}, {"_id": 0})
        .sort("_id", 1)
    )
    version_cursor = (
        db[VERSION_COLLECTION]
        .find({"collection": collection, "record_id": record_id}, {"_id": 0})
        .sort("version_number", 1)
    )
    signature_cursor = (
        db[SIGNATURE_COLLECTION]
        .find({"collection": collection, "record_id": record_id}, {"_id": 0})
        .sort("signed_at", 1)
    )
    bundle = {
        "standard": "21 CFR Part 11",
        "standards": compliance_readiness()["standards"],
        "export_id": f"export-{uuid4().hex}",
        "exported_at": utc_now(),
        "exported_by": {
            "user_id": _current_user_id(current_user),
            "email": current_user.get("email"),
            "role": current_user.get("role"),
        },
        "collection": collection,
        "record_id": record_id,
        "record_hash": signature_payload_hash(record),
        "record": record,
        "audit_trail": [entry async for entry in audit_cursor],
        "versions": [entry async for entry in version_cursor],
        "electronic_signatures": [entry async for entry in signature_cursor],
        "change_controls": await db[CHANGE_CONTROL_COLLECTION]
        .find({"affected_entities.entity_id": record_id}, {"_id": 0})
        .sort("_id", -1)
        .to_list(length=100),
    }
    encoded = _bson_safe(bundle)
    encoded["bundle_hash"] = sha256_hex(encoded)
    await db[EXPORT_COLLECTION].insert_one(
        {
            "export_id": encoded["export_id"],
            "standard": "21 CFR Part 11",
            "standards": compliance_readiness()["standards"],
            "collection": collection,
            "record_id": record_id,
            "bundle_hash": encoded["bundle_hash"],
            "exported_at": utc_now(),
            "exported_by": encoded["exported_by"],
        }
    )
    return encoded


@router.get("/compliance/standards")
async def get_compliance_standards(
    _: dict = Depends(get_current_user),
):
    return compliance_readiness()


def _risk_level(rpn: int) -> str:
    if rpn >= 75:
        return "High"
    if rpn >= 35:
        return "Medium"
    return "Low"


def _compliance_proof_risk_rows() -> list[dict]:
    candidates = [
        {
            "title": "Unauthorized access to regulated records",
            "process": "Access Control",
            "hazard": "Unauthorized user can create, change, approve, or export regulated records.",
            "impact": "Data integrity and Part 11 control failure.",
            "severity": 5,
            "occurrence": 2,
            "detectability": 3,
            "mitigation": "RBAC, authenticated routes, MFA endpoints, electronic signatures, access review records.",
        },
        {
            "title": "Audit trail tampering",
            "process": "Audit Trail",
            "hazard": "Audit rows are changed or removed without detection.",
            "impact": "Loss of complete and trustworthy regulated history.",
            "severity": 5,
            "occurrence": 1,
            "detectability": 2,
            "mitigation": "Hash-chained audit entries, verification endpoint, server-side Mongo wrapper, Elasticsearch sync for searchability.",
        },
        {
            "title": "Electronic signature repudiation",
            "process": "Electronic Signatures",
            "hazard": "Signer can deny approval or signature meaning cannot be reconstructed.",
            "impact": "Approval chain and release records become non-compliant.",
            "severity": 4,
            "occurrence": 2,
            "detectability": 2,
            "mitigation": "Password re-authentication, signer identity, meaning, timestamp, record hash, and signature hash.",
        },
        {
            "title": "Record loss or stale local state",
            "process": "Record Persistence",
            "hazard": "Regulated UI state diverges from backend persistence or disappears after refresh.",
            "impact": "Incomplete or stale regulated records.",
            "severity": 4,
            "occurrence": 3,
            "detectability": 3,
            "mitigation": "Backend persistence, Redis-backed session state, no regulated localStorage persistence, compliance bundle export.",
        },
        {
            "title": "Incorrect GraphRAG answer used operationally",
            "process": "Compliant Intelligence",
            "hazard": "Unsupported answer is used as operational evidence.",
            "impact": "Wrong operational or quality decision.",
            "severity": 4,
            "occurrence": 3,
            "detectability": 4,
            "mitigation": "Source details, approved question set, explicit evidence gaps, no autonomous regulated record changes.",
        },
        {
            "title": "Canvas and graph relationships out of sync",
            "process": "Factory Ontology Canvas",
            "hazard": "Canvas links do not match graph links after edit or refresh.",
            "impact": "Incorrect material flow, audit, or workflow reasoning.",
            "severity": 3,
            "occurrence": 3,
            "detectability": 3,
            "mitigation": "Backend canvas/graph sync APIs, stable node IDs, canvas restore and propagation tests.",
        },
    ]
    rows: list[dict] = []
    for item in candidates:
        rpn = item["severity"] * item["occurrence"] * item["detectability"]
        rows.append(
            {
                **item,
                "risk_priority_number": rpn,
                "risk_level": _risk_level(rpn),
                "status": "Open" if rpn >= 35 else "Controlled",
                "disposition": (
                    "Requires QA disposition"
                    if rpn >= 35
                    else "Acceptable with listed controls"
                ),
            }
        )
    return rows


def _default_user_requirements() -> list[dict]:
    return [
        {
            "requirement_id": "URS-001",
            "title": "Unique user authentication",
            "description": "The system shall require unique authenticated users for regulated actions.",
            "category": "Access Control",
            "priority": "Critical",
            "source": "21 CFR Part 11 / User Requirement Specification",
            "acceptance_criteria": [
                "Login is required",
                "Refresh sessions are controlled",
                "User identity is captured in regulated records",
            ],
            "linked_controls": ["unique_user_identity", "role_based_access_control"],
            "linked_tests": [
                "tests/test_live_smoke_integration.py::test_oq_live_authenticated_api_smoke"
            ],
        },
        {
            "requirement_id": "URS-002",
            "title": "Electronic signature control",
            "description": "The system shall bind electronic signatures to signer identity, meaning, timestamp, record hash, and signed record.",
            "category": "Electronic Signatures",
            "priority": "Critical",
            "source": "21 CFR Part 11 / User Requirement Specification",
            "acceptance_criteria": [
                "Password re-authentication is required",
                "Signature hash is stored",
                "Record hash is linked",
            ],
            "linked_controls": [
                "electronic_signature_reauthentication",
                "signature_record_hash_linking",
            ],
            "linked_tests": [
                "tests/test_live_smoke_integration.py::test_pq_live_compliance_process_smoke"
            ],
        },
        {
            "requirement_id": "URS-003",
            "title": "Tamper-evident audit trail",
            "description": "The system shall create a complete, hash-chained audit trail for regulated create, update, delete, sign, export, and workflow actions.",
            "category": "Audit Trail",
            "priority": "Critical",
            "source": "21 CFR Part 11 / User Requirement Specification",
            "acceptance_criteria": [
                "Audit entries include actor/action/timestamp",
                "Audit hashes verify",
                "Before/after record hashes are retained",
            ],
            "linked_controls": [
                "hash_chained_audit_trail",
                "audit_trail_verification",
                "record_version_history",
            ],
            "linked_tests": [
                "tests/test_live_smoke_integration.py::test_pq_live_compliance_process_smoke"
            ],
        },
        {
            "requirement_id": "URS-004",
            "title": "Validation evidence and test execution",
            "description": "The system shall retain IQ, OQ, PQ, and compliant-intelligence execution evidence including failed tests.",
            "category": "Validation",
            "priority": "High",
            "source": "EU GMP Annex 11 / Annex 15 / User Requirement Specification",
            "acceptance_criteria": [
                "IQ/OQ/PQ reports are generated",
                "Failed tests are identified",
                "Evidence is hash recorded",
            ],
            "linked_controls": ["record_version_history", "audit_trail_verification"],
            "linked_tests": [
                "tests/test_live_smoke_integration.py::test_iq_live_service_health_smoke",
                "tests/test_live_smoke_integration.py::test_oq_live_authenticated_api_smoke",
                "tests/test_live_smoke_integration.py::test_pq_live_compliance_process_smoke",
                "tests/test_live_smoke_integration.py::test_gqa_live_agentos_smoke",
            ],
        },
        {
            "requirement_id": "URS-005",
            "title": "Traceability matrix",
            "description": "The system shall maintain traceability from user requirements to controls, risks, tests, and validation evidence.",
            "category": "Validation",
            "priority": "High",
            "source": "EU GMP Annex 15 / User Requirement Specification",
            "acceptance_criteria": [
                "Each requirement has coverage status",
                "Linked evidence is visible",
                "Coverage gaps are reported",
            ],
            "linked_controls": ["audit_trail_verification", "record_version_history"],
            "linked_tests": [
                "tests/test_live_smoke_integration.py::test_pq_live_compliance_process_smoke"
            ],
        },
        {
            "requirement_id": "URS-006",
            "title": "Compliant intelligence guardrails",
            "description": "The system shall answer only approved compliant-intelligence questions from local graph evidence unless explicit web search is requested.",
            "category": "Compliant Intelligence",
            "priority": "High",
            "source": "User Requirement Specification",
            "acceptance_criteria": [
                "Approved questions are routed locally",
                "Unsupported questions do not create regulated records",
                "Answers include deterministic evidence",
            ],
            "linked_controls": ["audit_trail_verification"],
            "linked_tests": [
                "tests/test_live_smoke_integration.py::test_gqa_live_agentos_smoke"
            ],
        },
    ]


def _requirement_input_to_row(requirement: UserRequirementInput) -> dict:
    row = requirement.model_dump(mode="python")
    row["requirement_id"] = (
        row.get("requirement_id") or f"URS-{uuid4().hex[:8].upper()}"
    )
    return row


def _assess_user_requirement(
    row: dict,
    available_controls: set[str],
    available_tests: set[str],
    evidence_refs: list[str],
) -> dict:
    linked_controls = list(row.get("linked_controls") or [])
    linked_tests = list(row.get("linked_tests") or [])
    control_hits = [item for item in linked_controls if item in available_controls]
    test_hits = [item for item in linked_tests if item in available_tests]
    gaps: list[str] = []
    if not linked_controls:
        gaps.append("No linked compliance control.")
    elif len(control_hits) != len(linked_controls):
        missing = sorted(set(linked_controls) - set(control_hits))
        gaps.append(f"Missing controls: {', '.join(missing)}")
    if not linked_tests:
        gaps.append("No linked verification test.")
    elif len(test_hits) != len(linked_tests):
        missing = sorted(set(linked_tests) - set(test_hits))
        gaps.append(f"Missing tests: {', '.join(missing)}")
    if not row.get("acceptance_criteria"):
        gaps.append("No acceptance criteria.")

    status_value = (
        "Satisfied"
        if not gaps
        else "Partially Covered" if control_hits or test_hits else "Gap"
    )
    coverage = {
        "controls_required": len(linked_controls),
        "controls_covered": len(control_hits),
        "tests_required": len(linked_tests),
        "tests_covered": len(test_hits),
        "evidence_refs": list(
            dict.fromkeys([*(row.get("linked_evidence") or []), *evidence_refs])
        ),
    }
    return {
        **row,
        "status": status_value,
        "coverage": coverage,
        "gaps": gaps,
    }


async def _latest_validation_evidence_refs(db) -> list[str]:
    records = (
        await db[VALIDATION_EVIDENCE_COLLECTION]
        .find({}, {"_id": 0, "validation_evidence_id": 1, "title": 1})
        .sort("_id", -1)
        .limit(20)
        .to_list(length=20)
    )
    refs: list[str] = []
    for record in records:
        refs.append(str(record.get("validation_evidence_id") or record.get("title")))
    return [item for item in refs if item]


async def _latest_risk_refs(db) -> list[str]:
    records = (
        await db[RISK_ASSESSMENT_COLLECTION]
        .find({}, {"_id": 0, "risk_assessment_id": 1, "title": 1})
        .sort("_id", -1)
        .limit(20)
        .to_list(length=20)
    )
    refs: list[str] = []
    for record in records:
        refs.append(str(record.get("risk_assessment_id") or record.get("title")))
    return [item for item in refs if item]


def _traceability_rows(
    requirements: list[dict], risk_refs: list[str], evidence_refs: list[str]
) -> list[dict]:
    rows: list[dict] = []
    for requirement in requirements:
        coverage = (
            requirement.get("coverage")
            if isinstance(requirement.get("coverage"), dict)
            else {}
        )
        rows.append(
            {
                "requirement_id": requirement.get("requirement_id"),
                "requirement": requirement.get("title"),
                "category": requirement.get("category"),
                "priority": requirement.get("priority"),
                "controls": requirement.get("linked_controls") or [],
                "risks": risk_refs,
                "tests": requirement.get("linked_tests") or [],
                "evidence": list(
                    dict.fromkeys(
                        [*(coverage.get("evidence_refs") or []), *evidence_refs]
                    )
                ),
                "status": requirement.get("status"),
                "gaps": requirement.get("gaps") or [],
            }
        )
    return rows


@router.post("/compliance/risk-assessments/run")
async def run_compliance_risk_assessment(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    assessed_at = utc_now()
    assessed_by = _current_user_id(current_user)
    rows = _compliance_proof_risk_rows()
    payload = {
        "risk_assessment_id": f"risk-assessment-{uuid4().hex}",
        "title": "Pharma Compliance Proof Risk Assessment",
        "standard_id": "ich-q9-r1",
        "status": (
            "Open" if any(row["risk_level"] != "Low" for row in rows) else "Controlled"
        ),
        "assessed_at": assessed_at,
        "assessed_by": assessed_by,
        "summary": {
            "total": len(rows),
            "high": sum(1 for row in rows if row["risk_level"] == "High"),
            "medium": sum(1 for row in rows if row["risk_level"] == "Medium"),
            "low": sum(1 for row in rows if row["risk_level"] == "Low"),
            "max_rpn": max(row["risk_priority_number"] for row in rows),
        },
        "rows": rows,
    }
    payload["record_hash"] = sha256_hex(payload)
    await db[RISK_ASSESSMENT_COLLECTION].insert_one(
        {
            **payload,
            "created_at": assessed_at,
            "updated_at": assessed_at,
            "created_by": assessed_by,
            "updated_by": assessed_by,
            "record_type": "risk_assessment_execution",
        }
    )
    return _bson_safe(payload)


@router.get("/compliance/risk-assessments/latest")
async def latest_compliance_risk_assessment(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await db[RISK_ASSESSMENT_COLLECTION].find_one(
        {"record_type": "risk_assessment_execution"},
        {"_id": 0},
        sort=[("_id", -1)],
    )
    return _bson_safe(record) if record else None


@router.post("/compliance/risk-assessments", status_code=status.HTTP_201_CREATED)
async def create_risk_assessment(
    body: RiskAssessmentRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    payload = body.model_dump(mode="python")
    payload["risk_priority_number"] = (
        body.severity * body.occurrence * body.detectability
    )
    payload["standard_id"] = "ich-q9-r1"
    return await _insert_gxp_record(
        db, RISK_ASSESSMENT_COLLECTION, "risk_assessment", payload, current_user
    )


@router.get("/compliance/risk-assessments")
async def list_risk_assessments(
    status_value: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await _list_gxp_records(
        db, RISK_ASSESSMENT_COLLECTION, page, page_size, status_value
    )


@router.post("/compliance/user-requirements/assess")
async def assess_user_requirements(
    body: UserRequirementsAssessmentRequest | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    assessed_at = utc_now()
    assessed_by = _current_user_id(current_user)
    request_body = body or UserRequirementsAssessmentRequest()
    source_rows = (
        [
            _requirement_input_to_row(requirement)
            for requirement in request_body.requirements
        ]
        if request_body.requirements
        else _default_user_requirements()
    )
    controls = set(compliance_readiness()["technical_controls"])
    available_tests = {
        "tests/test_live_smoke_integration.py::test_iq_live_service_health_smoke",
        "tests/test_live_smoke_integration.py::test_oq_live_authenticated_api_smoke",
        "tests/test_live_smoke_integration.py::test_pq_live_compliance_process_smoke",
        "tests/test_live_smoke_integration.py::test_gqa_live_agentos_smoke",
    }
    evidence_refs = await _latest_validation_evidence_refs(db)
    assessed_rows = [
        _assess_user_requirement(row, controls, available_tests, evidence_refs)
        for row in source_rows
    ]
    satisfied = sum(1 for row in assessed_rows if row["status"] == "Satisfied")
    partially_covered = sum(
        1 for row in assessed_rows if row["status"] == "Partially Covered"
    )
    gaps = sum(1 for row in assessed_rows if row["status"] == "Gap")
    summary = {
        "total": len(assessed_rows),
        "passed": satisfied,
        "failed": partially_covered + gaps,
        "satisfied": satisfied,
        "partially_covered": partially_covered,
        "gaps": gaps,
    }
    payload = {
        "assessment_id": f"urs-assessment-{uuid4().hex}",
        "title": "Pharma Compliance Proof User Requirements Assessment",
        "kind": "user-requirements-assessment",
        "scope": request_body.scope,
        "standard_id": "eu-gmp-annex-15",
        "status": "Passed" if summary["passed"] == summary["total"] else "Open",
        "assessed_at": assessed_at,
        "assessed_by": assessed_by,
        "summary": summary,
        "rows": assessed_rows,
        "requirements": assessed_rows,
    }
    payload["record_hash"] = sha256_hex(payload)
    await db[USER_REQUIREMENT_COLLECTION].insert_one(
        {
            **payload,
            "created_at": assessed_at,
            "updated_at": assessed_at,
            "created_by": assessed_by,
            "updated_by": assessed_by,
            "record_type": "user_requirement_assessment",
        }
    )
    return _bson_safe(payload)


@router.get("/compliance/user-requirements/latest")
async def latest_user_requirements_assessment(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await db[USER_REQUIREMENT_COLLECTION].find_one(
        {"record_type": "user_requirement_assessment"},
        {"_id": 0},
        sort=[("_id", -1)],
    )
    return _bson_safe(record) if record else None


@router.get("/compliance/user-requirements")
async def list_user_requirements(
    status_value: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await _list_gxp_records(
        db, USER_REQUIREMENT_COLLECTION, page, page_size, status_value
    )


@router.post("/compliance/traceability-matrix/run")
async def run_traceability_matrix(
    body: TraceabilityMatrixRequest | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    generated_at = utc_now()
    generated_by = _current_user_id(current_user)
    request_body = body or TraceabilityMatrixRequest()
    latest_assessment = await db[USER_REQUIREMENT_COLLECTION].find_one(
        {"scope": request_body.scope},
        {"_id": 0},
        sort=[("_id", -1)],
    )
    requirements = list((latest_assessment or {}).get("requirements") or [])
    if request_body.requirement_ids:
        requested_ids = set(request_body.requirement_ids)
        requirements = [
            row for row in requirements if row.get("requirement_id") in requested_ids
        ]
    if not requirements:
        assessment_payload = await assess_user_requirements(
            UserRequirementsAssessmentRequest(scope=request_body.scope),
            db=db,
            current_user=current_user,
        )
        requirements = list(assessment_payload.get("requirements") or [])
        if request_body.requirement_ids:
            requested_ids = set(request_body.requirement_ids)
            requirements = [
                row
                for row in requirements
                if row.get("requirement_id") in requested_ids
            ]

    risk_refs = await _latest_risk_refs(db)
    evidence_refs = await _latest_validation_evidence_refs(db)
    rows = _traceability_rows(requirements, risk_refs, evidence_refs)
    summary = {
        "total_requirements": len(rows),
        "satisfied": sum(1 for row in rows if row["status"] == "Satisfied"),
        "partially_covered": sum(
            1 for row in rows if row["status"] == "Partially Covered"
        ),
        "gaps": sum(1 for row in rows if row["status"] == "Gap"),
        "coverage_percent": (
            round(
                (sum(1 for row in rows if row["status"] == "Satisfied") / len(rows))
                * 100,
                2,
            )
            if rows
            else 0
        ),
    }
    payload = {
        "traceability_matrix_id": f"traceability-matrix-{uuid4().hex}",
        "assessment_id": f"traceability-matrix-assessment-{uuid4().hex}",
        "title": "Pharma Compliance Proof Traceability Matrix Assessment",
        "kind": "traceability-matrix-assessment",
        "scope": request_body.scope,
        "standard_id": "eu-gmp-annex-15",
        "status": "Passed" if rows and summary["gaps"] == 0 else "Open",
        "generated_at": generated_at,
        "generated_by": generated_by,
        "assessed_at": generated_at,
        "assessed_by": generated_by,
        "summary": {
            **summary,
            "total": summary["total_requirements"],
            "passed": summary["satisfied"],
            "failed": summary["partially_covered"] + summary["gaps"],
        },
        "rows": rows,
    }
    payload["record_hash"] = sha256_hex(payload)
    await db[TRACEABILITY_MATRIX_COLLECTION].insert_one(
        {
            **payload,
            "created_at": generated_at,
            "updated_at": generated_at,
            "created_by": generated_by,
            "updated_by": generated_by,
            "record_type": "traceability_matrix",
        }
    )
    return _bson_safe(payload)


@router.post("/compliance/traceability-matrix/assess")
async def assess_traceability_matrix(
    body: TraceabilityMatrixRequest | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    return await run_traceability_matrix(body=body, db=db, current_user=current_user)


@router.get("/compliance/traceability-matrix/latest")
async def latest_traceability_matrix_assessment(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await db[TRACEABILITY_MATRIX_COLLECTION].find_one(
        {"record_type": "traceability_matrix"},
        {"_id": 0},
        sort=[("_id", -1)],
    )
    return _bson_safe(record) if record else None


@router.get("/compliance/traceability-matrix")
async def list_traceability_matrices(
    status_value: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await _list_gxp_records(
        db, TRACEABILITY_MATRIX_COLLECTION, page, page_size, status_value
    )


@router.post("/compliance/validation-evidence", status_code=status.HTTP_201_CREATED)
async def create_validation_evidence(
    body: GxpEvidenceRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    payload = body.model_dump(mode="python")
    payload["standard_id"] = payload.get("standard_id") or "eu-gmp-annex-11"
    return await _insert_gxp_record(
        db, VALIDATION_EVIDENCE_COLLECTION, "validation_evidence", payload, current_user
    )


@router.get("/compliance/validation-evidence")
async def list_validation_evidence(
    status_value: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await _list_gxp_records(
        db, VALIDATION_EVIDENCE_COLLECTION, page, page_size, status_value
    )


COMPLIANCE_TEST_SCOPE_SELECTORS = {
    "iq": "tests/test_live_smoke_integration.py::test_iq_live_service_health_smoke",
    "oq": "tests/test_live_smoke_integration.py::test_oq_live_authenticated_api_smoke",
    "pq": "tests/test_live_smoke_integration.py::test_pq_live_compliance_process_smoke",
    "gqa": "tests/test_live_smoke_integration.py::test_gqa_live_agentos_smoke",
    "test-execution-reports": "tests/test_live_smoke_integration.py",
    "all": "tests/test_live_smoke_integration.py",
}


def _compliance_test_command(scope: str) -> list[str]:
    selector = (
        COMPLIANCE_TEST_SCOPE_SELECTORS.get(scope)
        or COMPLIANCE_TEST_SCOPE_SELECTORS["all"]
    )
    if selector.startswith("tests/"):
        targets = selector.split()
    else:
        targets = [f"tests/test_pharma_compliance_proof_validation.py::{selector}"]
    return [sys.executable, "-m", "pytest", "-vv", "--no-cov", *targets]


def _test_report_name(test_id: str) -> str:
    lowered = test_id.lower()
    if (
        "test_iq_" in lowered
        or "empty_table" in lowered
        or "seeded_database" in lowered
        or "seed_static" in lowered
    ):
        return "IQ Execution Report"
    if (
        "test_oq_" in lowered
        or "auth" in lowered
        or "audit" in lowered
        or "change_control" in lowered
        or "workflow_canvas" in lowered
        or "download" in lowered
    ):
        return "OQ Execution Report"
    if (
        "test_pq_" in lowered
        or "material_flow" in lowered
        or "ahmedabad" in lowered
        or "canvas" in lowered
        or "event_flow" in lowered
    ):
        return "PQ Execution Report"
    if "test_gqa_" in lowered or "agentos" in lowered or "llm_judge" in lowered:
        return "Compliant Intelligence Execution Report"
    return "OQ Execution Report"


def _parse_pytest_results(output: str) -> list[dict]:
    results: list[dict] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"^(tests/[^\s]+?)\s+(PASSED|FAILED|SKIPPED|ERROR|XFAILED|XPASSED)\b",
        re.MULTILINE,
    )
    for match in pattern.finditer(output):
        test_id = match.group(1).strip()
        status_value = match.group(2).strip().lower()
        if test_id in seen:
            continue
        seen.add(test_id)
        results.append(
            {
                "id": test_id,
                "report": _test_report_name(test_id),
                "status": (
                    "Passed"
                    if status_value == "passed"
                    else status_value.replace("x", "x-").title()
                ),
                "evidence": f"pytest {status_value}: {test_id}",
            }
        )
    return results


def _summarize_pytest(output: str, returncode: int, results: list[dict]) -> dict:
    passed = sum(1 for item in results if item["status"] == "Passed")
    failed = sum(1 for item in results if item["status"] in {"Failed", "Error"})
    skipped = sum(1 for item in results if item["status"] == "Skipped")
    summary_matches = re.findall(r"=+\s*(.+?)\s*=+\s*$", output.strip(), re.MULTILINE)
    return {
        "status": "Passed" if returncode == 0 else "Failed",
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": len(results),
        "summary": (
            summary_matches[-1]
            if summary_matches
            else ("pytest completed" if returncode == 0 else "pytest failed")
        ),
    }


def _failed_test_rows(rows: list[dict]) -> list[dict]:
    return [item for item in rows if item["status"] not in {"Passed", "Skipped"}]


def _report_evidence(
    report_name: str, rows: list[dict], executed_at: datetime, executed_by: str
) -> str:
    passed = sum(1 for item in rows if item["status"] == "Passed")
    failed_rows = _failed_test_rows(rows)
    skipped = sum(1 for item in rows if item["status"] == "Skipped")
    evidence = (
        f"Executed {executed_at.strftime('%d/%m/%Y, %H:%M:%S')} by {executed_by}; "
        f"passed {passed}, failed {len(failed_rows)}, skipped {skipped}"
    )
    if failed_rows:
        failed_ids = "; ".join(
            str(item.get("id") or "unknown test") for item in failed_rows
        )
        evidence = f"{evidence}; failed tests: {failed_ids}"
    return evidence


def _compliance_execution_reports(
    results: list[dict], executed_at: datetime, executed_by: str
) -> list[dict]:
    reports: list[dict] = []
    for report_name in [
        "IQ Execution Report",
        "OQ Execution Report",
        "PQ Execution Report",
        "Compliant Intelligence Execution Report",
    ]:
        rows = [item for item in results if item["report"] == report_name]
        if not rows:
            continue
        failed_tests = _failed_test_rows(rows)
        reports.append(
            {
                "report": report_name,
                "status": "Passed" if not failed_tests else "Failed",
                "passed": sum(1 for item in rows if item["status"] == "Passed"),
                "failed": len(failed_tests),
                "skipped": sum(1 for item in rows if item["status"] == "Skipped"),
                "total": len(rows),
                "scope": f"{len(rows)} integration tests",
                "evidence": _report_evidence(
                    report_name, rows, executed_at, executed_by
                ),
                "failed_tests": failed_tests,
                "tests": rows,
            }
        )
    return reports


def _compliance_agent_scope_summary(scope: str, execution: dict) -> dict:
    summary = execution.get("summary") or {}
    failed_tests: list[dict] = []
    for report in execution.get("reports") or []:
        failed_tests.extend(report.get("failed_tests") or [])
    return {
        "scope": scope,
        "status": summary.get("status") or "Unknown",
        "passed": int(summary.get("passed") or 0),
        "failed": int(summary.get("failed") or 0),
        "skipped": int(summary.get("skipped") or 0),
        "total": int(summary.get("total") or 0),
        "evidence": summary.get("summary") or execution.get("raw_output_tail") or "",
        "execution_id": execution.get("execution_id"),
        "failed_tests": failed_tests,
        "validation_evidence_id": execution.get("execution_id"),
    }


def _compliance_agent_reports(scope_results: list[dict]) -> list[dict]:
    report_names = {
        "iq": "IQ Execution Report",
        "oq": "OQ Execution Report",
        "pq": "PQ Execution Report",
        "gqa": "Compliant Intelligence Execution Report",
    }
    return [
        {
            "report": report_names.get(
                item["scope"], f"{item['scope'].upper()} Execution Report"
            ),
            "scope": item["scope"],
            "status": item["status"],
            "passed": item["passed"],
            "failed": item["failed"],
            "skipped": item["skipped"],
            "total": item["total"],
            "evidence": item["evidence"],
            "execution_id": item["execution_id"],
            "validation_evidence_id": item["validation_evidence_id"],
            "failed_tests": item["failed_tests"],
            "tests": item["failed_tests"],
        }
        for item in scope_results
    ]


def _validate_compliance_agent_scopes(scopes: list[str]) -> list[str]:
    normalized = [str(scope).strip().lower() for scope in scopes if str(scope).strip()]
    if not normalized:
        normalized = ["iq", "oq", "pq", "gqa"]
    allowed = {"iq", "oq", "pq", "gqa"}
    invalid = [scope for scope in normalized if scope not in allowed]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported compliance agent scope(s): {', '.join(invalid)}",
        )
    return normalized


@router.post("/compliance/test-execution/run")
async def run_compliance_test_execution(
    body: ComplianceTestExecutionRequest | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    repo_root = os.environ.get("MONKEYPATCHED_REPO_ROOT") or str(
        Path(__file__).resolve().parents[3]
    )
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", repo_root)
    env.setdefault("RUN_LIVE_SMOKE", "1")
    scope = body.scope if body else "all"
    cmd = _compliance_test_command(scope)
    try:
        process = await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = f"{exc.stdout or ''}\n{exc.stderr or ''}".strip()
        results = _parse_pytest_results(output)
        summary = _summarize_pytest(output, 124, results)
        summary["status"] = "Timed Out"
        summary["summary"] = "pytest timed out after 900 seconds"
    else:
        output = f"{process.stdout}\n{process.stderr}".strip()
        results = _parse_pytest_results(output)
        summary = _summarize_pytest(output, process.returncode, results)

    executed_at = utc_now()
    executed_by = _current_user_id(current_user)
    reports = _compliance_execution_reports(results, executed_at, executed_by)

    payload = {
        "execution_id": f"test-exec-{uuid4().hex}",
        "executed_at": executed_at,
        "executed_by": executed_by,
        "command": " ".join(cmd),
        "scope": scope,
        "summary": summary,
        "reports": reports,
        "raw_output_tail": output[-12000:],
    }
    await db[VALIDATION_EVIDENCE_COLLECTION].insert_one(
        {
            "validation_evidence_id": payload["execution_id"],
            "title": "Compliance Integration Test Suite Execution",
            "description": summary["summary"],
            "standard_id": "eu-gmp-annex-11",
            "status": summary["status"],
            "evidence_type": "test_execution_report",
            "record_hash": sha256_hex(payload),
            "created_at": executed_at,
            "updated_at": executed_at,
            "created_by": payload["executed_by"],
            "updated_by": payload["executed_by"],
            "payload": payload,
        }
    )
    return _bson_safe(payload)


@router.post("/compliance/agent/run")
async def run_compliance_agent(
    body: ComplianceAgentRunRequest | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    request_body = body or ComplianceAgentRunRequest()
    scopes = _validate_compliance_agent_scopes(request_body.scopes)
    started_at = utc_now()
    executed_by = _current_user_id(current_user)
    agent_run_id = f"compliance-agent-run-{uuid4().hex}"
    scope_results: list[dict] = []
    failed_scope: str | None = None
    failed_tests: list[dict] = []

    for scope in scopes:
        execution = await run_compliance_test_execution(
            body=ComplianceTestExecutionRequest(scope=scope),
            db=db,
            current_user=current_user,
        )
        scope_summary = _compliance_agent_scope_summary(scope, execution)
        scope_results.append(scope_summary)
        if scope_summary["status"] != "Passed" or scope_summary["failed"] > 0:
            failed_scope = scope
            failed_tests = list(scope_summary["failed_tests"])
            if not request_body.continue_on_failure:
                break

    all_scopes_passed = len(scope_results) == len(scopes) and all(
        item["status"] == "Passed" and item["failed"] == 0 for item in scope_results
    )
    urs_assessment = None
    traceability_matrix = None
    if all_scopes_passed:
        urs_assessment = await assess_user_requirements(
            UserRequirementsAssessmentRequest(),
            db=db,
            current_user=current_user,
        )
        traceability_matrix = await run_traceability_matrix(
            TraceabilityMatrixRequest(),
            db=db,
            current_user=current_user,
        )

    completed_at = utc_now()
    summary = {
        "total_scopes": len(scopes),
        "executed_scopes": len(scope_results),
        "passed_scopes": sum(
            1
            for item in scope_results
            if item["status"] == "Passed" and item["failed"] == 0
        ),
        "failed_scopes": sum(
            1
            for item in scope_results
            if item["status"] != "Passed" or item["failed"] > 0
        ),
        "passed": sum(int(item["passed"]) for item in scope_results),
        "failed": sum(int(item["failed"]) for item in scope_results),
        "skipped": sum(int(item["skipped"]) for item in scope_results),
        "total": sum(int(item["total"]) for item in scope_results),
        "status": "Passed" if all_scopes_passed else "Failed",
        "summary": (
            "All compliance scopes passed"
            if all_scopes_passed
            else f"{failed_scope or 'One or more compliance scopes'} failed"
        ),
        "failed_scope": failed_scope,
        "failed_tests": failed_tests,
        "continue_on_failure": request_body.continue_on_failure,
    }
    payload = {
        "agent_run_id": agent_run_id,
        "execution_id": agent_run_id,
        "title": "Compliance Agent Sequential Test Execution",
        "evidence_type": "compliance_agent_run",
        "scope": "agent",
        "status": summary["status"],
        "started_at": started_at,
        "completed_at": completed_at,
        "executed_by": executed_by,
        "scopes": scopes,
        "summary": summary,
        "scope_results": scope_results,
        "reports": _compliance_agent_reports(scope_results),
        "user_requirements_assessment_id": (urs_assessment or {}).get("assessment_id"),
        "traceability_matrix_id": (traceability_matrix or {}).get(
            "traceability_matrix_id"
        ),
        "notification": {
            "status": summary["status"],
            "message": (
                "Passed: all compliance scopes passed"
                if all_scopes_passed
                else f"Failed: {failed_scope or 'one or more scopes'} failed"
            ),
        },
    }
    payload["record_hash"] = sha256_hex(payload)
    await db[VALIDATION_EVIDENCE_COLLECTION].insert_one(
        {
            "validation_evidence_id": agent_run_id,
            "title": payload["title"],
            "description": payload["notification"]["message"],
            "standard_id": "eu-gmp-annex-11",
            "status": payload["status"],
            "evidence_type": "compliance_agent_run",
            "record_hash": payload["record_hash"],
            "created_at": completed_at,
            "updated_at": completed_at,
            "created_by": executed_by,
            "updated_by": executed_by,
            "payload": payload,
        }
    )
    return _bson_safe(payload)


@router.post("/compliance/agent/report")
async def save_compliance_agent_report(
    body: ComplianceAgentReportRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    completed_at = utc_now()
    executed_by = _current_user_id(current_user)
    report_id = f"compliance-agent-report-{uuid4().hex}"
    failed_tests = list(body.failed_tests or body.summary.get("failed_tests") or [])
    reports = list(body.reports or [])
    summary = dict(body.summary or {})
    if reports and not {"passed", "failed", "skipped", "total", "summary"}.issubset(
        summary.keys()
    ):
        summary.update(
            {
                "passed": sum(int(report.get("passed") or 0) for report in reports),
                "failed": sum(int(report.get("failed") or 0) for report in reports),
                "skipped": sum(int(report.get("skipped") or 0) for report in reports),
                "total": sum(int(report.get("total") or 0) for report in reports),
                "summary": body.message
                or f"{summary.get('passed_scopes', 0)} of {summary.get('total_scopes', 0)} compliance scopes passed",
            }
        )
    else:
        summary.setdefault("passed", int(summary.get("passed_scopes") or 0))
        summary.setdefault(
            "failed", int(summary.get("failed_scopes") or len(failed_tests))
        )
        summary.setdefault("skipped", 0)
        summary.setdefault(
            "total",
            int(
                summary.get("total_scopes")
                or summary.get("passed", 0) + summary.get("failed", 0)
            ),
        )
        summary.setdefault(
            "summary",
            body.message
            or f"{summary.get('passed_scopes', summary.get('passed', 0))} of {summary.get('total_scopes', summary.get('total', 0))} compliance scopes passed",
        )
    summary.setdefault(
        "status",
        "Failed" if failed_tests or int(summary.get("failed") or 0) > 0 else "Passed",
    )
    payload = {
        "agent_run_id": report_id,
        "execution_id": report_id,
        "title": "n8n Compliance Agent Report",
        "evidence_type": "compliance_agent_run",
        "scope": "agent",
        "status": summary["status"],
        "started_at": body.raw_response.get("started_at"),
        "completed_at": completed_at,
        "executed_at": completed_at,
        "executed_by": executed_by,
        "source": body.source,
        "summary": summary,
        "scope_results": body.scope_results,
        "reports": reports,
        "failed_tests": failed_tests,
        "raw_response": body.raw_response or body.model_dump(mode="python"),
    }
    payload["record_hash"] = sha256_hex(payload)
    await db[VALIDATION_EVIDENCE_COLLECTION].insert_one(
        {
            "validation_evidence_id": report_id,
            "title": payload["title"],
            "description": body.message
            or f"n8n compliance agent report: {payload['status']}",
            "standard_id": "eu-gmp-annex-11",
            "status": payload["status"],
            "evidence_type": "compliance_agent_run",
            "record_hash": payload["record_hash"],
            "created_at": completed_at,
            "updated_at": completed_at,
            "created_by": executed_by,
            "updated_by": executed_by,
            "payload": payload,
        }
    )
    return _bson_safe(payload)


@router.post("/compliance/agent/n8n/run")
async def run_compliance_agent_via_n8n(
    body: ComplianceAgentRunRequest | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    webhook_url = str(settings.N8N_COMPLIANCE_REPORT_WEBHOOK_URL or "").strip()
    if not webhook_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="N8N_COMPLIANCE_REPORT_WEBHOOK_URL is not configured",
        )
    request_body = body or ComplianceAgentRunRequest()
    payload = {
        "message": "Run compliance report",
        "scopes": _validate_compliance_agent_scopes(request_body.scopes),
        "continue_on_failure": request_body.continue_on_failure,
        "source": "sentinel-x-chat",
    }
    async with httpx.AsyncClient(timeout=900.0) as client:
        response = await client.post(
            webhook_url, json=payload, headers=n8n_webhook_auth_headers()
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"n8n compliance webhook returned HTTP {response.status_code}",
        )
    try:
        n8n_payload = response.json()
    except ValueError:
        n8n_payload = {"message": response.text}
    report = await save_compliance_agent_report(
        ComplianceAgentReportRequest(
            summary=n8n_payload.get("summary") or {},
            reports=n8n_payload.get("reports") or [],
            failed_tests=n8n_payload.get("failed_tests")
            or (n8n_payload.get("summary") or {}).get("failed_tests")
            or [],
            scope_results=n8n_payload.get("scope_results") or [],
            message=n8n_payload.get("message") or "n8n compliance report returned",
            source="n8n",
            raw_response=n8n_payload,
        ),
        db=db,
        current_user=current_user,
    )
    return report


@router.get("/compliance/test-execution/latest")
async def latest_compliance_test_execution(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await db[VALIDATION_EVIDENCE_COLLECTION].find_one(
        {
            "evidence_type": {"$in": ["test_execution_report", "compliance_agent_run"]},
            "payload": {"$exists": True},
        },
        {"_id": 0, "payload": 1},
        sort=[("_id", -1)],
    )
    payload = (record or {}).get("payload")
    return _bson_safe(payload) if payload else None


@router.post("/compliance/change-controls/approval-chain/preview")
async def preview_change_control_approval_chain(
    body: ChangeControlRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    payload = body.model_dump(mode="python")
    manual_chain = _manual_chain_from_payload(payload)
    resolved_entities = await _resolve_affected_entities(
        db, payload.get("affected_entities") or []
    )
    preview_id = (
        payload.get("change_control_id") or f"change_control-preview-{uuid4().hex}"
    )
    auto_chain, basis = await resolve_named_approval_chain(
        db,
        source_collection=CHANGE_CONTROL_COLLECTION,
        source_id=preview_id,
        source_document={
            **payload,
            "change_control_id": preview_id,
            "affected_entities": resolved_entities,
        },
        affected_entities=resolved_entities,
        current_user_id=_current_user_id(current_user),
    )
    selected_chain = auto_chain
    assignment_basis = basis
    mode = "auto_graph_resolution"
    if manual_chain:
        selected_chain = await _resolve_manual_approval_chain(db, manual_chain, basis)
        assignment_basis = {
            **basis,
            "mode": "manual_dialog_assignment",
            "manual_dialog_assignment": True,
            "auto_chain_available": True,
            "manual_chain_count": len(selected_chain),
        }
        mode = "manual_dialog_assignment"
    return _bson_safe(
        {
            "mode": mode,
            "approval_chain": selected_chain,
            "auto_approval_chain": auto_chain,
            "approval_assignment_basis": assignment_basis,
            "affected_entities": resolved_entities,
            "dialog_contract": {
                "submit_field": "approval_chain",
                "required_step_fields": ["stage", "role", "assigned_user_id"],
                "accepted_worker_fields": [
                    "assigned_user_id",
                    "assigned_user_name",
                    "assigned_user_email",
                ],
            },
        }
    )


@router.get("/compliance/approvers/typeahead")
async def approver_typeahead(
    q: str = Query(default="", max_length=120),
    plant_id: str | None = Query(default=None, max_length=128),
    line_id: str | None = Query(default=None, max_length=128),
    stage_id: str | None = Query(default=None, max_length=128),
    workstation_id: str | None = Query(default=None, max_length=128),
    role: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    line_id = line_id if isinstance(line_id, str) else None
    stage_id = stage_id if isinstance(stage_id, str) else None
    workstation_id = workstation_id if isinstance(workstation_id, str) else None
    role = role if isinstance(role, str) else None
    limit = limit if isinstance(limit, int) else 20
    text = q.strip()
    filters: list[dict] = [
        {
            "$or": [
                {"is_active": {"$ne": False}},
                {"is_active": {"$exists": False}},
            ]
        }
    ]
    if text:
        pattern = re.escape(text).replace("\\ ", ".*")
        filters.append(
            {
                "$or": [
                    {"user_id": {"$regex": pattern, "$options": "i"}},
                    {"employee_id": {"$regex": pattern, "$options": "i"}},
                    {"name": {"$regex": pattern, "$options": "i"}},
                    {"email": {"$regex": pattern, "$options": "i"}},
                    {"title": {"$regex": pattern, "$options": "i"}},
                    {"role": {"$regex": pattern, "$options": "i"}},
                ]
            }
        )
    if role:
        role_pattern = re.escape(role.strip()).replace("\\ ", ".*")
        filters.append(
            {
                "$or": [
                    {"role": {"$regex": role_pattern, "$options": "i"}},
                    {"title": {"$regex": role_pattern, "$options": "i"}},
                ]
            }
        )
    context_or = []
    if workstation_id:
        context_or.append({"workstation_id": workstation_id})
    if stage_id:
        context_or.append({"stage_id": stage_id})
    if line_id:
        context_or.append({"line_id": line_id})
    if plant_id:
        context_or.append({"plant_id": plant_id})
    if context_or:
        filters.append(
            {
                "$or": context_or
                + [
                    {
                        "title": {
                            "$in": [
                                "Document Controller",
                                "Quality Control",
                                "Change Control",
                                "Plant Manager",
                                "Line Manager",
                            ]
                        }
                    }
                ]
            }
        )
    query = {"$and": filters} if filters else {}
    cursor = (
        db["users"]
        .find(
            query,
            {
                "_id": 0,
                "embedding": 0,
                "password": 0,
                "hashed_password": 0,
                "refresh_tokens": 0,
            },
        )
        .limit(limit)
    )
    workers = []
    async for user in cursor:
        workers.append(
            {
                "user_id": user.get("user_id"),
                "name": user.get("name") or user.get("email") or user.get("user_id"),
                "email": user.get("email"),
                "title": user.get("title"),
                "role": user.get("role"),
                "employee_id": user.get("employee_id"),
                "plant_id": user.get("plant_id"),
                "line_id": user.get("line_id"),
                "stage_id": user.get("stage_id"),
                "workstation_id": user.get("workstation_id"),
                "label": " - ".join(
                    str(part)
                    for part in (
                        user.get("name") or user.get("email") or user.get("user_id"),
                        user.get("title"),
                        user.get("email"),
                    )
                    if part
                ),
                "value": user.get("user_id"),
            }
        )
    return _bson_safe({"query": text, "count": len(workers), "results": workers})


@router.post("/compliance/change-controls", status_code=status.HTTP_201_CREATED)
async def create_change_control(
    body: ChangeControlRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    payload = body.model_dump(mode="python")
    record = await _build_change_control_record(db, payload, current_user)
    await db[CHANGE_CONTROL_COLLECTION].insert_one(record)
    proposed_nodes = await _create_proposed_change_nodes(db, record, current_user)
    if proposed_nodes:
        await db[CHANGE_CONTROL_COLLECTION].update_one(
            {"change_control_id": record["change_control_id"]},
            {
                "$set": {
                    "proposed_change_nodes": proposed_nodes,
                    "proposed_change_node_ids": [
                        node.get("proposed_change_id") for node in proposed_nodes
                    ],
                    "live_node_touched": False,
                    "cdc_source": PROPOSED_CHANGE_COLLECTION,
                    "updated_at": utc_now(),
                }
            },
        )
        record = {
            **record,
            "proposed_change_nodes": proposed_nodes,
            "proposed_change_node_ids": [
                node.get("proposed_change_id") for node in proposed_nodes
            ],
            "live_node_touched": False,
            "cdc_source": PROPOSED_CHANGE_COLLECTION,
        }
    await safe_mirror(mirror_document(CHANGE_CONTROL_COLLECTION, record, "create"))
    queue_publication_status = {
        "change_control_queue": {
            "published": False,
            "subject": settings.NATS_CHANGE_CONTROL_SUBJECT,
        },
        "approval_notification_queue": {
            "published": False,
            "subject": settings.NATS_APPROVALS_SUBJECT,
        },
        "n8n_email_webhook": {
            "enabled": bool(str(settings.N8N_EMAIL_WEBHOOK_URL or "").strip()),
            "attempted": 0,
            "queued": 0,
            "failed": 0,
        },
    }
    try:
        subject = await nats_store.publish_change_control_event(
            {
                "event": "change_control_created",
                "source_collection": CHANGE_CONTROL_COLLECTION,
                "source_id": record["change_control_id"],
                "source_title": record.get("title")
                or record.get("change_control_number"),
                "change_control": _bson_safe(record),
                "proposed_change_nodes": proposed_nodes,
                "proposed_node_collection": PROPOSED_CHANGE_COLLECTION,
                "approval_chain": record["approval_chain"],
                "approval_assignment_basis": record.get("approval_assignment_basis"),
            }
        )
        queue_publication_status["change_control_queue"] = {
            "published": True,
            "subject": subject,
        }
    except Exception as exc:
        queue_publication_status["change_control_queue"]["error"] = str(exc)
    approval_token_records = await create_approval_tracking_and_notifications(
        db,
        source_collection=CHANGE_CONTROL_COLLECTION,
        source_id=record["change_control_id"],
        source_title=record.get("title")
        or record.get("change_control_number")
        or record["change_control_id"],
        chain=record["approval_chain"],
        current_user_id=_current_user_id(current_user),
    )
    approval_event = approval_notification_event(
        source_collection=CHANGE_CONTROL_COLLECTION,
        source_id=record["change_control_id"],
        source_title=record.get("title")
        or record.get("change_control_number")
        or record["change_control_id"],
        source=_bson_safe(record),
        chain=record["approval_chain"],
        token_records=approval_token_records,
    )
    try:
        subject = await nats_store.publish_approval_event(approval_event)
        queue_publication_status["approval_notification_queue"] = {
            "published": True,
            "subject": subject,
        }
    except Exception as exc:
        queue_publication_status["approval_notification_queue"]["error"] = str(exc)
    queue_publication_status["n8n_email_webhook"] = (
        await _post_approval_notifications_to_n8n(approval_event)
    )
    response_record = dict(record)
    response_record["queue_publication_status"] = queue_publication_status
    response_record["notification_delivery"] = {
        "mode": "external_nats_queue",
        "approval_subject": settings.NATS_APPROVALS_SUBJECT,
        "expected_consumer": "reviewer assignment / email notification workflow",
        "n8n_email_webhook_configured": bool(
            str(settings.N8N_EMAIL_WEBHOOK_URL or "").strip()
        ),
    }
    return _bson_safe(response_record)


@router.get("/compliance/change-controls")
async def list_change_controls(
    status_value: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return _bson_safe(
        await _list_gxp_records(
            db, CHANGE_CONTROL_COLLECTION, page, page_size, status_value
        )
    )


@router.get("/compliance/approvals/{approval_id}/form", response_class=HTMLResponse)
async def approval_form(
    approval_id: str,
    request: Request,
    token: str = Query(..., min_length=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    approval = await db["approvals"].find_one({"approval_id": approval_id}, {"_id": 0})
    token_error = _approval_token_error(approval, token)
    if token_error:
        return HTMLResponse(
            _approval_login_html(
                approval or {"source_title": "Invalid approval"}, token, token_error
            ),
            status_code=403,
        )
    current_user = await _approval_user_from_request(request)
    if not current_user:
        return HTMLResponse(_approval_login_html(approval, token))
    if not _approval_user_matches(approval, current_user):
        return HTMLResponse(
            _approval_login_html(
                approval, token, "This approval is assigned to a different user."
            ),
            status_code=403,
        )
    return HTMLResponse(_approval_form_html(approval, token, current_user=current_user))


@router.post("/compliance/approvals/{approval_id}/form", response_class=HTMLResponse)
async def submit_approval_form(
    approval_id: str,
    request: Request,
    token: str = Form(...),
    action: str = Form(default="decide"),
    decision: str = Form(default=""),
    reason: str | None = Form(default=None),
    meaning: str | None = Form(default=None),
    email: str | None = Form(default=None),
    password: str | None = Form(default=None),
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    approval = await db["approvals"].find_one({"approval_id": approval_id}, {"_id": 0})
    token_error = _approval_token_error(approval, token)
    if token_error:
        return HTMLResponse(
            _approval_login_html(
                approval or {"source_title": "Invalid approval"}, token, token_error
            ),
            status_code=403,
        )

    if action == "login":
        user = await _approval_login_user(db, email, password)
        if not user:
            return HTMLResponse(
                _approval_login_html(
                    approval, token, "Invalid credentials or MFA-enabled account."
                ),
                status_code=401,
            )
        current_user = {
            "sub": user.get("user_id"),
            "email": user.get("email"),
            "role": user.get("role"),
        }
        if not _approval_user_matches(approval, current_user):
            return HTMLResponse(
                _approval_login_html(
                    approval, token, "This approval is assigned to a different user."
                ),
                status_code=403,
            )
        response = HTMLResponse(
            _approval_form_html(approval, token, current_user=current_user)
        )
        _set_approval_access_cookie(response, user)
        return response

    current_user = await _approval_user_from_request(request)
    if not current_user:
        return HTMLResponse(
            _approval_login_html(
                approval, token, "Sign in before submitting this approval."
            ),
            status_code=401,
        )
    if not _approval_user_matches(approval, current_user):
        return HTMLResponse(
            _approval_login_html(
                approval, token, "This approval is assigned to a different user."
            ),
            status_code=403,
        )

    normalized_decision = decision.strip().title()
    if normalized_decision not in {"Approved", "Rejected"}:
        return HTMLResponse(
            _approval_form_html(
                approval,
                token,
                "Decision must be Approved or Rejected.",
                current_user=current_user,
            ),
            status_code=400,
        )
    if normalized_decision == "Rejected" and not str(reason or "").strip():
        return HTMLResponse(
            _approval_form_html(
                approval,
                token,
                "Rejection reason is required.",
                current_user=current_user,
            ),
            status_code=400,
        )

    try:
        result = await record_approval_decision(
            db,
            approval=approval,
            approval_id=approval_id,
            decision=normalized_decision,
            reason=reason,
            signature_meaning=meaning,
            decision_source="sentinel_x_email_form",
        )
    except ApprovalAlreadyDecidedError as exc:
        return HTMLResponse(
            _approval_form_html(approval, token, str(exc), current_user=current_user),
            status_code=409,
        )
    return HTMLResponse(
        _approval_complete_html(result["approval"], normalized_decision)
    )


@router.post("/compliance/approvals/{approval_id}/external-decision")
async def submit_external_approval_decision(
    approval_id: str,
    body: ExternalApprovalDecisionRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    approval = await db["approvals"].find_one({"approval_id": approval_id}, {"_id": 0})
    token_error = _approval_token_error(approval, body.token)
    if token_error:
        status_code = (
            status.HTTP_409_CONFLICT
            if "already been decided" in token_error
            else status.HTTP_403_FORBIDDEN
        )
        raise HTTPException(status_code=status_code, detail=token_error)
    requester = {
        "sub": body.approver_user_id,
        "user_id": body.approver_user_id,
        "email": body.approver_email,
    }
    if not _approval_user_matches(approval, requester):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This approval is assigned to a different user.",
        )

    normalized_decision = body.decision.strip().title()
    if normalized_decision not in {"Approved", "Rejected"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decision must be Approved or Rejected.",
        )
    if normalized_decision == "Rejected" and not str(body.reason or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rejection reason is required.",
        )

    try:
        result = await record_approval_decision(
            db,
            approval=approval,
            approval_id=approval_id,
            decision=normalized_decision,
            reason=body.reason,
            signature_meaning=body.signature_meaning,
            decision_source=body.source or "external_approval_form",
        )
    except ApprovalAlreadyDecidedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return _bson_safe(
        {
            "status": "accepted",
            "approval_id": approval_id,
            "decision": normalized_decision,
            "source_id": approval.get("source_id"),
            "target_node": "Electronic Signature",
            "decision_subject": result["decision_subject"],
            "audit_subject": result["audit_subject"],
            "approval_audit_status": result["approval_audit_status"],
            "approvals": result["approvals"],
        }
    )


@router.get("/compliance/change-controls/{change_control_id}")
async def get_change_control(
    change_control_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    record = await db[CHANGE_CONTROL_COLLECTION].find_one(
        {
            "$or": [
                {"change_control_id": change_control_id},
                {"change_control_number": change_control_id},
            ]
        },
        {"_id": 0},
    )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Change control not found"
        )
    return _bson_safe(record)


@router.post("/compliance/access-reviews", status_code=status.HTTP_201_CREATED)
async def create_access_review(
    body: ReviewRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    payload = body.model_dump(mode="python")
    payload["standard_id"] = "eu-gmp-annex-11"
    return await _insert_gxp_record(
        db, ACCESS_REVIEW_COLLECTION, "access_review", payload, current_user
    )


@router.get("/compliance/access-reviews")
async def list_access_reviews(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await _list_gxp_records(db, ACCESS_REVIEW_COLLECTION, page, page_size)


@router.post("/compliance/data-integrity-reviews", status_code=status.HTTP_201_CREATED)
async def create_data_integrity_review(
    body: ReviewRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    payload = body.model_dump(mode="python")
    payload["standard_id"] = "mhra-gxp-data-integrity"
    payload["principles"] = compliance_readiness()["data_integrity_principles"]
    return await _insert_gxp_record(
        db,
        DATA_INTEGRITY_REVIEW_COLLECTION,
        "data_integrity_review",
        payload,
        current_user,
    )


@router.get("/compliance/data-integrity-reviews")
async def list_data_integrity_reviews(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    return await _list_gxp_records(
        db, DATA_INTEGRITY_REVIEW_COLLECTION, page, page_size
    )
