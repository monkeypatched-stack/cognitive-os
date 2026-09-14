from __future__ import annotations

from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.config import settings
from services.common.approval_chains import approval_notification_event
from services.auth.helpers import nats_store
from services.process_definitions.helpers import process_definition as crud
from services.process_definitions.models.process_definition import (
    ProcessDefinitionCanvasBackendCall,
)
from services.process_definitions.node_services.common import execute_common_node
from services.process_definitions.node_services.functions import execute_function_node
from services.process_definitions.node_services.messaging import execute_messaging_node
from services.process_definitions.node_services.network import (
    execute_http_node,
    execute_mqtt_node,
    execute_tcp_udp_node,
    execute_websocket_node,
)
from services.process_definitions.node_services.parsers import execute_parser_node
from services.process_definitions.node_services.sequence import execute_sequence_node
from services.process_definitions.node_services.storage import execute_storage_node
from services.process_definitions.node_services.webhooks import execute_webhook_node
from services.process_definitions.node_services.models import NodeExecutionResult

COMMON_NODES = {
    "inject",
    "debug",
    "catch",
    "status",
    "link in",
    "link out",
    "link call",
    "junction",
    "complete",
    "comment",
    "subflow",
}
FUNCTION_NODES = {
    "function",
    "switch",
    "change",
    "range",
    "template",
    "delay",
    "trigger",
    "filter",
    "rbe",
    "exec",
}
NETWORK_HTTP_NODES = {"http in", "http response", "http request"}
NETWORK_MQTT_NODES = {"mqtt in", "mqtt out"}
NETWORK_WEBSOCKET_NODES = {"websocket in", "websocket out"}
NETWORK_TCP_UDP_NODES = {"tcp in", "tcp out", "udp in", "udp out"}
SEQUENCE_NODES = {"split", "join", "sort", "batch"}
PARSER_NODES = {"csv", "html", "json", "xml", "yaml"}
STORAGE_NODES = {"file", "file in", "file out", "watch"}
WEBHOOK_NODES = {
    "incoming webhook",
    "outgoing webhook",
    "n8n incoming",
    "n8n outgoing",
    "openclaw incoming",
    "openclaw outgoing",
}
MESSAGING_NODES = {"gmail", "whatsapp"}
COMPLIANCE_NODES = {
    "change request",
    "duplicate change check",
    "impact assessment",
    "risk decision",
    "approval request",
    "electronic signature",
    "implementation task",
    "training assignment",
    "effectiveness check",
    "audit queue",
    "regulated event",
    "redact sensitive values",
    "audit trail",
    "record version",
    "hash verification",
    "compliance report",
}
MATERIAL_FLOW_NODES = {
    "batch record",
    "mbmr template",
    "bmr record",
    "bpr record",
    "dispense weighing",
    "ipc checkpoint",
    "yield reconciliation",
    "equipment usage",
    "area room usage",
}


def _node_label(node: dict[str, Any]) -> str:
    return str(
        node.get("name")
        or node.get("title")
        or (node.get("data") or {}).get("label")
        or node.get("node_id")
        or ""
    ).strip()


def _is_reviewer_assignment_node(node: dict[str, Any]) -> bool:
    label = _node_label(node).lower()
    node_id = str(node.get("node_id") or node.get("id") or "").lower()
    return (
        label == "reviewer assignment"
        or node_id.endswith(":appr-review")
        or node_id == "appr-review"
    )


def _is_approval_queue_node(node: dict[str, Any]) -> bool:
    label = _node_label(node).lower()
    node_id = str(node.get("node_id") or node.get("id") or "").lower()
    return (
        label == "approval queue"
        or node_id.endswith(":appr-queue")
        or node_id == "appr-queue"
    )


def _is_audit_queue_node(node: dict[str, Any]) -> bool:
    label = _node_label(node).lower()
    node_id = str(node.get("node_id") or node.get("id") or "").lower()
    return (
        label == "audit queue"
        or node_id.endswith(":audit-queue")
        or node_id == "audit-queue"
    )


def _is_electronic_signature_node(node: dict[str, Any]) -> bool:
    label = _node_label(node).lower()
    node_id = str(node.get("node_id") or node.get("id") or "").lower()
    return (
        label == "electronic signature"
        or node_id.endswith(":appr-esig")
        or node_id == "appr-esig"
    )


def _is_approval_decision_node(node: dict[str, Any]) -> bool:
    label = _node_label(node).lower()
    node_id = str(node.get("node_id") or node.get("id") or "").lower()
    return (
        label == "approval decision"
        or node_id.endswith(":appr-decision")
        or node_id == "appr-decision"
    )


def _approval_chain_from_message(message: dict[str, Any]) -> list[dict[str, Any]]:
    payload = (
        message.get("payload") if isinstance(message.get("payload"), dict) else message
    )
    chain = (
        payload.get("approval_chain")
        or payload.get("approval_process_definition")
        or message.get("approval_chain")
        or message.get("approval_process_definition")
    )
    return chain if isinstance(chain, list) else []


def _source_payload_from_message(message: dict[str, Any]) -> dict[str, Any]:
    return (
        message.get("payload") if isinstance(message.get("payload"), dict) else message
    )


APPROVAL_APPROVED_STATUSES = {"approved", "complete", "completed"}
APPROVAL_REJECTED_STATUSES = {"rejected", "denied"}
APPROVAL_TERMINAL_STATUSES = APPROVAL_APPROVED_STATUSES | APPROVAL_REJECTED_STATUSES


def _source_id_from_payload(source: dict[str, Any]) -> str | None:
    for key in ("source_id", "change_control_id", "document_id", "record_id"):
        if source.get(key):
            return str(source[key])
    return None


async def _approval_records_for_signature(
    db: AsyncIOMotorDatabase | None, source: dict[str, Any]
) -> list[dict[str, Any]]:
    embedded = source.get("approvals")
    if isinstance(embedded, list):
        return [item for item in embedded if isinstance(item, dict)]
    embedded = source.get("approval_records")
    if isinstance(embedded, list):
        return [item for item in embedded if isinstance(item, dict)]
    if db is None:
        return []
    source_id = _source_id_from_payload(source)
    if not source_id and source.get("approval_id"):
        approval = await db["approvals"].find_one(
            {"approval_id": source.get("approval_id")}, {"_id": 0}
        )
        source_id = (approval or {}).get("source_id")
    if not source_id:
        return []
    return (
        await db["approvals"]
        .find({"source_id": source_id}, {"_id": 0})
        .to_list(length=200)
    )


def _approval_completion(records: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [
        str(record.get("decision") or record.get("status") or "").strip().lower()
        for record in records
    ]
    pending = [
        record
        for record, status_value in zip(records, statuses)
        if status_value not in APPROVAL_TERMINAL_STATUSES
    ]
    rejected = [
        record
        for record, status_value in zip(records, statuses)
        if status_value in APPROVAL_REJECTED_STATUSES
    ]
    approved = [
        record
        for record, status_value in zip(records, statuses)
        if status_value in APPROVAL_APPROVED_STATUSES
    ]
    all_received = bool(records) and not pending
    route = (
        "rejected"
        if rejected
        else "approved" if all_received and len(approved) == len(records) else "pending"
    )
    return {
        "approval_count": len(records),
        "approved_count": len(approved),
        "rejected_count": len(rejected),
        "pending_count": len(pending),
        "all_approvals_received": all_received,
        "route": route,
        "pending_approval_ids": [
            record.get("approval_id") for record in pending if record.get("approval_id")
        ],
        "rejected_approval_ids": [
            record.get("approval_id")
            for record in rejected
            if record.get("approval_id")
        ],
    }


async def _publish_signature_audit_event(
    source: dict[str, Any], completion: dict[str, Any]
) -> str:
    audit_event = {
        "event": "approval_audit_event",
        "event_type": "approval-audit",
        "source_collection": source.get("source_collection")
        or source.get("collection")
        or "gxp_change_controls",
        "source_id": _source_id_from_payload(source),
        "source_title": source.get("source_title") or source.get("title"),
        "approval_id": source.get("approval_id"),
        "decision": "Rejected" if completion["route"] == "rejected" else "Approved",
        "route": completion["route"],
        "signature_valid": True,
        "all_approvals_received": completion["all_approvals_received"],
        "approval_count": completion["approval_count"],
        "approved_count": completion["approved_count"],
        "rejected_count": completion["rejected_count"],
        "pending_count": completion["pending_count"],
    }
    return await nats_store.publish_approval_audit_event(audit_event)


def _validate_named_chain(chain: list[dict[str, Any]]) -> str | None:
    if not chain:
        return "Reviewer Assignment requires a resolved approval_chain"
    for step in chain:
        if not isinstance(step, dict):
            return "Reviewer Assignment approval_chain contains a non-object step"
        if not step.get("assigned_user_id") or not step.get("assigned_user_name"):
            return f"Reviewer Assignment step '{step.get('stage') or step.get('step') or step.get('sequence')}' is not assigned to a named worker"
    return None


def _approval_queue_payload(
    message: dict[str, Any], chain: list[dict[str, Any]]
) -> dict[str, Any]:
    source = _source_payload_from_message(message)
    source_id = (
        source.get("change_control_id")
        or source.get("document_id")
        or source.get("source_id")
    )
    source_title = (
        source.get("title")
        or source.get("document_name")
        or source.get("source_title")
        or source_id
    )
    return approval_notification_event(
        source_collection=str(
            source.get("source_collection")
            or source.get("collection")
            or "gxp_change_controls"
        ),
        source_id=str(source_id),
        source_title=str(source_title),
        source=source,
        chain=chain,
    )


async def _publish_reviewer_assignment_to_approval_queue(
    config: dict[str, Any],
    message: dict[str, Any],
    chain: list[dict[str, Any]],
) -> NodeExecutionResult:
    payload = _approval_queue_payload(message, chain)
    try:
        subject = await nats_store.publish_approval_event(payload)
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "approval_queue_published",
                "action": "reviewer assignment",
                "provider": "nats-approval-queue",
                "subject": subject,
                "websocket_path": "/api/v1/ws/events",
                "notification_count": len(payload["notifications"]),
                "delivery_mode": "parallel",
                "message": payload,
                "next_message": {
                    **payload,
                    "requires_electronic_signature": True,
                    "target_node": "Electronic Signature",
                },
            },
        )
    except Exception as exc:
        return NodeExecutionResult(False, None, None, str(exc))


def _node_red_message(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("message"), dict):
        return dict(payload["message"])
    trigger_message = payload.get("triggerMessage")
    if isinstance(trigger_message, dict) and isinstance(
        trigger_message.get("message"), dict
    ):
        return dict(trigger_message["message"])
    if "payload" in payload:
        return dict(payload)
    if isinstance(trigger_message, dict) and "payload" in trigger_message:
        return {"payload": trigger_message.get("payload")}
    return {"payload": payload}


async def execute_canvas_node_service(
    *,
    db: AsyncIOMotorDatabase,
    layout_id: str,
    node_type: str | None,
    node: dict,
    backend: ProcessDefinitionCanvasBackendCall | None,
    config: dict,
    payload: dict[str, Any],
) -> NodeExecutionResult:
    message_payload = (
        payload if node_type in {"inject", "trigger"} else _node_red_message(payload)
    )
    if node_type in COMMON_NODES:
        layout = (
            await crud.get_canvas_layout_by_id(db, layout_id)
            if node_type in {"link out", "link call"}
            else None
        )
        return await execute_common_node(
            node_type, node, layout, config, message_payload
        )
    if node_type in FUNCTION_NODES:
        return await execute_function_node(node_type, config, message_payload)
    if node_type in SEQUENCE_NODES:
        return await execute_sequence_node(node_type, config, message_payload)
    if node_type in PARSER_NODES:
        return await execute_parser_node(node_type, config, message_payload)
    if node_type in STORAGE_NODES:
        return await execute_storage_node(node_type, config, message_payload)
    if node_type in WEBHOOK_NODES:
        return await execute_webhook_node(node_type, config, message_payload)
    if node_type in MESSAGING_NODES:
        return await execute_messaging_node(node_type, config, message_payload)
    if node_type in COMPLIANCE_NODES:
        message = dict(message_payload)
        message["compliance_action"] = node_type
        if node_type == "audit queue" or _is_audit_queue_node(node):
            source = _source_payload_from_message(message)
            return NodeExecutionResult(
                True,
                None,
                {
                    "status": "audit_queue_bound",
                    "action": "audit queue",
                    "provider": "nats-websocket",
                    "subject": settings.NATS_AUDIT_SUBJECT,
                    "queue": settings.NATS_AUDIT_QUEUE,
                    "websocket_path": "/api/v1/ws/events",
                    "message": message,
                    "next_message": {
                        "event": "audit_queue_ready",
                        "event_type": "audit-event",
                        "source": source,
                        "subject": settings.NATS_AUDIT_SUBJECT,
                        "queue": settings.NATS_AUDIT_QUEUE,
                        "websocket_path": "/api/v1/ws/events",
                        "target_node": "Receive Regulated Event",
                    },
                },
            )
        if node_type == "approval request" and _is_approval_queue_node(node):
            source = _source_payload_from_message(message)
            return NodeExecutionResult(
                True,
                None,
                {
                    "status": "approval_queue_bound",
                    "action": "approval queue",
                    "provider": "nats-websocket",
                    "subject": settings.NATS_APPROVALS_SUBJECT,
                    "queue": settings.NATS_APPROVALS_QUEUE,
                    "websocket_path": "/api/v1/ws/events",
                    "message": message,
                    "next_message": {
                        "event": "approval_queue_ready",
                        "source": source,
                        "subject": settings.NATS_APPROVALS_SUBJECT,
                        "queue": settings.NATS_APPROVALS_QUEUE,
                        "websocket_path": "/api/v1/ws/events",
                    },
                },
            )
        if node_type == "approval request" and _is_reviewer_assignment_node(node):
            chain = _approval_chain_from_message(message)
            error = _validate_named_chain(chain)
            if error:
                return NodeExecutionResult(False, None, None, error)
            return await _publish_reviewer_assignment_to_approval_queue(
                config, message, chain
            )
        if node_type == "electronic signature" and _is_electronic_signature_node(node):
            source = _source_payload_from_message(message)
            signed = bool(
                source.get("signature_hash")
                or source.get("signature_id")
                or source.get("signature_valid") is True
            )
            if not signed:
                return NodeExecutionResult(
                    True,
                    None,
                    {
                        "status": "signature_required",
                        "action": "electronic signature",
                        "message": source,
                        "next_message": {
                            **source,
                            "signature_valid": False,
                            "target_node": "Electronic Signature",
                        },
                        "signature_required": True,
                    },
                )
            approvals = await _approval_records_for_signature(db, source)
            completion = _approval_completion(approvals)
            if not completion["all_approvals_received"]:
                return NodeExecutionResult(
                    True,
                    None,
                    {
                        "status": "approvals_pending",
                        "action": "electronic signature",
                        "message": source,
                        **completion,
                        "next_message": {
                            **source,
                            **completion,
                            "signature_valid": False,
                            "target_node": "Electronic Signature",
                        },
                        "signature_required": True,
                    },
                )
            try:
                audit_subject = await _publish_signature_audit_event(source, completion)
            except Exception as exc:
                return NodeExecutionResult(False, None, None, str(exc))
            target_node = (
                "Rejected" if completion["route"] == "rejected" else "Approved"
            )
            return NodeExecutionResult(
                True,
                None,
                {
                    "status": "signature_valid",
                    "action": "electronic signature",
                    "message": source,
                    **completion,
                    "audit_subject": audit_subject,
                    "next_message": {
                        **source,
                        **completion,
                        "signature_valid": True,
                        "audit_subject": audit_subject,
                        "target_node": target_node,
                    },
                    "signature_required": True,
                },
            )
        if node_type == "risk decision" and _is_approval_decision_node(node):
            source = _source_payload_from_message(message)
            decision = (
                str(source.get("decision") or source.get("status") or "")
                .strip()
                .lower()
            )
            route = (
                "rejected"
                if decision == "rejected"
                else "approved" if decision == "approved" else "more-info"
            )
            return NodeExecutionResult(
                True,
                None,
                {
                    "status": "decision_routed",
                    "action": "approval decision",
                    "route": route,
                    "message": source,
                    "next_message": {
                        **source,
                        "route": route,
                        "target_node": (
                            "Rejected"
                            if route == "rejected"
                            else (
                                "Approved"
                                if route == "approved"
                                else "Approval Decision"
                            )
                        ),
                    },
                },
            )
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "compliance_queued",
                "action": node_type,
                "endpoint": config.get("endpoint"),
                "method": config.get("method", "POST"),
                "message": message,
                "audit_required": bool(config.get("audit_required", True)),
                "signature_required": bool(config.get("signature_required", False)),
            },
        )
    if node_type in MATERIAL_FLOW_NODES:
        message = dict(message_payload)
        source = _source_payload_from_message(message)
        next_message = {
            **message,
            "material_flow_action": node_type,
            "batch_execution_record_id": config.get("batch_execution_record_id")
            or source.get("batch_execution_record_id"),
            "batch_id": config.get("batch_id") or source.get("batch_id"),
            "process_definition_id": config.get("process_definition_id")
            or source.get("process_definition_id"),
            "process_step_id": config.get("process_step_id")
            or source.get("process_step_id"),
            "bom_id": config.get("bom_id") or source.get("bom_id"),
        }
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "material_flow_queued",
                "action": node_type,
                "endpoint": config.get("endpoint"),
                "method": config.get("method", "POST"),
                "message": message,
                "next_message": next_message,
                "audit_required": bool(config.get("audit_required", True)),
                "signature_required": bool(config.get("signature_required", False)),
            },
        )
    if node_type in NETWORK_HTTP_NODES:
        return await execute_http_node(node_type, backend, config, message_payload)
    if node_type in NETWORK_MQTT_NODES:
        return await execute_mqtt_node(node_type, config, message_payload)
    if node_type in NETWORK_WEBSOCKET_NODES:
        return await execute_websocket_node(node_type, config, message_payload)
    if node_type in NETWORK_TCP_UDP_NODES:
        return await execute_tcp_udp_node(node_type, config, message_payload)
    if backend and backend.url:
        return await execute_backend_http_call(backend, config, message_payload)
    return NodeExecutionResult(True, None, {"status": "queued"})


async def execute_backend_http_call(
    backend: ProcessDefinitionCanvasBackendCall,
    config: dict,
    payload: dict[str, Any],
) -> NodeExecutionResult:
    method = (backend.method or "POST").upper()
    query_params = (
        config.get("query") if isinstance(config.get("query"), dict) else None
    )
    timeout_seconds = (
        config.get("timeout_seconds")
        if isinstance(config.get("timeout_seconds"), (int, float))
        else 20.0
    )
    try:
        async with httpx.AsyncClient(timeout=float(timeout_seconds)) as client:
            response = await client.request(
                method,
                backend.url,
                json=payload,
                headers=backend.headers,
                params=query_params,
            )
        try:
            response_body = response.json()
        except Exception:
            response_body = response.text
        return NodeExecutionResult(
            response.is_success,
            response.status_code,
            response_body,
            None if response.is_success else response.text,
        )
    except httpx.HTTPError as exc:
        return NodeExecutionResult(False, None, None, str(exc))
