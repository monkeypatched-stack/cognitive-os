"""Internal service-to-service authentication for platform HTTP calls.

Actor Runtime ``POST /execute`` is internal-only. External clients call
``POST /actors/{id}/execute`` on the API Gateway; the control plane
proxies with ``X-Internal-Service-Token``.
"""

from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Request

_HEADER = "X-Internal-Service-Token"
_ENV_TOKEN = "INTERNAL_SERVICE_TOKEN"
_ENV_REQUIRED = "ACTOR_RUNTIME_INTERNAL_ONLY"


def internal_only_enabled() -> bool:
    raw = os.getenv(_ENV_REQUIRED, "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    try:
        from src.monkey_brain.kernel.production_gates import insecure_dev_mode

        return not insecure_dev_mode()
    except ImportError:
        return True


def internal_service_token() -> str:
    return os.getenv(_ENV_TOKEN, "")


def require_internal_service_token(request: Request) -> None:
    if not internal_only_enabled():
        return
    expected = internal_service_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="INTERNAL_SERVICE_TOKEN is not configured",
        )
    provided = request.headers.get(_HEADER, "")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=403,
            detail="Internal API — route through the API Gateway",
        )


def internal_service_headers() -> dict[str, str]:
    token = internal_service_token()
    if not token:
        return {}
    return {_HEADER: token}
