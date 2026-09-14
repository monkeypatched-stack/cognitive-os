from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx

from services.common.config import settings
from services.common.compliance import utc_now
from services.common.n8n_auth import n8n_webhook_auth_headers

APPROVALS_COLLECTION = "approvals"
NOTIFICATIONS_COLLECTION = "notifications"


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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _record_id(document: dict[str, Any]) -> str | None:
    for key in (
        "change_control_id",
        "document_id",
        "id",
        "sop_id",
        "workstation_id",
        "stage_id",
        "line_id",
        "plant_id",
        "machine_id",
        "equipment_id",
    ):
        value = document.get(key)
        if value:
            return str(value)
    return None


def _user_summary(user: dict[str, Any] | None, fallback_role: str) -> dict[str, Any]:
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


def _approval_step(
    *,
    sequence: int,
    stage: str,
    role: str,
    user: dict[str, Any] | None,
    source: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    assigned = _user_summary(user, role)
    return {
        "sequence": sequence,
        "stage": stage,
        "step": stage,
        "status": "Pending",
        "role": role,
        "required_role": role,
        "required_roles": [role],
        "assigned_user": assigned,
        "assigned_user_id": assigned.get("user_id"),
        "assigned_user_name": assigned.get("name"),
        "assigned_user_title": assigned.get("title"),
        "assigned_user_email": assigned.get("email"),
        "minimum_approvals": 1,
        "routing": "sequential",
        "source": source,
        "context": context,
    }


async def ensure_system_approval_user(db: Any, key: str) -> dict[str, Any]:
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
    return (
        await db["users"].find_one(
            {"user_id": document["user_id"]}, {"_id": 0, "embedding": 0}
        )
        or document
    )


async def _user_by_id(db: Any, user_id: str | None) -> dict[str, Any] | None:
    if not user_id:
        return None
    return await db["users"].find_one({"user_id": user_id}, {"_id": 0, "embedding": 0})


async def _line_manager_for_context(
    db: Any, workstation: dict[str, Any] | None, plant_id: str | None
) -> dict[str, Any] | None:
    if not workstation:
        return None
    explicit = await _user_by_id(
        db, workstation.get("line_manager_id") or workstation.get("supervisor_id")
    )
    if explicit:
        return explicit
    line_id = workstation.get("line_id")
    stage_id = workstation.get("stage_id")
    filters: list[dict[str, Any]] = []
    if line_id:
        filters.extend([{"line_id": line_id}, {"assigned_line_id": line_id}])
    if stage_id:
        filters.append({"stage_id": stage_id})
    if plant_id:
        filters.append({"plant_id": plant_id})
    role_filter = {
        "$or": [
            {"title": {"$regex": "line manager", "$options": "i"}},
            {"role": {"$regex": "line[-_ ]?manager", "$options": "i"}},
        ]
    }
    if filters:
        scoped = await db["users"].find_one(
            {"$and": [role_filter, {"$or": filters}]},
            {"_id": 0, "embedding": 0},
        )
        if scoped:
            return scoped
    return await db["users"].find_one(role_filter, {"_id": 0, "embedding": 0})


async def _user_by_text(db: Any, value: Any) -> dict[str, Any] | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    escaped = text.replace(" ", ".*")
    return await db["users"].find_one(
        {
            "$or": [
                {"user_id": text},
                {"name": {"$regex": f"^{escaped}$", "$options": "i"}},
                {"email": {"$regex": escaped, "$options": "i"}},
            ]
        },
        {"_id": 0, "embedding": 0},
    )


async def _ensure_named_user(
    db: Any, value: Any, fallback_role: str, plant_id: str | None = None
) -> dict[str, Any] | None:
    user = await _user_by_text(db, value)
    if user:
        return user
    if not value:
        return None
    name = str(value).strip()
    if not name or name.lower().startswith("unknown"):
        return None
    user_id = (
        "USER-AUTO-"
        + "".join(ch for ch in name.upper() if ch.isalnum() or ch == "-").replace(
            " ", "-"
        )[:48]
    )
    document = {
        "user_id": user_id,
        "name": name,
        "title": fallback_role,
        "role": fallback_role.lower().replace(" ", "-"),
        "department": "DEPT-DOCUMENT-CONTROL",
        "plant_id": plant_id or "PLT-IND-AHM-004",
        "is_active": True,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    await db["users"].update_one(
        {"user_id": user_id},
        {"$setOnInsert": document, "$set": {"updated_at": utc_now()}},
        upsert=True,
    )
    return (
        await db["users"].find_one({"user_id": user_id}, {"_id": 0, "embedding": 0})
        or document
    )


async def _sop_context(
    db: Any, affected_entities: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    entity_ids = [
        str(entity.get("entity_id"))
        for entity in affected_entities
        if entity.get("entity_id")
    ]
    names = [
        str(entity.get("name")) for entity in affected_entities if entity.get("name")
    ]
    stage_ids = [
        str(entity.get("entity_id"))
        for entity in affected_entities
        if entity.get("collection") in {"industrial_stages", "stages"}
        and entity.get("entity_id")
    ]
    workstation_ids = [
        str(entity.get("entity_id"))
        for entity in affected_entities
        if entity.get("collection") == "workstations" and entity.get("entity_id")
    ]

    sop = None
    if entity_ids or names:
        sop = await db["sops"].find_one(
            {
                "$or": [
                    {"id": {"$in": entity_ids}},
                    {"sop_id": {"$in": entity_ids}},
                    {"title": {"$in": names}},
                    {"name": {"$in": names}},
                    {"stage_id": {"$in": stage_ids}},
                ]
            },
            {"_id": 0, "embedding": 0},
        )

    if sop and sop.get("workstation_id"):
        workstation_ids.insert(0, str(sop["workstation_id"]))
    workstation = None
    if workstation_ids:
        workstation = await db["workstations"].find_one(
            {
                "$or": [
                    {"id": {"$in": workstation_ids}},
                    {"workstation_id": {"$in": workstation_ids}},
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
                    {"user_id": workstation.get("assigned_user_id")},
                    {"workstation_id": workstation.get("id")},
                ],
                "title": "Production Worker",
            },
            {"_id": 0, "embedding": 0},
        )
    return sop, workstation, operator


def _approval_basis(
    source_collection: str, source_id: str, sop: dict | None, workstation: dict | None
) -> dict[str, Any]:
    return {
        "mode": (
            "n8n_auto_resolution"
            if settings.N8N_APPROVAL_RESOLVER_WEBHOOK_URL
            else "manual_default_graph_resolution"
        ),
        "source_collection": source_collection,
        "source_id": source_id,
        "sop_id": (sop or {}).get("id") or (sop or {}).get("sop_id"),
        "sop_title": (sop or {}).get("title") or (sop or {}).get("name"),
        "document_node_collection": "sops" if sop else None,
        "document_node_id": (sop or {}).get("id") or (sop or {}).get("sop_id"),
        "workstation_id": (workstation or {}).get("id")
        or (workstation or {}).get("workstation_id"),
        "workstation_name": (workstation or {}).get("name"),
        "line_id": (workstation or {}).get("line_id") or (sop or {}).get("line_id"),
        "stage_id": (workstation or {}).get("stage_id") or (sop or {}).get("stage_id"),
        "plant_id": (workstation or {}).get("plant_id") or (sop or {}).get("plant_id"),
        "uses_actual_worker_nodes": True,
        "resolved_at": utc_now(),
    }


def validate_named_approval_chain(chain: list[dict[str, Any]]) -> None:
    if not chain:
        raise ValueError("approval chain is empty")
    for step in chain:
        if not step.get("assigned_user_id") or not step.get("assigned_user_name"):
            raise ValueError(
                f"approval step '{step.get('stage') or step.get('step')}' is not resolved to a named worker"
            )


async def _local_approval_chain(
    db: Any,
    *,
    source_collection: str,
    source_id: str,
    source_document: dict[str, Any],
    affected_entities: list[dict[str, Any]],
    current_user_id: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    document_controller = await ensure_system_approval_user(db, "document_controller")
    quality_control = await ensure_system_approval_user(db, "quality_control")
    change_control = await ensure_system_approval_user(db, "change_control")
    sop, workstation, operator = await _sop_context(db, affected_entities)
    basis = _approval_basis(source_collection, source_id, sop, workstation)
    plant_id = basis.get("plant_id")

    current_user = await _user_by_id(db, current_user_id)
    document_owner = await _ensure_named_user(
        db, source_document.get("document_owner"), "Document Owner", plant_id
    )
    reviewer = await _ensure_named_user(
        db, source_document.get("document_reviewer"), "Document Reviewer", plant_id
    )
    approver = await _ensure_named_user(
        db, source_document.get("document_approver"), "Document Approver", plant_id
    )
    line_manager = await _line_manager_for_context(db, workstation, plant_id)
    plant_manager = await _user_by_id(
        db, (workstation or {}).get("site_manager_id")
    ) or await _user_by_id(db, "USER-PRASHUN-JAVERI")

    worker = operator or document_owner or current_user or document_controller
    line_manager = line_manager or plant_manager or current_user or document_controller
    plant_manager = plant_manager or line_manager or current_user or document_controller

    if source_collection == "document_metadata":
        steps = [
            (
                "Document Review",
                "Document Controller",
                document_controller,
                "document_control",
            ),
            (
                "Document Owner Review",
                "Document Owner",
                document_owner or reviewer or current_user or document_controller,
                "document_owner",
            ),
            (
                "Quality Control Review",
                "Quality Control",
                quality_control,
                "quality_control",
            ),
            (
                "Document Approval",
                "Document Approver",
                approver or plant_manager,
                "document_approval",
            ),
            ("Plant Manager Approval", "Plant Manager", plant_manager, "plant"),
        ]
    else:
        steps = [
            (
                "Document Review",
                "Document Controller",
                document_controller,
                "document_control",
            ),
            ("Workstation Impact Review", "Production Worker", worker, "workstation"),
            (
                "Quality Control Review",
                "Quality Control",
                quality_control,
                "quality_control",
            ),
            (
                "Change Control Approval",
                "Change Control",
                change_control,
                "change_control",
            ),
            ("Line Manager Approval", "Line Manager", line_manager, "line"),
            ("Plant Manager Approval", "Plant Manager", plant_manager, "plant"),
        ]
    chain = [
        _approval_step(
            sequence=index,
            stage=stage,
            role=role,
            user=user,
            source=source,
            context=basis,
        )
        for index, (stage, role, user, source) in enumerate(steps, start=1)
    ]
    validate_named_approval_chain(chain)
    return chain, basis


async def _n8n_approval_chain(
    db: Any,
    *,
    source_collection: str,
    source_id: str,
    source_document: dict[str, Any],
    affected_entities: list[dict[str, Any]],
    current_user_id: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    if not settings.N8N_APPROVAL_RESOLVER_WEBHOOK_URL:
        return None
    fallback_chain, fallback_basis = await _local_approval_chain(
        db,
        source_collection=source_collection,
        source_id=source_id,
        source_document=source_document,
        affected_entities=affected_entities,
        current_user_id=current_user_id,
    )
    payload = {
        "source_collection": source_collection,
        "source_id": source_id,
        "source_document": source_document,
        "affected_entities": affected_entities,
        "current_user_id": current_user_id,
        "manual_default_chain": fallback_chain,
        "manual_default_basis": fallback_basis,
    }
    try:
        async with httpx.AsyncClient(
            timeout=settings.N8N_APPROVAL_RESOLVER_TIMEOUT_SECONDS
        ) as client:
            response = await client.post(
                settings.N8N_APPROVAL_RESOLVER_WEBHOOK_URL,
                json=payload,
                headers=n8n_webhook_auth_headers(),
            )
            response.raise_for_status()
            data = response.json()
    except Exception:
        return fallback_chain, {
            **fallback_basis,
            "mode": "manual_default_graph_resolution",
            "n8n_status": "fallback",
        }
    chain = data.get("approval_chain") or data.get("approval_workflow")
    basis = data.get("approval_assignment_basis") or fallback_basis
    if not isinstance(chain, list):
        return fallback_chain, {
            **fallback_basis,
            "mode": "manual_default_graph_resolution",
            "n8n_status": "invalid_response",
        }
    try:
        validate_named_approval_chain(chain)
    except ValueError:
        return fallback_chain, {
            **fallback_basis,
            "mode": "manual_default_graph_resolution",
            "n8n_status": "unresolved_response",
        }
    return chain, {**basis, "mode": "n8n_auto_resolution", "n8n_status": "resolved"}


async def resolve_named_approval_chain(
    db: Any,
    *,
    source_collection: str,
    source_id: str,
    source_document: dict[str, Any],
    affected_entities: list[dict[str, Any]],
    current_user_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved = await _n8n_approval_chain(
        db,
        source_collection=source_collection,
        source_id=source_id,
        source_document=source_document,
        affected_entities=affected_entities,
        current_user_id=current_user_id,
    )
    if resolved:
        return resolved
    return await _local_approval_chain(
        db,
        source_collection=source_collection,
        source_id=source_id,
        source_document=source_document,
        affected_entities=affected_entities,
        current_user_id=current_user_id,
    )


def approval_graph_relationships(
    chain: list[dict[str, Any]], basis: dict[str, Any]
) -> list[dict[str, Any]]:
    relationships = [
        {
            "type": "REQUIRES_APPROVAL_FROM",
            "target_collection": "users",
            "target_id": step.get("assigned_user_id"),
            "stage": step.get("stage"),
            "role": step.get("role"),
        }
        for step in chain
        if step.get("assigned_user_id")
    ]
    if basis.get("document_node_id"):
        relationships.append(
            {
                "type": "CONTROLS_DOCUMENT",
                "target_collection": basis.get("document_node_collection") or "sops",
                "target_id": basis.get("document_node_id"),
            }
        )
    if basis.get("workstation_id"):
        relationships.append(
            {
                "type": "CONTROLS_PROCESS",
                "target_collection": "workstations",
                "target_id": basis.get("workstation_id"),
            }
        )
    return relationships


def source_lookup(source_collection: str, source_id: str) -> dict[str, Any]:
    return {
        "$or": [
            {"change_control_id": source_id},
            {"document_id": source_id},
            {"id": source_id},
            {"sop_id": source_id},
            {"workstation_id": source_id},
            {"stage_id": source_id},
            {"line_id": source_id},
            {"plant_id": source_id},
            {"machine_id": source_id},
            {"equipment_id": source_id},
        ]
    }


def approval_token_for(approval_id: str) -> str:
    return hashlib.sha256(
        f"{approval_id}:{settings.ACCESS_TOKEN_SECRET}".encode("utf-8")
    ).hexdigest()


def new_approval_token() -> str:
    return secrets.token_urlsafe(32)


def approval_token_expires_at() -> datetime:
    return utc_now() + timedelta(hours=24)


def approval_step_email(step: dict[str, Any]) -> str | None:
    assigned_user = (
        step.get("assigned_user") if isinstance(step.get("assigned_user"), dict) else {}
    )
    return step.get("assigned_user_email") or assigned_user.get("email")


def approval_notification_event(
    *,
    source_collection: str,
    source_id: str,
    source_title: str,
    source: dict[str, Any],
    chain: list[dict[str, Any]],
    token_records: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    def notification_for_step(step: dict[str, Any]) -> dict[str, Any]:
        approval_id = (
            f"approval-{source_collection}-{source_id}-{step.get('sequence')}".replace(
                " ", "-"
            )
        )
        token_record = (token_records or {}).get(approval_id) or {}
        approval_token = token_record.get("approval_token") or new_approval_token()
        approval_expires_at = (
            token_record.get("approval_token_expires_at") or approval_token_expires_at()
        )
        approval_form_url = (
            token_record.get("approval_form_url")
            or f"{settings.APPROVAL_FORM_BASE_URL.rstrip('/')}/{approval_id}/form?token={approval_token}"
        )
        return {
            "sequence": step.get("sequence"),
            "stage": step.get("stage") or step.get("step"),
            "role": step.get("role") or step.get("required_role"),
            "assigned_user_id": step.get("assigned_user_id"),
            "assigned_user_name": step.get("assigned_user_name"),
            "assigned_user_email": approval_step_email(step),
            "source_collection": source_collection,
            "source_id": source_id,
            "source_title": source_title,
            "approval_id": approval_id,
            "approval_form_url": approval_form_url,
            "approval_token_expires_at": approval_expires_at,
            "subject": f"Approval required: {source_title}",
            "body": f"{step.get('stage') or step.get('step') or 'Approval'} is pending for {source_title}.",
        }

    notifications = [notification_for_step(step) for step in chain]
    return {
        "event": "approval_reviewer_assignment",
        "event_type": "approval-request",
        "source_collection": source_collection,
        "source_id": source_id,
        "source_title": source_title,
        "source": source,
        "approval_chain": chain,
        "notifications": notifications,
        "delivery_mode": "parallel",
    }


async def write_approval_chain_to_source(
    db: Any,
    *,
    source_collection: str,
    source_id: str,
    chain: list[dict[str, Any]],
    basis: dict[str, Any],
) -> None:
    validate_named_approval_chain(chain)
    await db[source_collection].update_one(
        source_lookup(source_collection, source_id),
        {
            "$set": {
                "approval_chain": chain,
                "approval_workflow": chain,
                "approval_assignment_basis": basis,
                "approval_chain_resolved": True,
                "approval_chain_resolved_at": utc_now(),
            }
        },
    )


async def create_approval_tracking_and_notifications(
    db: Any,
    *,
    source_collection: str,
    source_id: str,
    source_title: str,
    chain: list[dict[str, Any]],
    current_user_id: str | None,
) -> dict[str, dict[str, Any]]:
    validate_named_approval_chain(chain)
    now = utc_now()
    token_records: dict[str, dict[str, Any]] = {}

    async def upsert_approval(step: dict[str, Any]) -> None:
        approval_id = (
            f"approval-{source_collection}-{source_id}-{step.get('sequence')}".replace(
                " ", "-"
            )
        )
        approval_token = new_approval_token()
        expires_at = approval_token_expires_at()
        approval_form_url = f"{settings.APPROVAL_FORM_BASE_URL.rstrip('/')}/{approval_id}/form?token={approval_token}"
        token_records[approval_id] = {
            "approval_token": approval_token,
            "approval_token_expires_at": expires_at,
            "approval_form_url": approval_form_url,
        }
        await db[APPROVALS_COLLECTION].update_one(
            {"approval_id": approval_id},
            {
                "$set": {
                    "approval_id": approval_id,
                    "source_collection": source_collection,
                    "source_id": source_id,
                    "source_title": source_title,
                    "sequence": step.get("sequence"),
                    "stage": step.get("stage"),
                    "role": step.get("role"),
                    "assigned_user_id": step.get("assigned_user_id"),
                    "assigned_user_name": step.get("assigned_user_name"),
                    "assigned_user_email": approval_step_email(step),
                    "status": step.get("status") or "Pending",
                    "routing": step.get("routing") or "sequential",
                    "requested_by": current_user_id or "system",
                    "requested_at": now,
                    "approval_form_url": approval_form_url,
                    "approval_token": approval_token,
                    "approval_token_expires_at": expires_at,
                    "approval_link_issued_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def upsert_notification(step: dict[str, Any]) -> None:
        notification_id = f"notification-approval-{source_collection}-{source_id}-{step.get('sequence')}".replace(
            " ", "-"
        )
        approval_id = (
            f"approval-{source_collection}-{source_id}-{step.get('sequence')}".replace(
                " ", "-"
            )
        )
        token_record = token_records.get(approval_id)
        if not token_record:
            approval_token = new_approval_token()
            expires_at = approval_token_expires_at()
            token_record = {
                "approval_token": approval_token,
                "approval_token_expires_at": expires_at,
                "approval_form_url": f"{settings.APPROVAL_FORM_BASE_URL.rstrip('/')}/{approval_id}/form?token={approval_token}",
            }
            token_records[approval_id] = token_record
        await db[NOTIFICATIONS_COLLECTION].update_one(
            {"notification_id": notification_id},
            {
                "$set": {
                    "notification_id": notification_id,
                    "user_id": step.get("assigned_user_id"),
                    "recipient_name": step.get("assigned_user_name"),
                    "recipient_email": approval_step_email(step),
                    "title": f"Approval required: {source_title}",
                    "message": f"{step.get('stage')} is pending for {source_title}.",
                    "type": "approval_request",
                    "source_collection": source_collection,
                    "source_id": source_id,
                    "approval_id": approval_id,
                    "approval_form_url": token_record["approval_form_url"],
                    "approval_token_expires_at": token_record[
                        "approval_token_expires_at"
                    ],
                    "approval_link_issued_at": now,
                    "approval_sequence": step.get("sequence"),
                    "status": "Unread",
                    "created_by": current_user_id or "system",
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    await asyncio.gather(*(upsert_approval(step) for step in chain))
    await asyncio.gather(*(upsert_notification(step) for step in chain))
    return token_records
