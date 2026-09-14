"""Utilities for process definitions service."""

from __future__ import annotations

from urllib.parse import urlparse
import ipaddress

_BLOCKED_SCHEMES = {"file", "gopher", "dict", "ftp"}
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"}


def validate_outbound_url(url: str) -> bool:
    """Validate that a URL is safe for outbound requests (no SSRF)."""
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
