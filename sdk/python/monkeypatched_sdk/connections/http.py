"""
HTTP Connection Pool
=====================

Pools httpx.AsyncClient instances for REST API integrations.

httpx.AsyncClient is the recommended async HTTP client for the platform
(already in requirements.txt).

Usage::

    pool = HTTPConnectionPool(
        base_url="https://cmms.example.com",
        auth_handler=api_key_handler,
        pool_size=5,
    )
    await pool.initialize()

    async with pool.connection() as client:
        response = await client.post("/api/work-orders", json=payload)
"""

import logging
from typing import Any, Dict, Optional

from .pool import ConnectionPool

logger = logging.getLogger(__name__)


class HTTPConnectionPool(ConnectionPool):
    """
    Connection pool for HTTP/REST APIs using httpx.AsyncClient.

    Parameters
    ----------
    base_url:       Base URL prepended to every request.
    auth_handler:   Optional auth handler whose headers are merged in.
    headers:        Additional headers applied to every request.
    verify_ssl:     Set False to disable TLS verification (dev only).
    pool_size:      Number of pooled sessions.
    timeout:        Acquire-from-pool timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        auth_handler: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        verify_ssl: bool = True,
        health_path: str = "/health",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.base_url = base_url.rstrip("/")
        self.auth_handler = auth_handler
        self.default_headers = headers or {}
        self.verify_ssl = verify_ssl
        self.health_path = health_path

    async def create_connection(self) -> Any:
        """Create a new httpx.AsyncClient session."""
        try:
            import httpx
        except ImportError as exc:
            raise ImportError("httpx is required for HTTPConnectionPool. Install with: pip install httpx") from exc

        # Merge auth headers if available
        headers = dict(self.default_headers)
        if self.auth_handler is not None:
            try:
                auth_headers = await self.auth_handler.authenticate()
                headers.update(auth_headers)
            except Exception as exc:
                logger.warning("Auth handler failed during connection creation: %s", exc)

        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            verify=self.verify_ssl,
            timeout=httpx.Timeout(self.timeout),
        )

    async def is_healthy(self, connection: Any) -> bool:
        """Ping the health endpoint to verify the connection is live."""
        try:
            response = await connection.get(self.health_path, timeout=5)
            return response.status_code < 500
        except Exception:
            return False

    async def _close_connection(self, connection: Any) -> None:
        """Close an httpx.AsyncClient."""
        try:
            await connection.aclose()
        except Exception as exc:
            logger.debug("Error closing HTTP connection: %s", exc)
