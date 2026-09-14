from __future__ import annotations

import json
from typing import Any

from services.process_definitions.node_services.models import (
    NodeExecutionResult,
    config_value,
    nested_value,
)


def node_type_from_record(node: dict) -> str | None:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return node.get("node_type") or metadata.get("nodeType") or metadata.get("type")


def inject_payload(config: dict, payload: dict[str, Any]) -> Any:
    if payload:
        return payload.get("payload", payload)
    raw = config.get("payload")
    payload_type = str(config_value(config, "payload_type", "json")).lower()
    if payload_type == "string":
        return "" if raw is None else str(raw)
    if payload_type == "number":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0
    if payload_type == "boolean":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"true", "1", "yes", "y", "on"}
    if isinstance(raw, (dict, list, int, float, bool)):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return raw


async def execute_common_node(
    node_type: str | None,
    node: dict,
    layout: dict | None,
    config: dict,
    payload: dict[str, Any],
) -> NodeExecutionResult:
    if node_type == "inject":
        message = {
            "topic": config.get("topic") or payload.get("topic"),
            "payload": inject_payload(config, payload),
        }
        return NodeExecutionResult(
            True, None, {"status": "injected", "message": message}
        )
    if node_type == "debug":
        if config.get("active") is False:
            return NodeExecutionResult(True, None, {"status": "disabled"})
        output = str(config_value(config, "output", "payload"))
        if output == "full":
            debug_value = payload
        elif output == "message":
            debug_value = payload.get("message", payload)
        else:
            debug_value = nested_value(
                payload, str(config_value(config, "property", "payload"))
            )
        return NodeExecutionResult(
            True, None, {"status": "debugged", "value": debug_value}
        )
    if node_type == "comment":
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "comment",
                "text": config.get("text")
                or node.get("title")
                or node.get("name")
                or "",
            },
        )
    if node_type in {"junction", "subflow"}:
        return NodeExecutionResult(
            True, None, {"status": "passed", "node_type": node_type, "message": payload}
        )
    if node_type in {"catch", "status", "complete", "link in"}:
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "registered",
                "node_type": node_type,
                "scope": config.get("scope"),
                "node_ids": config.get("node_ids") or [],
            },
        )
    if node_type in {"link out", "link call"}:
        link_id = str(config_value(config, "link_id", ""))
        linked = []
        if layout:
            for candidate in layout.get("nodes", []):
                candidate_config = (
                    candidate.get("config")
                    if isinstance(candidate.get("config"), dict)
                    else {}
                )
                candidate_type = node_type_from_record(candidate)
                if (
                    candidate_type == "link in"
                    and str(candidate_config.get("link_id") or "") == link_id
                ):
                    linked.append(candidate.get("node_id"))
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "linked",
                "node_type": node_type,
                "link_id": link_id,
                "target_node_ids": linked,
                "payload": payload,
            },
        )
    return NodeExecutionResult(True, None, {"status": "queued"})
