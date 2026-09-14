"""Request-scoped trace context via Python contextvars.

Satisfies INTRO-INV-003: every operation must propagate TraceID and SpanID.

Usage in middleware (set once per request):
    from services.common.trace_context import set_trace_id
    set_trace_id(request.headers.get("X-Trace-ID") or generate_trace_id())

Usage anywhere in the call chain (read):
    from services.common.trace_context import get_trace_id
    tid = get_trace_id()
"""

from __future__ import annotations

import secrets
from contextvars import ContextVar

_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
_span_id_var: ContextVar[str] = ContextVar("span_id", default="")


def set_trace_id(trace_id: str) -> None:
    _trace_id_var.set(trace_id)


def set_span_id(span_id: str) -> None:
    _span_id_var.set(span_id)


def get_trace_id() -> str:
    return _trace_id_var.get()


def get_span_id() -> str:
    return _span_id_var.get()


def generate_trace_id() -> str:
    return f"trace-{secrets.token_hex(12)}"


def generate_span_id() -> str:
    return f"span-{secrets.token_hex(8)}"
