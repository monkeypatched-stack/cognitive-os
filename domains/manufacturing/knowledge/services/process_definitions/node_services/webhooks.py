from __future__ import annotations

from typing import Any

import httpx

from services.common.n8n_auth import n8n_webhook_auth_headers
from services.process_definitions.node_services.models import (
    NodeExecutionResult,
    config_value,
)


async def execute_webhook_node(
    node_type: str | None,
    config: dict,
    payload: dict[str, Any],
) -> NodeExecutionResult:
    provider = str(config_value(config, "provider", node_type or "webhook"))
    if node_type in {"incoming webhook", "n8n incoming", "openclaw incoming"}:
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "registered",
                "provider": provider,
                "path": config.get("path"),
                "message": "Incoming webhook nodes are registered as canvas node listeners; post to this node's incoming webhook endpoint.",
            },
        )
    url = str(config_value(config, "url", ""))
    if not url:
        return NodeExecutionResult(False, None, None, f"{node_type} URL is required")
    method = str(config_value(config, "method", "POST")).upper()
    headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
    if str(provider).lower() == "n8n":
        headers = n8n_webhook_auth_headers(headers)
    if config.get("secret"):
        headers = {**headers, "x-canvas-webhook-secret": str(config["secret"])}
    timeout_seconds = float(config_value(config, "timeout_seconds", 20))
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(method, url, json=payload, headers=headers)
        try:
            body = response.json()
        except Exception:
            body = response.text
        return NodeExecutionResult(
            response.is_success,
            response.status_code,
            {
                "status": "sent" if response.is_success else "failed",
                "provider": provider,
                "body": body,
            },
            None if response.is_success else response.text,
        )
    except httpx.HTTPError as exc:
        return NodeExecutionResult(False, None, None, str(exc))
