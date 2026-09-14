"""API Gateway boundary enforcement for AgentOS.

External HTTP clients must enter through Kong (``kong/kong.yml``). Kong
sets ``X-Kong-Request-Id`` on every proxied request; AgentOS rejects
direct access when ``API_GATEWAY_REQUIRED=true`` (default in production
mode).

Health/readiness probes and Prometheus scrape paths are exempt so
Kubernetes can reach the control plane without traversing Kong.
"""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_KONG_REQUEST_ID = "x-kong-request-id"

# Paths reachable without Kong (K8s probes, in-cluster Prometheus).
_EXEMPT_EXACT = frozenset({"/live", "/ready", "/health", "/metrics"})
_EXEMPT_PREFIXES = (
    "/api/v1/agentos/prompt/health",
    "/api/v1/agentos/query/health",
)


def api_gateway_required() -> bool:
    raw = os.getenv("API_GATEWAY_REQUIRED", "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    try:
        from src.monkey_brain.kernel.production_gates import production_mode_enabled

        return production_mode_enabled()
    except ImportError:
        return False


def request_via_api_gateway(request: Request) -> bool:
    return bool(request.headers.get(_KONG_REQUEST_ID))


def is_exempt_gateway_path(path: str) -> bool:
    if path in _EXEMPT_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES)


class ApiGatewayBoundaryMiddleware(BaseHTTPMiddleware):
    """Reject external traffic that bypasses Kong."""

    async def dispatch(self, request: Request, call_next):
        if not api_gateway_required():
            return await call_next(request)
        path = request.url.path
        if is_exempt_gateway_path(path):
            return await call_next(request)
        if request_via_api_gateway(request):
            return await call_next(request)
        return JSONResponse(
            status_code=403,
            content={
                "error": "api_gateway_required",
                "detail": "External API access must use the Kong API Gateway",
            },
        )
