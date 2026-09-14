from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

from services.process_definitions.models.process_node_catalog import (
    ProcessNodeCatalogCategory,
    ProcessNodeCatalogItem,
    ProcessNodeCatalogResponse,
)

NODE_CATALOG_DEFINITIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Common",
        (
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
        ),
    ),
    (
        "Function",
        (
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
        ),
    ),
    (
        "Network",
        (
            "mqtt in",
            "mqtt out",
            "http in",
            "http response",
            "http request",
            "websocket in",
            "websocket out",
            "tcp in",
            "tcp out",
            "udp in",
            "udp out",
        ),
    ),
    ("Sequence", ("split", "join", "sort", "batch")),
    ("Parser", ("csv", "html", "json", "xml", "yaml")),
    ("Storage", ("file", "file in", "file out", "watch")),
    (
        "Webhooks",
        (
            "incoming webhook",
            "outgoing webhook",
            "n8n incoming",
            "n8n outgoing",
            "openclaw incoming",
            "openclaw outgoing",
        ),
    ),
    ("Messaging", ("gmail", "whatsapp")),
    (
        "Compliance",
        (
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
        ),
    ),
    (
        "Material Flow",
        (
            "batch record",
            "mbmr template",
            "bmr record",
            "bpr record",
            "dispense weighing",
            "ipc checkpoint",
            "yield reconciliation",
            "equipment usage",
            "area room usage",
        ),
    ),
)


def _catalog_key(category: str, label: str) -> str:
    text = f"{category}-{label}".strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _node_type(label: str) -> str:
    return label.strip().lower()


def _node_description(category: str, label: str) -> str:
    if category == "Compliance":
        return f"{label.title()} node for GxP change control, approval, and Part 11 audit process definitions."
    if category == "Material Flow":
        return f"{label.title()} node for batch/BMR material-flow execution and GMP evidence."
    return f"{label.title()} process_definition node in the {category} category."


def _backend_defaults(category: str, label: str) -> dict:
    node_type = _node_type(label)
    if node_type in {"http request", "http in", "http response"}:
        return {
            "service": "http",
            "action": node_type,
            "method": "POST",
            "path": f"/api/v1/process-definitions/canvas/{{layout_id}}/nodes/{{node_id}}/execute",
        }
    if node_type.startswith("mqtt "):
        return {
            "service": "mqtt",
            "action": node_type,
            "method": "PUBLISH" if node_type.endswith("out") else "SUBSCRIBE",
            "path": f"/api/v1/process-definitions/canvas/{{layout_id}}/nodes/{{node_id}}/execute",
        }
    if node_type.startswith("websocket "):
        return {
            "service": "websocket",
            "action": node_type,
            "method": "SEND" if node_type.endswith("out") else "LISTEN",
            "path": f"/api/v1/process-definitions/canvas/{{layout_id}}/nodes/{{node_id}}/execute",
        }
    if node_type.startswith("tcp ") or node_type.startswith("udp "):
        service = node_type.split(" ", 1)[0]
        return {
            "service": service,
            "action": node_type,
            "method": "SEND" if node_type.endswith("out") else "LISTEN",
            "path": f"/api/v1/process-definitions/canvas/{{layout_id}}/nodes/{{node_id}}/execute",
        }
    if (
        "webhook" in node_type
        or node_type.startswith("n8n ")
        or node_type.startswith("openclaw ")
    ):
        return {
            "service": "webhook",
            "action": node_type,
            "method": "POST",
            "path": f"/api/v1/process-definitions/canvas/{{layout_id}}/nodes/{{node_id}}/execute",
        }
    if node_type in {"gmail", "whatsapp"}:
        return {
            "service": "messaging",
            "action": node_type,
            "method": "SEND",
            "path": f"/api/v1/process-definitions/canvas/{{layout_id}}/nodes/{{node_id}}/execute",
        }
    if category == "Compliance":
        path_by_type = {
            "change request": "/api/v1/auth/compliance/change-controls",
            "duplicate change check": "/api/v1/auth/compliance/change-controls",
            "impact assessment": "/api/v1/auth/compliance/change-controls",
            "risk decision": "/api/v1/auth/compliance/change-controls",
            "approval request": "/api/v1/auth/compliance/change-controls",
            "electronic signature": "/api/v1/auth/electronic-signatures",
            "implementation task": "/api/v1/auth/compliance/change-controls",
            "training assignment": "/api/v1/auth/compliance/change-controls",
            "effectiveness check": "/api/v1/auth/compliance/change-controls",
            "audit queue": "/api/v1/ws/events",
            "regulated event": "/api/v1/auth/audit-trail",
            "redact sensitive values": "/api/v1/auth/audit-trail",
            "audit trail": "/api/v1/auth/audit-trail",
            "record version": "/api/v1/auth/compliance/bundle/{collection}/{record_id}",
            "hash verification": "/api/v1/auth/audit-trail/verify",
            "compliance report": "/api/v1/auth/audit-trail/verify",
        }
        method_by_type = {
            "hash verification": "GET",
            "record version": "GET",
            "compliance report": "GET",
        }
        return {
            "service": "compliance",
            "action": node_type,
            "method": method_by_type.get(node_type, "POST"),
            "path": path_by_type.get(
                node_type,
                f"/api/v1/process-definitions/canvas/{{layout_id}}/nodes/{{node_id}}/execute",
            ),
        }
    if category == "Material Flow":
        path_by_type = {
            "batch record": "/api/v1/batch-execution-records",
            "mbmr template": "/api/v1/process-definitions",
            "bmr record": "/api/v1/batch-execution-records",
            "bpr record": "/api/v1/batch-execution-records",
            "dispense weighing": "/api/v1/batch-execution-records",
            "ipc checkpoint": "/api/v1/process-definitions",
            "yield reconciliation": "/api/v1/batch-execution-records",
            "equipment usage": "/api/v1/equipment-usage-ledger",
            "area room usage": "/api/v1/area-room-usage-ledger",
        }
        return {
            "service": "material_flow",
            "action": node_type,
            "method": "POST",
            "path": path_by_type.get(
                node_type,
                f"/api/v1/process-definitions/canvas/{{layout_id}}/nodes/{{node_id}}/execute",
            ),
        }
    return {
        "service": "process_definition",
        "action": node_type,
        "method": "POST",
        "path": f"/api/v1/process-definitions/canvas/{{layout_id}}/nodes/{{node_id}}/execute",
    }


def _field(
    field_type: str,
    *,
    label: str,
    required: bool = False,
    default=None,
    secret: bool = False,
    options: list[str | dict] | None = None,
    placeholder: str | None = None,
    help: str | None = None,
) -> dict:
    schema = {
        "type": field_type,
        "label": label,
        "required": required,
        "default": default,
    }
    if secret:
        schema["secret"] = True
    if options:
        schema["options"] = options
    if placeholder:
        schema["placeholder"] = placeholder
    if help:
        schema["help"] = help
    return schema


def _options(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"value": value, "label": label} for value, label in pairs]


def _config_schema(label: str) -> dict:
    node_type = _node_type(label)
    common_name = {"name": _field("string", label="Name", default="")}
    material_flow_nodes = {
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
    typed_input = _options(
        ("str", "string"),
        ("num", "number"),
        ("bool", "boolean"),
        ("json", "JSON"),
        ("date", "timestamp"),
        ("flow", "flow context"),
        ("global", "global context"),
        ("msg", "msg property"),
    )
    node_scope_options = _options(
        ("all", "all nodes"),
        ("group", "in same group"),
        ("target", "selected nodes"),
    )
    if node_type == "http request":
        return {
            **common_name,
            "method": _field(
                "select",
                label="Method",
                default="GET",
                options=["GET", "POST", "PUT", "PATCH", "DELETE", "use"],
            ),
            "url": _field(
                "string",
                label="URL",
                required=True,
                default="",
                placeholder="http://example.com",
            ),
            "ret": _field(
                "select",
                label="Return",
                default="txt",
                options=_options(
                    ("txt", "UTF-8 string"),
                    ("bin", "binary buffer"),
                    ("obj", "parsed JSON object"),
                ),
            ),
            "headers": _field("object", label="Headers", default={}),
            "query": _field("object", label="Query Params", default={}),
            "payload": _field("string", label="Payload", default=""),
            "client_key": _field("string", label="Client Key", default=""),
            "client_secret": _field(
                "string", label="Client Secret", default="", secret=True
            ),
            "timeout_seconds": _field("number", label="Timeout Seconds", default=20),
        }
    if node_type == "http in":
        return {
            **common_name,
            "method": _field(
                "select",
                label="Method",
                default="GET",
                options=["GET", "POST", "PUT", "PATCH", "DELETE"],
            ),
            "path": _field(
                "string", label="URL", required=True, default="", placeholder="/path"
            ),
            "upload": _field("boolean", label="Accept file uploads", default=False),
            "client_key": _field("string", label="Client Key", default=""),
            "client_secret": _field(
                "string", label="Client Secret", default="", secret=True
            ),
        }
    if node_type == "http response":
        return {
            **common_name,
            "status_code": _field("number", label="Status Code", default=200),
            "headers": _field("object", label="Headers", default={}),
            "payload": _field("string", label="Payload", default=""),
        }
    if node_type in {"mqtt in", "mqtt out"}:
        return {
            **common_name,
            "broker_url": _field(
                "string", label="Broker URL", default="mqtt://localhost:1883"
            ),
            "host": _field("string", label="Host", default=""),
            "port": _field("number", label="Port", default=1883),
            "topic": _field("string", label="Topic", required=True, default=""),
            "client_id": _field("string", label="Client ID", default=""),
            "username": _field("string", label="Username", default=""),
            "password": _field("string", label="Password", default="", secret=True),
            "qos": _field("number", label="QoS", default=0),
            "retain": _field("boolean", label="Retain", default=False),
            "payload_template": _field("string", label="Payload Template", default=""),
        }
    if node_type in {"websocket in", "websocket out"}:
        return {
            **common_name,
            "url": _field("string", label="WebSocket URL", required=True, default=""),
            "headers": _field("object", label="Headers", default={}),
            "client_key": _field("string", label="Client Key", default=""),
            "client_secret": _field(
                "string", label="Client Secret", default="", secret=True
            ),
            "message_template": _field("string", label="Message Template", default=""),
            "timeout_seconds": _field("number", label="Timeout Seconds", default=20),
        }
    if node_type in {"tcp in", "tcp out", "udp in", "udp out"}:
        return {
            **common_name,
            "host": _field("string", label="Host", default=""),
            "port": _field("number", label="Port", required=True, default=None),
            "mode": _field(
                "select", label="Mode", default="client", options=["client", "server"]
            ),
            "encoding": _field("string", label="Encoding", default="utf-8"),
            "message_template": _field("string", label="Message Template", default=""),
            "timeout_seconds": _field("number", label="Timeout Seconds", default=20),
        }
    if node_type == "function":
        return {
            **common_name,
            "func": _field("code", label="Function", default="\nreturn msg;"),
            "outputs": _field("number", label="Outputs", default=1),
            "initialize": _field("code", label="Setup", default=""),
            "finalize": _field("code", label="On Stop", default=""),
        }
    if node_type == "switch":
        return {
            **common_name,
            "property": _field("string", label="Property", default="payload"),
            "property_type": _field(
                "select", label="Property Type", default="msg", options=typed_input
            ),
            "checkall": _field(
                "select",
                label="Checking",
                default="true",
                options=_options(
                    ("true", "checking all rules"),
                    ("false", "stopping after first match"),
                ),
            ),
            "rules": _field("array", label="Rules", default=[]),
        }
    if node_type == "change":
        return {
            **common_name,
            "rules": _field(
                "array",
                label="Rules",
                default=[
                    {"t": "set", "p": "payload", "pt": "msg", "to": "", "tot": "str"}
                ],
            ),
        }
    if node_type == "range":
        return {
            **common_name,
            "property": _field("string", label="Property", default="payload"),
            "output_property": _field(
                "string", label="Output Property", default="payload"
            ),
            "input_min": _field("number", label="Input Min", default=0),
            "input_max": _field("number", label="Input Max", default=100),
            "output_min": _field("number", label="Output Min", default=0),
            "output_max": _field("number", label="Output Max", default=1),
            "clamp": _field("boolean", label="Clamp", default=True),
        }
    if node_type == "template":
        return {
            **common_name,
            "template": _field("string", label="Template", default=""),
            "field": _field("string", label="Output Property", default="payload"),
            "field_type": _field(
                "select", label="Property Type", default="msg", options=typed_input
            ),
            "syntax": _field(
                "select",
                label="Syntax",
                default="mustache",
                options=["mustache", "plain"],
            ),
        }
    if node_type == "delay":
        return {
            **common_name,
            "mode": _field(
                "select", label="Mode", default="delay", options=["delay", "rate"]
            ),
            "delay_ms": _field("number", label="Delay ms", default=1000),
            "rate_count": _field("number", label="Rate Count", default=1),
            "rate_window_ms": _field("number", label="Rate Window ms", default=1000),
        }
    if node_type == "trigger":
        return {
            **common_name,
            "op1": _field("string", label="Send", default="1"),
            "op1type": _field(
                "select", label="Send Type", default="str", options=typed_input
            ),
            "op2": _field("string", label="Then Send", default="0"),
            "op2type": _field(
                "select", label="Then Send Type", default="str", options=typed_input
            ),
            "duration_ms": _field("number", label="After ms", default=250),
            "extend": _field(
                "boolean", label="Extend delay if new message arrives", default=False
            ),
            "reset": _field("string", label="Reset if msg property equals", default=""),
        }
    if node_type in {"filter", "rbe"}:
        return {
            **common_name,
            "property": _field("string", label="Property", default="payload"),
            "mode": _field(
                "select",
                label="Mode",
                default="block-unless-value-changes",
                options=[
                    "block-unless-value-changes",
                    "block-unless-value-changes-ignore-initial",
                    "block-if-value-changes",
                    "narrowband",
                    "deadband",
                ],
            ),
            "gap": _field("number", label="Gap", default=0),
            "start": _field("string", label="Initial Value", default=""),
        }
    if node_type == "exec":
        return {
            **common_name,
            "command": _field("string", label="Command", required=True, default=""),
            "args": _field("array", label="Arguments", default=[]),
            "cwd": _field("string", label="Working Directory", default=""),
            "timeout_seconds": _field("number", label="Timeout Seconds", default=20),
            "append": _field("boolean", label="Append msg.payload", default=False),
            "use_spawn": _field("boolean", label="Use spawn", default=False),
        }
    if node_type == "csv":
        return {
            **common_name,
            "mode": _field(
                "select", label="Mode", default="parse", options=["parse", "serialize"]
            ),
            "property": _field("string", label="Property", default="payload"),
            "delimiter": _field("string", label="Delimiter", default=","),
            "has_header": _field("boolean", label="Has Header", default=True),
        }
    if node_type == "html":
        return {
            **common_name,
            "mode": _field(
                "select", label="Mode", default="parse", options=["parse", "serialize"]
            ),
            "property": _field("string", label="Property", default="payload"),
            "extract": _field(
                "select",
                label="Extract",
                default="text",
                options=["text", "links", "raw"],
            ),
        }
    if node_type in {"json", "xml", "yaml"}:
        return {
            **common_name,
            "mode": _field(
                "select", label="Mode", default="parse", options=["parse", "serialize"]
            ),
            "property": _field("string", label="Property", default="payload"),
        }
    if node_type in {"file", "file out"}:
        return {
            **common_name,
            "path": _field("string", label="Path", required=True, default=""),
            "encoding": _field("string", label="Encoding", default="utf-8"),
            "append": _field("boolean", label="Append", default=False),
            "create_dirs": _field("boolean", label="Create Directories", default=True),
        }
    if node_type == "file in":
        return {
            **common_name,
            "path": _field("string", label="Path", required=True, default=""),
            "encoding": _field("string", label="Encoding", default="utf-8"),
            "mode": _field(
                "select",
                label="Mode",
                default="text",
                options=["text", "json", "bytes"],
            ),
        }
    if node_type == "watch":
        return {
            **common_name,
            "path": _field("string", label="Path", required=True, default=""),
            "recursive": _field("boolean", label="Recursive", default=False),
            "events": _field(
                "array", label="Events", default=["created", "modified", "deleted"]
            ),
        }
    if node_type in {"incoming webhook", "n8n incoming", "openclaw incoming"}:
        return {
            **common_name,
            "path": _field("string", label="Path", default=""),
            "secret": _field("string", label="Secret", default="", secret=True),
            "provider": _field(
                "string", label="Provider", default=node_type.replace(" incoming", "")
            ),
        }
    if node_type in {"outgoing webhook", "n8n outgoing", "openclaw outgoing"}:
        return {
            **common_name,
            "url": _field("string", label="URL", required=True, default=""),
            "method": _field(
                "select",
                label="Method",
                default="POST",
                options=["GET", "POST", "PUT", "PATCH", "DELETE"],
            ),
            "headers": _field("object", label="Headers", default={}),
            "secret": _field("string", label="Secret", default="", secret=True),
            "provider": _field(
                "string", label="Provider", default=node_type.replace(" outgoing", "")
            ),
            "timeout_seconds": _field("number", label="Timeout Seconds", default=20),
        }
    if node_type == "gmail":
        return {
            **common_name,
            "to": _field("string", label="To", required=True, default=""),
            "cc": _field("string", label="Cc", default=""),
            "subject": _field("string", label="Subject", default=""),
            "body_template": _field("string", label="Body Template", default=""),
            "provider": _field("string", label="Provider", default="gmail"),
        }
    if node_type == "whatsapp":
        return {
            **common_name,
            "to": _field("string", label="To", required=True, default=""),
            "message_template": _field("string", label="Message Template", default=""),
            "provider": _field("string", label="Provider", default="whatsapp"),
        }
    if node_type == "audit queue":
        return {
            **common_name,
            "source": _field("string", label="Source", default="external_nats_queue"),
            "subject": _field("string", label="Subject", default="indus.audit.queue"),
            "queue": _field("string", label="Queue", default="indus-audit-websocket"),
            "websocket_path": _field(
                "string", label="WebSocket Path", default="/api/v1/ws/events"
            ),
            "audit_required": _field("boolean", label="Audit Required", default=True),
        }
    if node_type in {
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
    }:
        schema = {
            **common_name,
            "collection": _field(
                "string", label="Collection", default="gxp_change_controls"
            ),
            "record_id_property": _field(
                "string",
                label="Record ID Property",
                default="payload.change_control_id",
            ),
            "status_property": _field(
                "string", label="Status Property", default="payload.status"
            ),
            "required_role": _field(
                "string", label="Required Role", default="Quality Assurance"
            ),
            "endpoint": _field(
                "string",
                label="Endpoint",
                default=_backend_defaults("Compliance", node_type).get("path", ""),
            ),
            "method": _field(
                "select",
                label="Method",
                default=_backend_defaults("Compliance", node_type).get(
                    "method", "POST"
                ),
                options=["GET", "POST", "PUT", "PATCH", "DELETE"],
            ),
            "reason_required": _field("boolean", label="Reason Required", default=True),
            "signature_required": _field(
                "boolean",
                label="Signature Required",
                default=node_type in {"approval request", "electronic signature"},
            ),
            "audit_required": _field("boolean", label="Audit Required", default=True),
        }
        if node_type == "audit trail":
            schema.update(
                {
                    "immutable": _field("boolean", label="Immutable", default=True),
                    "append_only": _field("boolean", label="Append Only", default=True),
                    "hash_chained": _field(
                        "boolean", label="Hash Chained", default=True
                    ),
                    "part11_audit": _field(
                        "boolean", label="Part 11 Audit", default=True
                    ),
                    "visual_role": _field(
                        "string", label="Visual Role", default="immutable_audit_sink"
                    ),
                }
            )
        return schema
    if node_type in material_flow_nodes:
        schema = {
            **common_name,
            "batch_execution_record_id": _field(
                "string", label="Batch Execution Record", default=""
            ),
            "batch_id": _field("string", label="Batch ID", default=""),
            "process_definition_id": _field(
                "string", label="Process Definition", default=""
            ),
            "process_step_id": _field("string", label="Process Step", default=""),
            "bom_id": _field("string", label="BOM", default=""),
            "mbmr_template_id": _field("string", label="MBMR Template", default=""),
            "bmr_document_id": _field("string", label="BMR Document", default=""),
            "bpr_document_id": _field("string", label="BPR Document", default=""),
            "material_id": _field("string", label="Material", default=""),
            "material_lot_id": _field("string", label="Material Lot", default=""),
            "payload_path": _field(
                "string", label="Input Payload Path", default="payload"
            ),
            "output_path": _field("string", label="Output Path", default="payload"),
            "endpoint": _field(
                "string",
                label="Endpoint",
                default=_backend_defaults("Material Flow", node_type).get("path", ""),
            ),
            "method": _field(
                "select",
                label="Method",
                default=_backend_defaults("Material Flow", node_type).get(
                    "method", "POST"
                ),
                options=["GET", "POST", "PUT", "PATCH", "DELETE"],
            ),
            "audit_required": _field("boolean", label="Audit Required", default=True),
            "signature_required": _field(
                "boolean",
                label="Signature Required",
                default=node_type in {"dispense weighing", "bmr record", "bpr record"},
            ),
        }
        if node_type == "dispense weighing":
            schema.update(
                {
                    "target_quantity": _field(
                        "number", label="Target Quantity", default=None
                    ),
                    "actual_quantity": _field(
                        "number", label="Actual Quantity", default=None
                    ),
                    "unit": _field("string", label="Unit", default="kg"),
                    "tolerance": _field("number", label="Tolerance", default=None),
                    "scale_equipment_id": _field(
                        "string", label="Scale Equipment", default=""
                    ),
                }
            )
        if node_type == "ipc checkpoint":
            schema.update(
                {
                    "checkpoint_id": _field(
                        "string", label="Checkpoint ID", default=""
                    ),
                    "sampling_plan_id": _field(
                        "string", label="Sampling Plan", default=""
                    ),
                    "method_reference": _field(
                        "string", label="Method Reference", default=""
                    ),
                    "cqa_ids": _field("array", label="CQA IDs", default=[]),
                    "cpp_ids": _field("array", label="CPP IDs", default=[]),
                }
            )
        if node_type == "yield reconciliation":
            schema.update(
                {
                    "theoretical_yield": _field(
                        "number", label="Theoretical Yield", default=None
                    ),
                    "actual_yield": _field(
                        "number", label="Actual Yield", default=None
                    ),
                    "unit": _field("string", label="Unit", default="kg"),
                }
            )
        if node_type == "equipment usage":
            schema.update(
                {
                    "equipment_id": _field("string", label="Equipment", default=""),
                    "machine_id": _field("string", label="Machine", default=""),
                    "usage_type": _field(
                        "string", label="Usage Type", default="production"
                    ),
                }
            )
        if node_type == "area room usage":
            schema.update(
                {
                    "area_id": _field("string", label="Area", default=""),
                    "room_id": _field("string", label="Room", default=""),
                    "usage_type": _field(
                        "string", label="Usage Type", default="production"
                    ),
                }
            )
        return schema
    if node_type == "split":
        return {
            **common_name,
            "property": _field("string", label="Property", default="payload"),
            "separator": _field("string", label="Separator", default=""),
            "include_parts": _field(
                "boolean", label="Include Parts Metadata", default=True
            ),
        }
    if node_type == "join":
        return {
            **common_name,
            "property": _field("string", label="Property", default="payload"),
            "mode": _field(
                "select",
                label="Mode",
                default="array",
                options=["array", "string", "object"],
            ),
            "separator": _field("string", label="Separator", default=""),
            "key_property": _field("string", label="Key Property", default="topic"),
        }
    if node_type == "sort":
        return {
            **common_name,
            "property": _field("string", label="Property", default="payload"),
            "key": _field("string", label="Sort Key", default=""),
            "direction": _field(
                "select",
                label="Direction",
                default="ascending",
                options=["ascending", "descending"],
            ),
            "numeric": _field("boolean", label="Numeric", default=False),
        }
    if node_type == "batch":
        return {
            **common_name,
            "property": _field("string", label="Property", default="payload"),
            "size": _field("number", label="Batch Size", default=10),
            "overlap": _field("number", label="Overlap", default=0),
            "include_parts": _field(
                "boolean", label="Include Parts Metadata", default=True
            ),
        }
    if node_type == "inject":
        return {
            **common_name,
            "topic": _field("string", label="Topic", default=""),
            "payload": _field("string", label="Payload", default=""),
            "payload_type": _field(
                "select", label="Payload Type", default="date", options=typed_input
            ),
            "repeat": _field(
                "select",
                label="Repeat",
                default="",
                options=_options(
                    ("", "none"),
                    ("interval", "interval"),
                    ("interval-time", "interval between times"),
                    ("cron", "cron"),
                ),
            ),
            "repeat_seconds": _field("number", label="Interval Seconds", default=None),
            "once": _field("boolean", label="Inject once after start", default=False),
            "once_delay": _field(
                "number", label="Delay Before First Inject", default=0.1
            ),
        }
    if node_type == "debug":
        return {
            **common_name,
            "active": _field("boolean", label="Active", default=True),
            "tosidebar": _field("boolean", label="Send to sidebar", default=True),
            "console": _field("boolean", label="Send to system console", default=False),
            "tostatus": _field(
                "boolean", label="Show status below node", default=False
            ),
            "complete": _field("string", label="Output", default="payload"),
            "target_type": _field(
                "select",
                label="Output Type",
                default="msg",
                options=_options(
                    ("msg", "msg property"),
                    ("full", "complete msg object"),
                    ("jsonata", "JSONata expression"),
                ),
            ),
        }
    if node_type == "catch":
        return {
            "scope": _field(
                "select",
                label="Catch errors from",
                default="all",
                options=node_scope_options,
            ),
            "uncaught": _field(
                "boolean",
                label="Ignore errors already handled by other catch nodes",
                default=False,
            ),
            "target_nodes": _field("node-list", label="Selected Nodes", default=[]),
            **common_name,
        }
    if node_type == "status":
        return {
            "scope": _field(
                "select",
                label="Report status from",
                default="all",
                options=node_scope_options,
            ),
            "target_nodes": _field("node-list", label="Selected Nodes", default=[]),
            **common_name,
        }
    if node_type == "complete":
        return {
            "scope": _field(
                "select",
                label="Complete from",
                default="all",
                options=node_scope_options,
            ),
            "target_nodes": _field("node-list", label="Selected Nodes", default=[]),
            **common_name,
        }
    if node_type == "comment":
        return {
            **common_name,
            "text": _field("string", label="Comment", default=""),
        }
    if node_type in {"link in", "link out", "link call"}:
        return {
            **common_name,
            "mode": _field(
                "select", label="Mode", default="link", options=["link", "return"]
            ),
            "links": _field("node-list", label="Linked Nodes", default=[]),
            "link_id": _field("string", label="Link ID", default=""),
        }
    if node_type == "junction":
        return {
            **common_name,
            "mode": _field(
                "select", label="Mode", default="pass-through", options=["pass-through"]
            ),
        }
    if node_type == "subflow":
        return {
            **common_name,
            "subflow_id": _field("string", label="Subflow ID", default=""),
            "environment": _field("object", label="Environment", default={}),
        }
    if node_type in {"inject", "debug", "catch", "status", "complete", "comment"}:
        return {**common_name}
    return common_name


def _config_defaults(label: str) -> dict:
    defaults = {}
    for key, schema in _config_schema(label).items():
        defaults[key] = schema.get("default")
    return defaults


@lru_cache(maxsize=1)
def get_node_catalog() -> ProcessNodeCatalogResponse:
    categories: list[ProcessNodeCatalogCategory] = []
    nodes: list[ProcessNodeCatalogItem] = []
    for category, labels in NODE_CATALOG_DEFINITIONS:
        category_nodes = [
            ProcessNodeCatalogItem(
                type=_node_type(label),
                label=label,
                category=category,
                key=_catalog_key(category, label),
                description=_node_description(category, label),
                config_schema=_config_schema(label),
                defaults={
                    "node_type": _node_type(label),
                    "node_category": category,
                    "backend": _backend_defaults(category, label),
                    "config": _config_defaults(label),
                    "metadata": {
                        "nodeType": _node_type(label),
                        "nodeCategory": category,
                        "nodeCatalogKey": _catalog_key(category, label),
                        "backend": _backend_defaults(category, label),
                    },
                },
            )
            for label in labels
        ]
        categories.append(
            ProcessNodeCatalogCategory(category=category, nodes=category_nodes)
        )
        nodes.extend(category_nodes)
    return ProcessNodeCatalogResponse(categories=categories, nodes=nodes)


def _candidate_values(value: str) -> Iterable[str]:
    normalized = value.strip().lower()
    if not normalized:
        return ()
    values = {
        normalized,
        normalized.replace("-", " "),
        normalized.replace("_", " "),
        normalized.replace(" ", "-"),
        normalized.replace(" ", "_"),
    }
    if ":" in normalized:
        suffix = normalized.rsplit(":", 1)[-1].strip()
        values.update(_candidate_values(suffix))
    return values


def get_catalog_item(identifier: str) -> ProcessNodeCatalogItem | None:
    candidates = set(_candidate_values(identifier))
    if not candidates:
        return None
    for item in get_node_catalog().nodes:
        item_values = {
            item.type.lower(),
            item.label.lower(),
            item.key.lower(),
            item.type.lower().replace(" ", "-"),
            item.type.lower().replace(" ", "_"),
        }
        if candidates & item_values:
            return item
    return None


def catalog_metadata_for(identifier: str | None) -> dict:
    if not identifier:
        return {}
    item = get_catalog_item(identifier)
    if not item:
        return {}
    return {
        "nodeType": item.type,
        "nodeCategory": item.category,
        "nodeCatalogKey": item.key,
        "nodeLabel": item.label,
        "backend": _backend_defaults(item.category, item.label),
    }


def catalog_defaults_for(identifier: str | None) -> dict:
    if not identifier:
        return {}
    item = get_catalog_item(identifier)
    if not item:
        return {}
    return item.defaults
