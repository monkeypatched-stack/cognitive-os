"""mTLS middleware — delegates cert parsing to cerebellum.capabilities.security.mtls_parser (INV-003).

This module owns only the Starlette middleware wiring. All cert parsing lives in cerebellum.
"""

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_ENABLED = os.getenv("MTLS_ENABLED", "false").lower() == "true"


def _is_production() -> bool:
    for var in ("AGENTOS_ENV", "MONKEYBRAIN_ENV", "ENVIRONMENT", "ENV"):
        if os.getenv(var, "").strip().lower() in ("production", "prod"):
            return True
    return False


# Surface the risk at import time: mTLS off in production means service-to-
# service calls carry no transport-level client identity. This is a valid
# posture only when TLS/mTLS terminates at the mesh — warn loudly so an
# accidental prod deploy without it is visible rather than silent.
if not _ENABLED and _is_production():
    logger.warning(
        "MTLS_ENABLED is false in a production environment — inter-service calls "
        "have no transport-level client identity. Confirm mTLS terminates at the "
        "mesh, or set MTLS_ENABLED=true.",
    )

try:
    from cerebellum.capabilities.security.mtls_parser import (
        process_request_cert,
        ca_valid,
    )
except ImportError:

    def process_request_cert(headers):  # type: ignore[misc]
        return None

    def ca_valid(cert_info, ca_path=""):  # type: ignore[misc]
        return True


class MTLSMiddleware(BaseHTTPMiddleware):
    """Attach request.state.mtls_cert (dict | None). No-op unless MTLS_ENABLED=true."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request.state.mtls_cert = None

        if not _ENABLED:
            return await call_next(request)

        cert_info = process_request_cert(dict(request.headers))
        if cert_info is None:
            return await call_next(request)

        if not ca_valid(cert_info):
            return JSONResponse(
                {"detail": "Client certificate not trusted"}, status_code=401
            )

        request.state.mtls_cert = cert_info
        return await call_next(request)
