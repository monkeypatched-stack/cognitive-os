from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.process_definitions.helpers import process_definition as crud
from services.process_definitions.models.process_definition import (
    ProcessDefinitionCanvasBackendCall,
)
from services.process_definitions.node_services.executor import (
    COMPLIANCE_NODES,
    MATERIAL_FLOW_NODES,
    execute_canvas_node_service,
)

FLOW_RUN_COLLECTION = "process_definition_canvas_flow_runs"
TEMP_SOP_LAYOUT_PREFIX = "sop-simulation"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _node_id(node: dict[str, Any]) -> str:
    return str(
        node.get("node_id") or node.get("id") or node.get("canvas_node_id") or ""
    )


def _node_label(node: dict[str, Any]) -> str:
    return str(
        node.get("name")
        or node.get("title")
        or (node.get("data") or {}).get("label")
        or _node_id(node)
    ).strip()


def _node_type_from_record(node: dict[str, Any]) -> str | None:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return node.get("node_type") or metadata.get("nodeType") or metadata.get("type")


def _backend_call_from_node(
    node: dict[str, Any],
) -> ProcessDefinitionCanvasBackendCall | None:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    backend = (
        node.get("backend")
        if isinstance(node.get("backend"), dict)
        else metadata.get("backend")
    )
    backend = dict(backend) if isinstance(backend, dict) else {}
    if config.get("url") and not backend.get("url"):
        backend["url"] = config["url"]
    if config.get("method"):
        backend["method"] = config["method"]
    if not backend.get("service"):
        backend["service"] = "process_definition"
    if not backend.get("action"):
        backend["action"] = _node_type_from_record(node)
    return ProcessDefinitionCanvasBackendCall(**backend) if backend else None


def _edge_source(edge: dict[str, Any]) -> str:
    return str(
        edge.get("source_node_id")
        or edge.get("source")
        or edge.get("sourceNodeId")
        or ""
    )


def _edge_target(edge: dict[str, Any]) -> str:
    return str(
        edge.get("target_node_id")
        or edge.get("target")
        or edge.get("targetNodeId")
        or ""
    )


def _edge_label(edge: dict[str, Any]) -> str:
    return (
        str(
            edge.get("label") or edge.get("sourceHandle") or edge.get("routeMode") or ""
        )
        .strip()
        .lower()
    )


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_token(value: Any, fallback: str) -> str:
    token = "".join(
        ch.lower() if ch.isalnum() else "-" for ch in str(value or fallback)
    )
    token = "-".join(part for part in token.split("-") if part)
    return token or fallback


def _condition_property(condition: dict[str, Any]) -> str:
    raw = (
        condition.get("property")
        or condition.get("field")
        or condition.get("parameter")
        or condition.get("check_command")
        or condition.get("name")
        or condition.get("id")
        or "value"
    )
    token = _safe_token(raw, "value").replace("-", "_")
    return f"payload.checks.{token}"


def _condition_rule(condition: dict[str, Any]) -> dict[str, Any]:
    operator = str(condition.get("operator") or "exists").strip().lower()
    operator_map = {
        "greater_than": "gt",
        "less_than": "lt",
        "equals": "equals",
        "not_equals": "not_equals",
        "exists": "exists",
        "not_exists": "not_exists",
        "contains": "contains",
    }
    rule = {"operation": operator_map.get(operator, operator)}
    if "expected_value" in condition:
        rule["value"] = condition.get("expected_value")
    return rule


def _node(
    node_id: str,
    node_type: str,
    name: str,
    *,
    config: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "name": name,
        "config": config or {},
        "metadata": metadata or {},
    }


def _edge(
    edge_id: str,
    source: str,
    target: str,
    *,
    source_port: int = 0,
    label: str | None = None,
) -> dict[str, Any]:
    edge = {
        "edge_id": edge_id,
        "source_node_id": source,
        "target_node_id": target,
        "source_port": source_port,
    }
    if label:
        edge["label"] = label
    return edge


def _section_conditions(section: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in _as_list(
            (section or {}).get("conditions") if isinstance(section, dict) else []
        )
        if isinstance(item, dict)
    ]


def _section_constraints(section: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in _as_list(
            (section or {}).get("constraints") if isinstance(section, dict) else []
        )
        if isinstance(item, dict)
    ]


def _section_actions(section: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in _as_list(
            (section or {}).get("actions") if isinstance(section, dict) else []
        )
        if isinstance(item, dict)
    ]


def _step_records(sop: dict[str, Any]) -> list[dict[str, Any]]:
    process_definition = (
        sop.get("process_definition")
        if isinstance(sop.get("process_definition"), dict)
        else {}
    )
    steps_section = (
        process_definition.get("steps")
        if isinstance(process_definition.get("steps"), dict)
        else {}
    )
    steps = [
        item for item in _as_list(steps_section.get("steps")) if isinstance(item, dict)
    ]
    if steps:
        return sorted(steps, key=lambda item: int(item.get("sequence") or 999999))

    templates = [
        item
        for item in _as_list(process_definition.get("instruction_templates"))
        if isinstance(item, dict)
    ]
    instruction_steps: list[dict[str, Any]] = []
    for template in templates:
        for item in _as_list(template.get("steps")):
            if not isinstance(item, dict):
                continue
            instruction_steps.append(
                {
                    "id": item.get("process_step_id")
                    or f"{template.get('instruction_template_id', 'template')}-step-{item.get('sequence')}",
                    "sequence": item.get("sequence") or len(instruction_steps) + 1,
                    "name": item.get("title")
                    or item.get("instruction")
                    or "Instruction Step",
                    "description": item.get("instruction")
                    or item.get("acceptance_criteria")
                    or "",
                    "action_type": "manual",
                    "metadata": {
                        "source": "instruction_template",
                        "template_id": template.get("instruction_template_id"),
                    },
                }
            )
    if instruction_steps:
        return sorted(
            instruction_steps, key=lambda item: int(item.get("sequence") or 999999)
        )

    return [
        {
            "id": f"{process_definition.get('process_definition_id') or sop.get('id')}-step-001",
            "sequence": 1,
            "name": sop.get("title") or sop.get("id") or "SOP Step",
            "description": sop.get("purpose") or sop.get("scope") or "Execute SOP.",
            "action_type": "manual",
        }
    ]


def compile_sop_to_canvas_layout(
    sop: dict[str, Any],
    *,
    layout_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sop_id = str(sop.get("id") or sop.get("sop_id") or "sop")
    process_definition = (
        sop.get("process_definition")
        if isinstance(sop.get("process_definition"), dict)
        else {}
    )
    process_definition_id = str(
        process_definition.get("process_definition_id") or f"PROC-{sop_id}"
    )
    resolved_layout_id = (
        layout_id
        or f"{TEMP_SOP_LAYOUT_PREFIX}-{_safe_token(sop_id, 'sop')}-{uuid4().hex[:8]}"
    )
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    previous = "sop-start"
    fail_targets: list[str] = []

    nodes.append(
        _node(
            previous,
            "inject",
            f"Start {sop.get('title') or sop_id}",
            config={"payload": payload or {}, "payload_type": "json"},
            metadata={
                "compiled_from": "sop",
                "sop_id": sop_id,
                "process_definition_id": process_definition_id,
            },
        )
    )

    def append_linear(node: dict[str, Any], *, edge_label: str | None = None) -> str:
        nonlocal previous
        nodes.append(node)
        edges.append(
            _edge(
                f"{previous}-{node['node_id']}",
                previous,
                node["node_id"],
                label=edge_label,
            )
        )
        previous = node["node_id"]
        return previous

    def append_check(
        condition: dict[str, Any],
        section: str,
        index: int,
        *,
        step_id: str | None = None,
    ) -> str:
        nonlocal previous
        condition_id = str(condition.get("id") or f"{section}-{index}")
        node_id = f"{section}-{_safe_token(step_id, 'process')}-{_safe_token(condition_id, str(index))}"
        fail_id = f"{node_id}-failed"
        prop = _condition_property(condition)
        nodes.append(
            _node(
                node_id,
                "switch",
                condition.get("name") or f"{section.title()} {index}",
                config={
                    "property": prop,
                    "check_all": False,
                    "rules": [
                        _condition_rule(condition),
                        {
                            "operation": "not_equals",
                            "value": "__SOP_SIMULATION_NEVER_MATCH__",
                        },
                    ],
                },
                metadata={
                    "compiled_from": "sop_condition",
                    "section": section,
                    "condition": condition,
                    "process_definition_id": process_definition_id,
                    "process_step_id": step_id,
                    "mandatory": bool(condition.get("is_mandatory", True)),
                    "check_property": prop,
                },
            )
        )
        nodes.append(
            _node(
                fail_id,
                "audit queue",
                f"{condition.get('name') or condition_id} Failed",
                config={
                    "audit_required": True,
                    "signature_required": bool(condition.get("is_mandatory", True)),
                },
                metadata={
                    "compiled_from": "sop_condition_failure",
                    "section": section,
                    "condition": condition,
                    "process_definition_id": process_definition_id,
                    "process_step_id": step_id,
                },
            )
        )
        edges.append(_edge(f"{previous}-{node_id}", previous, node_id))
        edges.append(
            _edge(f"{node_id}-pass", node_id, "__NEXT__", source_port=0, label="pass")
        )
        edges.append(
            _edge(f"{node_id}-fail", node_id, fail_id, source_port=1, label="fail")
        )
        fail_targets.append(fail_id)
        previous = node_id
        return node_id

    pending_next_edges: list[dict[str, Any]] = []

    def close_pending_next(target_id: str) -> None:
        for edge in pending_next_edges:
            if edge["target_node_id"] == "__NEXT__":
                edge["target_node_id"] = target_id
        pending_next_edges.clear()

    for index, condition in enumerate(
        _section_conditions(sop.get("prechecks")), start=1
    ):
        append_check(condition, "precheck", index)
        pending_next_edges.extend(
            [edge for edge in edges if edge["target_node_id"] == "__NEXT__"]
        )

    for constraint_index, constraint in enumerate(
        _section_constraints(sop.get("constraints")), start=1
    ):
        constraint_node_id = f"constraint-{_safe_token(constraint.get('id') or constraint.get('name'), str(constraint_index))}"
        close_pending_next(constraint_node_id)
        append_linear(
            _node(
                constraint_node_id,
                "comment",
                constraint.get("name") or f"Constraint {constraint_index}",
                config={
                    "text": constraint.get("violation_message")
                    or constraint.get("description")
                    or ""
                },
                metadata={
                    "compiled_from": "sop_constraint",
                    "constraint": constraint,
                    "process_definition_id": process_definition_id,
                    "hard": bool(constraint.get("is_hard_constraint", True)),
                },
            )
        )

    for step_index, step in enumerate(_step_records(sop), start=1):
        step_id = str(step.get("id") or f"step-{step_index}")
        step_node_id = f"step-{_safe_token(step_id, str(step_index))}"
        close_pending_next(step_node_id)
        append_linear(
            _node(
                step_node_id,
                "change",
                step.get("name") or f"SOP Step {step_index}",
                config={
                    "rules": [
                        {
                            "operation": "set",
                            "property": f"payload.executed_steps.{_safe_token(step_id, str(step_index)).replace('-', '_')}",
                            "value": {
                                "step_id": step_id,
                                "sequence": step.get("sequence") or step_index,
                                "name": step.get("name"),
                                "description": step.get("description"),
                                "action_type": step.get("action_type") or "manual",
                            },
                            "value_type": "json",
                        }
                    ]
                },
                metadata={
                    "compiled_from": "sop_step",
                    "step": step,
                    "process_definition_id": process_definition_id,
                    "process_step_id": step_id,
                },
            )
        )
        for check_index, condition in enumerate(
            _section_conditions(step.get("prechecks")), start=1
        ):
            append_check(condition, "step-precheck", check_index, step_id=step_id)
            pending_next_edges.extend(
                [edge for edge in edges if edge["target_node_id"] == "__NEXT__"]
            )
        for check_index, condition in enumerate(
            _section_conditions(step.get("postchecks")), start=1
        ):
            append_check(condition, "step-postcheck", check_index, step_id=step_id)
            pending_next_edges.extend(
                [edge for edge in edges if edge["target_node_id"] == "__NEXT__"]
            )

    for index, condition in enumerate(
        _section_conditions(sop.get("postchecks")), start=1
    ):
        append_check(condition, "postcheck", index)
        pending_next_edges.extend(
            [edge for edge in edges if edge["target_node_id"] == "__NEXT__"]
        )

    corrective_actions = _section_actions(sop.get("corrective_actions"))
    if corrective_actions:
        for action_index, action in enumerate(corrective_actions, start=1):
            action_node_id = f"corrective-{_safe_token(action.get('id') or action.get('name'), str(action_index))}"
            nodes.append(
                _node(
                    action_node_id,
                    "implementation task",
                    action.get("name") or f"Corrective Action {action_index}",
                    config={"audit_required": True},
                    metadata={
                        "compiled_from": "sop_corrective_action",
                        "corrective_action": action,
                        "process_definition_id": process_definition_id,
                    },
                )
            )
            for fail_id in fail_targets:
                edges.append(
                    _edge(f"{fail_id}-{action_node_id}", fail_id, action_node_id)
                )

    end_id = "sop-complete"
    close_pending_next(end_id)
    append_linear(
        _node(
            end_id,
            "debug",
            "SOP Simulation Complete",
            config={"output": "full"},
            metadata={
                "compiled_from": "sop_complete",
                "sop_id": sop_id,
                "process_definition_id": process_definition_id,
            },
        )
    )

    return {
        "layout_id": resolved_layout_id,
        "page_id": "process_definition",
        "process_definition_id": process_definition_id,
        "source_sop_id": sop_id,
        "compiled_from": "sop",
        "nodes": nodes,
        "edges": [edge for edge in edges if edge.get("target_node_id") != "__NEXT__"],
    }


def _body_message(body: Any, fallback: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(body, dict):
        return fallback
    for key in ("next_message", "message"):
        value = body.get(key)
        if isinstance(value, dict):
            return value
    if isinstance(body.get("payload"), dict):
        return body["payload"]
    if body.get("message") is None and body.get("status") in {"filtered", "disabled"}:
        return None
    return fallback


def _route_value(body: Any, message: dict[str, Any] | None) -> str | None:
    sources = [
        body if isinstance(body, dict) else {},
        message if isinstance(message, dict) else {},
    ]
    for source in sources:
        value = source.get("route") or source.get("decision") or source.get("status")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def _target_node_label(body: Any, message: dict[str, Any] | None) -> str | None:
    sources = [
        body if isinstance(body, dict) else {},
        message if isinstance(message, dict) else {},
    ]
    for source in sources:
        value = source.get("target_node") or source.get("targetNode")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def _matched_outputs(body: Any) -> set[int]:
    if not isinstance(body, dict) or not isinstance(body.get("matched_outputs"), list):
        return set()
    outputs = set()
    for value in body["matched_outputs"]:
        try:
            outputs.add(int(value))
        except (TypeError, ValueError):
            continue
    return outputs


def _targeted_edges(
    *,
    outgoing: list[dict[str, Any]],
    body: Any,
    message: dict[str, Any] | None,
    nodes_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not outgoing or message is None:
        return []

    matched_outputs = _matched_outputs(body)
    if matched_outputs:
        matched = []
        for edge in outgoing:
            try:
                source_port = int(edge.get("source_port", 0))
            except (TypeError, ValueError):
                source_port = 0
            if source_port in matched_outputs:
                matched.append(edge)
        return matched

    target_label = _target_node_label(body, message)
    if target_label:
        targeted = [
            edge
            for edge in outgoing
            if _node_label(nodes_by_id.get(_edge_target(edge), {})).lower()
            == target_label
        ]
        if targeted:
            return targeted

    route = _route_value(body, message)
    if route:
        routed = [edge for edge in outgoing if _edge_label(edge) == route]
        if routed:
            return routed

    unlabeled = [edge for edge in outgoing if not _edge_label(edge)]
    return unlabeled or outgoing


def _side_effect_node_response(
    node_type: str | None,
    node: dict[str, Any],
    config: dict[str, Any],
    message: dict[str, Any],
) -> dict[str, Any]:
    if node_type in MATERIAL_FLOW_NODES:
        return {
            "status": "material_flow_simulated",
            "action": node_type,
            "endpoint": config.get("endpoint"),
            "method": config.get("method", "POST"),
            "message": message,
            "next_message": {**message, "material_flow_action": node_type},
            "would_write": True,
        }
    if node_type in COMPLIANCE_NODES:
        return {
            "status": "compliance_simulated",
            "action": node_type,
            "endpoint": config.get("endpoint"),
            "method": config.get("method", "POST"),
            "message": message,
            "next_message": {**message, "compliance_action": node_type},
            "audit_required": bool(config.get("audit_required", True)),
            "signature_required": bool(config.get("signature_required", False)),
            "would_publish": node_type
            in {"approval request", "audit queue", "electronic signature"},
        }
    return {
        "status": "side_effect_simulated",
        "action": node_type or _node_label(node),
        "message": message,
        "next_message": message,
        "would_call_external_system": True,
    }


def _is_side_effect_node(
    node_type: str | None, backend: ProcessDefinitionCanvasBackendCall | None
) -> bool:
    if node_type in COMPLIANCE_NODES or node_type in MATERIAL_FLOW_NODES:
        return True
    if node_type in {
        "http request",
        "mqtt out",
        "websocket out",
        "tcp out",
        "udp out",
        "outgoing webhook",
        "n8n outgoing",
        "openclaw outgoing",
        "gmail",
        "whatsapp",
        "file",
        "file out",
        "exec",
    }:
        return True
    return bool(backend and backend.url)


async def _execute_node_for_simulation(
    *,
    db: AsyncIOMotorDatabase,
    layout_id: str,
    node: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[bool, Any, str | None]:
    node_type = _node_type_from_record(node)
    config = (
        dict(node.get("config") or {}) if isinstance(node.get("config"), dict) else {}
    )
    backend = _backend_call_from_node(node)
    if node_type in {"delay", "trigger"}:
        config["delay_ms"] = 0
        config["duration_ms"] = 0
    if _is_side_effect_node(node_type, backend):
        return True, _side_effect_node_response(node_type, node, config, payload), None
    result = await execute_canvas_node_service(
        db=db,
        layout_id=layout_id,
        node_type=node_type,
        node=node,
        backend=backend,
        config=config,
        payload=payload,
    )
    return result.ok, result.response_body, result.error


async def simulate_canvas_flow(
    *,
    db: AsyncIOMotorDatabase,
    layout_id: str,
    payload: dict[str, Any],
    start_node_id: str | None = None,
    max_steps: int = 100,
    persist_trace: bool = True,
) -> dict[str, Any]:
    layout = await crud.get_canvas_layout_by_id(db, layout_id)
    if not layout:
        raise ValueError(f"ProcessDefinition canvas layout '{layout_id}' not found")

    nodes_by_id = {
        _node_id(node): node for node in layout.get("nodes") or [] if _node_id(node)
    }
    edges = [
        edge
        for edge in layout.get("edges") or []
        if _edge_source(edge) and _edge_target(edge)
    ]
    incoming_targets = {_edge_target(edge) for edge in edges}
    outgoing_by_source: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        outgoing_by_source.setdefault(_edge_source(edge), []).append(edge)

    starts = (
        [start_node_id]
        if start_node_id
        else [
            node_id
            for node_id, node in nodes_by_id.items()
            if node_id not in incoming_targets
            or _node_type_from_record(node)
            in {"inject", "trigger", "http in", "incoming webhook"}
        ]
    )
    starts = [node_id for node_id in starts if node_id in nodes_by_id]
    if not starts and nodes_by_id:
        starts = [next(iter(nodes_by_id))]

    flow_run_id = f"canvas-flow-run-{uuid4().hex}"
    queue = deque((node_id, dict(payload or {}), None) for node_id in starts)
    node_trace: list[dict[str, Any]] = []
    edges_taken: list[dict[str, Any]] = []
    final_messages: list[dict[str, Any]] = []
    blocked_constraints: list[dict[str, Any]] = []
    steps = 0

    while queue and steps < max_steps:
        node_id, message, via_edge = queue.popleft()
        node = nodes_by_id.get(node_id)
        if not node:
            continue
        steps += 1
        ok, response_body, error = await _execute_node_for_simulation(
            db=db,
            layout_id=layout_id,
            node=node,
            payload=message,
        )
        output_message = _body_message(response_body, message)
        trace_item = {
            "sequence": steps,
            "node_id": node_id,
            "node_name": _node_label(node),
            "node_type": _node_type_from_record(node),
            "input": message,
            "ok": ok,
            "status": (
                response_body.get("status") if isinstance(response_body, dict) else None
            ),
            "response_body": response_body,
            "error": error,
            "output": output_message,
            "via_edge": via_edge,
        }
        node_trace.append(trace_item)
        if not ok:
            blocked_constraints.append(
                {"node_id": node_id, "reason": error or "node simulation failed"}
            )
            continue

        outgoing = outgoing_by_source.get(node_id, [])
        selected_edges = _targeted_edges(
            outgoing=outgoing,
            body=response_body,
            message=output_message,
            nodes_by_id=nodes_by_id,
        )
        if not selected_edges or output_message is None:
            if output_message is not None:
                final_messages.append(output_message)
            continue
        for edge in selected_edges:
            target = _edge_target(edge)
            edge_trace = {
                "edge_id": edge.get("edge_id") or edge.get("id"),
                "source_node_id": node_id,
                "target_node_id": target,
                "label": edge.get("label"),
                "source_port": edge.get("source_port", 0),
            }
            edges_taken.append(edge_trace)
            queue.append((target, dict(output_message), edge_trace))

    status = "completed"
    if queue:
        status = "truncated"
        blocked_constraints.append({"reason": f"max_steps {max_steps} reached"})
    if any(not item["ok"] for item in node_trace):
        status = "failed"

    flow_run = {
        "flow_run_id": flow_run_id,
        "layout_id": layout_id,
        "mode": "simulate",
        "status": status,
        "input_message": payload or {},
        "start_node_ids": starts,
        "node_trace": node_trace,
        "edges_taken": edges_taken,
        "events_generated": [
            item["response_body"]
            for item in node_trace
            if isinstance(item.get("response_body"), dict)
            and (
                item["response_body"].get("would_publish")
                or item["response_body"].get("would_write")
            )
        ],
        "records_would_create": [
            item["response_body"]
            for item in node_trace
            if isinstance(item.get("response_body"), dict)
            and item["response_body"].get("would_write")
        ],
        "final_message": (
            final_messages[-1]
            if final_messages
            else (node_trace[-1]["output"] if node_trace else payload or {})
        ),
        "final_messages": final_messages,
        "blocked_constraints": blocked_constraints,
        "risk_summary": {
            "failed_nodes": sum(1 for item in node_trace if not item["ok"]),
            "side_effects_suppressed": sum(
                1
                for item in node_trace
                if isinstance(item.get("response_body"), dict)
                and (
                    item["response_body"].get("would_call_external_system")
                    or item["response_body"].get("would_publish")
                    or item["response_body"].get("would_write")
                )
            ),
        },
        "created_at": _now(),
    }
    if persist_trace:
        await db[FLOW_RUN_COLLECTION].insert_one(flow_run.copy())
    flow_run.pop("_id", None)
    return flow_run


async def simulate_sop_flow(
    *,
    db: AsyncIOMotorDatabase,
    sop: dict[str, Any],
    payload: dict[str, Any],
    start_node_id: str | None = None,
    max_steps: int = 100,
    persist_trace: bool = True,
) -> dict[str, Any]:
    layout = compile_sop_to_canvas_layout(sop, payload=payload)
    await db[crud.CANVAS_COLLECTION].insert_one(layout.copy())
    try:
        result = await simulate_canvas_flow(
            db=db,
            layout_id=layout["layout_id"],
            payload=payload,
            start_node_id=start_node_id,
            max_steps=max_steps,
            persist_trace=persist_trace,
        )
    finally:
        await db[crud.CANVAS_COLLECTION].delete_one({"layout_id": layout["layout_id"]})

    process_definition = (
        sop.get("process_definition")
        if isinstance(sop.get("process_definition"), dict)
        else {}
    )
    source_metadata = {
        "compiled_from": "sop",
        "source_sop_id": sop.get("id") or sop.get("sop_id"),
        "source_sop_title": sop.get("title"),
        "source_process_definition_id": process_definition.get("process_definition_id"),
        "compiled_layout": {
            "layout_id": layout["layout_id"],
            "node_count": len(layout.get("nodes") or []),
            "edge_count": len(layout.get("edges") or []),
        },
    }
    result.update(source_metadata)
    if persist_trace:
        await db[FLOW_RUN_COLLECTION].update_one(
            {"flow_run_id": result["flow_run_id"]},
            {"$set": source_metadata},
        )
    return result
