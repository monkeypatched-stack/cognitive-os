from __future__ import annotations

from collections.abc import Mapping

from services.common.config import settings


def n8n_webhook_auth_headers(
    existing: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return headers for authenticated n8n webhook calls.

    Authentication is opt-in via N8N_WEBHOOK_SECRET. Existing headers win so callers
    can intentionally override the default header name/value for a specific node.
    """
    headers = dict(existing or {})
    secret = str(settings.N8N_WEBHOOK_SECRET or "").strip()
    if not secret:
        return headers
    header_name = str(
        settings.N8N_WEBHOOK_AUTH_HEADER or "X-Sentinel-Webhook-Secret"
    ).strip()
    if not header_name:
        header_name = "X-Sentinel-Webhook-Secret"
    headers.setdefault(header_name, secret)
    return headers
