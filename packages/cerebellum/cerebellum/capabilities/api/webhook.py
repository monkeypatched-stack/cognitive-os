"""Webhook Capability — webhook integration."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
import ipaddress
from cerebellum.capability import Capability

_BLOCKED_SCHEMES = {"file", "gopher", "dict", "ftp"}
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"}


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme.lower() in _BLOCKED_SCHEMES:
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    hostname = parsed.hostname or ""
    if hostname in _BLOCKED_HOSTS:
        return False
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        pass
    return True


class WebhookCapability(Capability):
    """Webhook integration capability."""

    def __init__(self, url: str = "", secret: str = ""):
        super().__init__(name="webhook")
        self._url = url
        self._secret = secret

    async def execute(self, state: dict[str, Any], **kwargs) -> dict[str, Any]:
        """Send webhook."""
        import httpx

        url = state.get("url", self._url)
        if not _is_safe_url(url):
            return {"status_code": 0, "success": False, "error": "blocked: unsafe URL"}
        payload = state.get("payload", {})
        headers = state.get("headers", {"Content-Type": "application/json"})

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            response = await client.post(url, json=payload, headers=headers)
            return {
                "status_code": response.status_code,
                "success": 200 <= response.status_code < 300,
            }
