"""
Auth Factory
============

Creates the correct auth handler based on a strategy enum.

Usage::

    handler = AuthFactory.create(
        AuthStrategy.OAUTH2,
        token_endpoint="...",
        client_id="...",
        client_secret="...",
    )
    headers = await handler.authenticate()
"""

from enum import Enum
from typing import Any

from ..contracts.exceptions import ConfigurationError
from .api_key import APIKeyHandler
from .basic import BasicAuthHandler
from .jwt_handler import JWTHandler
from .oauth2 import OAuth2Handler


class AuthStrategy(Enum):
    """Supported authentication strategies."""

    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    JWT = "jwt"
    BASIC = "basic"
    NONE = "none"


class _NoOpAuthHandler:
    """Placeholder when no authentication is required."""

    async def authenticate(self) -> dict:
        return {}

    async def is_valid(self) -> bool:
        return True

    async def refresh(self) -> None:
        pass


class AuthFactory:
    """
    Factory that creates auth handler instances.

    Supported strategies
    --------------------
    OAUTH2    → OAuth2Handler  (token_endpoint, client_id, client_secret, scope?)
    API_KEY   → APIKeyHandler  (api_key, header_name?, header_prefix?)
    JWT       → JWTHandler     (secret, payload, algorithm?, expiry_seconds?)
    BASIC     → BasicAuthHandler (username, password)
    NONE      → no-op (no auth required)

    Extra kwargs are forwarded to the handler constructor.
    """

    @staticmethod
    def create(strategy: AuthStrategy, **kwargs: Any):
        """
        Instantiate and return the appropriate auth handler.

        Parameters
        ----------
        strategy : AuthStrategy
            Which authentication scheme to use.
        **kwargs
            Constructor arguments forwarded to the handler.
        """
        if strategy == AuthStrategy.OAUTH2:
            _require(kwargs, "token_endpoint", "client_id", "client_secret")
            return OAuth2Handler(**kwargs)

        if strategy == AuthStrategy.API_KEY:
            _require(kwargs, "api_key")
            return APIKeyHandler(**kwargs)

        if strategy == AuthStrategy.JWT:
            _require(kwargs, "secret", "payload")
            return JWTHandler(**kwargs)

        if strategy == AuthStrategy.BASIC:
            _require(kwargs, "username", "password")
            return BasicAuthHandler(**kwargs)

        if strategy == AuthStrategy.NONE:
            return _NoOpAuthHandler()

        raise ConfigurationError(f"Unknown auth strategy: {strategy}")

    @staticmethod
    def from_config(credentials: dict):
        """
        Auto-detect the auth strategy from an adapter credentials dict.

        Detection rules (in priority order):
        1. ``oauth_endpoint`` or ``token_endpoint`` present → OAUTH2
        2. ``api_key`` present → API_KEY
        3. ``jwt_secret`` present → JWT
        4. ``username`` + ``password`` present → BASIC
        5. Otherwise → NONE
        """
        if credentials.get("oauth_endpoint") or credentials.get("token_endpoint"):
            endpoint = credentials.get("oauth_endpoint") or credentials.get("token_endpoint")
            return AuthFactory.create(
                AuthStrategy.OAUTH2,
                token_endpoint=endpoint,
                client_id=credentials.get("client_id", ""),
                client_secret=credentials.get("client_secret", ""),
                scope=credentials.get("scope", ""),
            )

        if credentials.get("api_key"):
            return AuthFactory.create(
                AuthStrategy.API_KEY,
                api_key=credentials["api_key"],
                header_name=credentials.get("header_name", "X-API-Key"),
            )

        if credentials.get("jwt_secret"):
            return AuthFactory.create(
                AuthStrategy.JWT,
                secret=credentials["jwt_secret"],
                payload=credentials.get("jwt_payload", {}),
            )

        if credentials.get("username") and credentials.get("password"):
            return AuthFactory.create(
                AuthStrategy.BASIC,
                username=credentials["username"],
                password=credentials["password"],
            )

        return AuthFactory.create(AuthStrategy.NONE)


def _require(kwargs: dict, *keys: str) -> None:
    """Raise ConfigurationError if any required key is absent."""
    missing = [k for k in keys if k not in kwargs]
    if missing:
        raise ConfigurationError(f"AuthFactory: missing required parameters: {missing}")
