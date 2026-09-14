"""Trace propagation middleware — satisfies INTRO-INV-003.

Sets trace_id and span_id on every request from X-Trace-ID / X-Request-ID headers
(or generates new ones). The context variables are then readable anywhere in the
call chain via services.common.trace_context.get_trace_id().
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from services.common.trace_context import (
    generate_span_id,
    generate_trace_id,
    set_span_id,
    set_trace_id,
)


class TraceMiddleware(BaseHTTPMiddleware):
    """Propagate or generate X-Trace-ID for every request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = (
            request.headers.get("X-Trace-ID")
            or request.headers.get("X-Request-ID")
            or generate_trace_id()
        )
        span_id = request.headers.get("X-Span-ID") or generate_span_id()

        set_trace_id(trace_id)
        set_span_id(span_id)

        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Span-ID"] = span_id
        return response
