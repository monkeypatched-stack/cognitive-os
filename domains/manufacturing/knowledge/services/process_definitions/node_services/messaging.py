from __future__ import annotations

from typing import Any

from services.process_definitions.node_services.models import (
    NodeExecutionResult,
    config_value,
    render_template,
)


async def execute_messaging_node(
    node_type: str | None,
    config: dict,
    payload: dict[str, Any],
) -> NodeExecutionResult:
    recipient = str(config_value(config, "to", ""))
    if not recipient:
        return NodeExecutionResult(
            False, None, None, f"{node_type} recipient is required"
        )
    if node_type == "gmail":
        body_template = str(config.get("body_template") or "")
        body = (
            render_template(body_template, payload)
            if body_template
            else str(payload.get("body") or payload.get("payload") or "")
        )
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "queued",
                "provider": config_value(config, "provider", "gmail"),
                "to": recipient,
                "cc": config.get("cc") or "",
                "subject": config.get("subject") or payload.get("subject") or "",
                "body": body,
            },
        )
    if node_type == "whatsapp":
        message_template = str(config.get("message_template") or "")
        message = (
            render_template(message_template, payload)
            if message_template
            else str(payload.get("message") or payload.get("payload") or "")
        )
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "queued",
                "provider": config_value(config, "provider", "whatsapp"),
                "to": recipient,
                "message": message,
            },
        )
    return NodeExecutionResult(True, None, {"status": "queued"})
