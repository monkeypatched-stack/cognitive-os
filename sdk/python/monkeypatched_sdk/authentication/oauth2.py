"""
OAuth2 Client Credentials Handler
===================================

Handles the OAuth2 client_credentials grant flow:

    1. POST to token endpoint with client_id / client_secret
    2. Cache the bearer token until it expires (with a safety buffer)
    3. Transparently refresh on expiry

Uses httpx (already in platform requirements.txt).
"""

import logging
import time
from typing import Any, Dict, Optional

from ..contracts.exceptions import AuthenticationError

logger = logging.getLogger(__name__)

# Refresh the token this many seconds before it actually expires
_EXPIRY_BUFFER_SECONDS = 60


class OAuth2Handler:
    """
    OAuth2 Client Credentials authentication.

    Suitable for server-to-server integrations such as SAP, Dynamics,
    DataBricks, Snowflake, and any OAuth2-compatible API.

    Usage::

        auth = OAuth2Handler(
            token_endpoint="https://api.example.com/oauth/token",
            client_id="my-client-id",
            client_secret="my-client-secret",
            scope="read write",
        )

        # In your adapter.initialize():
        headers = await auth.authenticate()   # {"Authorization": "Bearer <token>"}
    """

    def __init__(
        self,
        token_endpoint: str,
        client_id: str,
        client_secret: str,
        scope: str = "",
        extra_params: Optional[Dict[str, str]] = None,
    ) -> None:
        self.token_endpoint = token_endpoint
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.extra_params = extra_params or {}

        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    async def authenticate(self) -> Dict[str, Any]:
        """
        Return bearer-token headers, refreshing if necessary.

        Returns
        -------
        dict – ``{"Authorization": "Bearer <token>"}``
        """
        if not await self.is_valid():
            await self.refresh()
        return {"Authorization": f"Bearer {self._token}"}

    async def is_valid(self) -> bool:
        """Return True if a non-expired token is cached."""
        return self._token is not None and not self._is_expired()

    async def refresh(self) -> None:
        """Fetch a new access token from the token endpoint."""
        try:
            import httpx
        except ImportError as exc:
            raise AuthenticationError("httpx is required for OAuth2Handler. Install with: pip install httpx") from exc

        payload: Dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.scope:
            payload["scope"] = self.scope
        payload.update(self.extra_params)

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self.token_endpoint,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                data = response.json()

        except httpx.HTTPStatusError as exc:
            raise AuthenticationError(
                f"OAuth2 token request failed ({exc.response.status_code}): {exc.response.text}"
            ) from exc
        except Exception as exc:
            raise AuthenticationError(f"OAuth2 token request error: {exc}") from exc

        self._token = data.get("access_token")
        if not self._token:
            raise AuthenticationError("OAuth2 response did not contain 'access_token'.")

        expires_in = int(data.get("expires_in", 3600))
        self._expires_at = time.monotonic() + expires_in - _EXPIRY_BUFFER_SECONDS
        logger.debug(
            "OAuth2 token refreshed; expires in %ds.",
            expires_in - _EXPIRY_BUFFER_SECONDS,
        )

    def _is_expired(self) -> bool:
        return time.monotonic() >= self._expires_at
