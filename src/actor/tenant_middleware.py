"""Tenant Middleware — Phase 2.2: Enforce tenant context throughout pipeline.

Decorators and context managers to ensure:
1. All requests validated with tenant_id
2. All database queries scoped to tenant
3. All responses verified for tenant leakage
"""

from __future__ import annotations

import functools
import logging
from contextvars import ContextVar
from typing import Any, Callable, TypeVar

logger = logging.getLogger("agentos.tenant_middleware")

F = TypeVar("F", bound=Callable[..., Any])

_tenant_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)


class TenantContext:
    """Async-safe tenant context using ContextVar.

    Each async task gets its own isolated tenant context.
    No cross-task contamination.
    """

    @classmethod
    def set_tenant(cls, tenant_id: str) -> None:
        """Set current tenant context."""
        _tenant_var.set(tenant_id)
        logger.debug("[tenant_context] Set tenant: %s", tenant_id)

    @classmethod
    def get_tenant(cls) -> str | None:
        """Get current tenant context."""
        return _tenant_var.get()

    @classmethod
    def clear(cls) -> None:
        """Clear tenant context."""
        _tenant_var.set(None)


def require_tenant(func: F) -> F:
    """Decorator: Ensure request has valid tenant_id.

    Validates:
    1. Request object has tenant_id
    2. tenant_id is non-empty string
    3. Sets TenantContext for downstream operations
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Extract request from args
        request = None
        if args and hasattr(args[0], "tenant_id"):
            request = args[0]
        elif "request" in kwargs:
            request = kwargs["request"]

        if request is None:
            raise ValueError("[tenant_middleware] No request object found")

        tenant_id = getattr(request, "tenant_id", None)
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"[tenant_middleware] Invalid tenant_id: {tenant_id}. Must be non-empty string.")

        # Set context for this request
        TenantContext.set_tenant(tenant_id)
        logger.debug("[tenant_middleware] Enforced tenant: %s", tenant_id)

        try:
            result = func(*args, **kwargs)
            return result
        finally:
            TenantContext.clear()

    return wrapper


def validate_response_tenant(func: F) -> F:
    """Decorator: Verify response doesn't leak cross-tenant data.

    Validates response before returning to ensure:
    1. Response tenant_id matches request tenant_id
    2. No data from other tenants in result
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Get current tenant context
        current_tenant = TenantContext.get_tenant()
        if not current_tenant:
            logger.warning("[tenant_middleware] No tenant context set")

        # Call function
        result = func(*args, **kwargs)

        # Verify response tenant_id if present
        if result and hasattr(result, "tenant_id"):
            if result.tenant_id != current_tenant:
                raise RuntimeError(
                    f"[tenant_middleware] SECURITY VIOLATION: "
                    f"Response tenant_id '{result.tenant_id}' "
                    f"doesn't match context '{current_tenant}'"
                )

        logger.debug("[tenant_middleware] Response validated for tenant: %s", current_tenant)
        return result

    return wrapper


def tenant_scoped_query(actor_id: str, tenant_id: str) -> tuple[str, str]:
    """Validate and scope query to (actor_id, tenant_id).

    Returns:
        Tuple of (actor_id, tenant_id) validated as safe

    Raises:
        ValueError: If validation fails
    """
    # Validate actor_id (alphanumeric + underscore/dash only)
    import re

    if not re.match(r"^[a-z0-9_-]{1,128}$", actor_id):
        raise ValueError(f"[tenant_middleware] Invalid actor_id: {actor_id}. Must match [a-z0-9_-]{{1,128}}")

    # Validate tenant_id
    if not re.match(r"^[a-z0-9_-]{1,128}$", tenant_id):
        raise ValueError(f"[tenant_middleware] Invalid tenant_id: {tenant_id}. Must match [a-z0-9_-]{{1,128}}")

    # Verify tenant context matches
    current_tenant = TenantContext.get_tenant()
    if current_tenant and current_tenant != tenant_id:
        raise ValueError(
            f"[tenant_middleware] SECURITY VIOLATION: "
            f"Query tenant_id '{tenant_id}' doesn't match context '{current_tenant}'"
        )

    logger.debug("[tenant_middleware] Query scoped to: %s/%s", tenant_id, actor_id)
    return actor_id, tenant_id


class TenantMiddleware:
    """Request/response middleware for tenant enforcement."""

    def __init__(self):
        """Initialize middleware."""
        self._violations = []

    def validate_request(self, request: Any) -> None:
        """Validate request has tenant_id.

        Args:
            request: Request object

        Raises:
            ValueError: If validation fails
        """
        tenant_id = getattr(request, "tenant_id", None)
        if not tenant_id:
            raise ValueError("[tenant_middleware] Request missing tenant_id. All requests must specify tenant_id.")

        TenantContext.set_tenant(tenant_id)

    def validate_response(self, response: Any, request: Any) -> None:
        """Validate response doesn't leak cross-tenant data.

        Args:
            response: Response object
            request: Original request

        Raises:
            RuntimeError: If cross-tenant leakage detected
        """
        request_tenant = getattr(request, "tenant_id", None)
        response_tenant = getattr(response, "tenant_id", None)

        if response_tenant and response_tenant != request_tenant:
            violation = {
                "type": "cross_tenant_leakage",
                "request_tenant": request_tenant,
                "response_tenant": response_tenant,
                "timestamp": __import__("datetime").datetime.now().isoformat(),
            }
            self._violations.append(violation)

            raise RuntimeError(
                f"[tenant_middleware] SECURITY VIOLATION: "
                f"Response leaks data from tenant '{response_tenant}' "
                f"to request from tenant '{request_tenant}'"
            )

    def get_violations(self) -> list[dict]:
        """Get list of detected violations.

        Returns:
            List of violation records
        """
        return list(self._violations)

    def clear_violations(self) -> None:
        """Clear violation history."""
        self._violations.clear()


# Global middleware instance
_global_middleware = TenantMiddleware()


def get_tenant_middleware() -> TenantMiddleware:
    """Get global tenant middleware instance."""
    return _global_middleware
