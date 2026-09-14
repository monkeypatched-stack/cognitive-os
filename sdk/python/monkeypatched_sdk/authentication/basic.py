"""
HTTP Basic Authentication Handler
====================================

Encodes username:password as Base64 and injects the Authorization header.

RFC 7617 compliant.
"""

import base64
from typing import Any, Dict


class BasicAuthHandler:
    """
    HTTP Basic Auth.

    Suitable for legacy REST APIs, internal services, and CMMS/ERP
    systems that do not support token-based auth.

    Usage::

        auth = BasicAuthHandler(username="user", password="pass")
        headers = await auth.authenticate()
        # {"Authorization": "Basic dXNlcjpwYXNz"}
    """

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self._header: str = self._encode()

    def _encode(self) -> str:
        token = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    async def authenticate(self) -> Dict[str, Any]:
        """Return the Authorization header dict."""
        return {"Authorization": self._header}

    async def is_valid(self) -> bool:
        """Valid as long as username and password are non-empty."""
        return bool(self.username and self.password)

    async def refresh(self) -> None:
        """No-op: basic auth credentials do not expire."""
