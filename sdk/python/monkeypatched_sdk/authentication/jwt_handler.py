"""
JWT Authentication Handler
============================

Signs a JWT payload with a shared secret or RSA private key and injects
it as a Bearer token.

Uses python-jose (already in platform requirements.txt).

Suitable for:
    - Service-to-service JWTs
    - Custom auth schemes that issue short-lived tokens
    - M2M authentication flows
"""

import logging
import time
from typing import Any, Dict, Optional

from ..contracts.exceptions import AuthenticationError

logger = logging.getLogger(__name__)

_DEFAULT_EXPIRY_SECONDS = 3600


class JWTHandler:
    """
    JWT Bearer token authentication.

    Parameters
    ----------
    secret:          Shared secret (HMAC) or PEM private key (RSA/EC).
    payload:         Base claims dict embedded in every token.
    algorithm:       JWT signing algorithm (default: HS256).
    expiry_seconds:  Token lifetime; 0 = no expiry claim.

    Usage::

        auth = JWTHandler(
            secret="my-shared-secret",
            payload={"sub": "my-adapter", "iss": "monkeypatched"},
            algorithm="HS256",
        )
        headers = await auth.authenticate()
        # {"Authorization": "Bearer <jwt>"}
    """

    def __init__(
        self,
        secret: str,
        payload: Dict[str, Any],
        algorithm: str = "HS256",
        expiry_seconds: int = _DEFAULT_EXPIRY_SECONDS,
    ) -> None:
        self.secret = secret
        self.base_payload = payload
        self.algorithm = algorithm
        self.expiry_seconds = expiry_seconds

        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

    async def authenticate(self) -> Dict[str, Any]:
        """Return a valid Bearer token header, generating one if needed."""
        if not await self.is_valid():
            await self.refresh()
        return {"Authorization": f"Bearer {self._token}"}

    async def is_valid(self) -> bool:
        """Return True if a valid, non-expired token is cached."""
        return self._token is not None and (self.expiry_seconds == 0 or time.monotonic() < self._token_expiry)

    async def refresh(self) -> None:
        """Generate a fresh JWT."""
        await self._generate_token()

    async def _generate_token(self) -> None:
        try:
            from jose import jwt
        except ImportError:
            try:
                import jwt as _jwt  # PyJWT fallback

                claims = dict(self.base_payload)
                if self.expiry_seconds > 0:
                    claims["exp"] = int(time.time()) + self.expiry_seconds
                    claims["iat"] = int(time.time())
                self._token = _jwt.encode(claims, self.secret, algorithm=self.algorithm)
                self._token_expiry = time.monotonic() + self.expiry_seconds - 60
                logger.debug("JWT generated via PyJWT.")
                return
            except ImportError:
                raise AuthenticationError(
                    "JWTHandler requires python-jose or PyJWT. Install with: pip install python-jose[cryptography]"
                )

        claims = dict(self.base_payload)
        if self.expiry_seconds > 0:
            claims["exp"] = int(time.time()) + self.expiry_seconds
            claims["iat"] = int(time.time())

        try:
            self._token = jwt.encode(claims, self.secret, algorithm=self.algorithm)
            self._token_expiry = time.monotonic() + self.expiry_seconds - 60
            logger.debug("JWT generated via python-jose.")
        except Exception as exc:
            raise AuthenticationError(f"JWT generation failed: {exc}") from exc
