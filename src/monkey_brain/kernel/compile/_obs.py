"""Built-in observability for the compile package.

Every significant runtime operation emits a metric or event here. In production these
route to Lemon (when the runtime has booted it); otherwise they are a silent no-op — the
pure compile package never hard-depends on the running system. A test/override sink can
be installed to capture and assert on emitted telemetry.

Coarse structural events only (batch updates, compiles, cognitive cycles, knowledge
exchange, context changes) — never per-cell hot loops.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agentos.compile")

_sink: Any = None


class Recorder:
    """A capture sink for tests — records every counter/gauge/event."""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def counter(self, name: str, increment: int = 1, **tags) -> None:
        self.events.append(("counter", name, increment, tags))

    def gauge(self, name: str, value: float, **tags) -> None:
        self.events.append(("gauge", name, value, tags))

    def histogram(self, name: str, value: float, **tags) -> None:
        self.events.append(("histogram", name, value, tags))

    def event(self, name: str, **fields) -> None:
        self.events.append(("event", name, fields))

    def names(self) -> set[str]:
        return {e[1] for e in self.events}

    def count(self, name: str) -> int:
        return sum(1 for e in self.events if e[1] == name)


def set_sink(sink: Any) -> None:
    """Install an override sink (tests / custom exporters)."""
    global _sink
    _sink = sink


def clear_sink() -> None:
    global _sink
    _sink = None


def _lemon():
    try:
        from src.introspection.lemon import get_lemon

        return get_lemon()
    except Exception:
        return None


def counter(name: str, increment: int = 1, **tags) -> None:
    if _sink is not None:
        try:
            _sink.counter(name, increment=increment, **tags)
        except Exception:
            pass
        return
    lemon = _lemon()
    if lemon is not None:
        try:
            lemon.counter(name, increment=increment, **tags)
        except Exception:
            pass


def gauge(name: str, value: float, **tags) -> None:
    if _sink is not None:
        try:
            _sink.gauge(name, value, **tags)
        except Exception:
            pass
        return
    lemon = _lemon()
    if lemon is not None:
        try:
            lemon.gauge(name, value, **tags)
        except Exception:
            pass


def histogram(name: str, value: float, **tags) -> None:
    if _sink is not None:
        try:
            _sink.histogram(name, value, **tags)
        except Exception:
            pass
        return
    lemon = _lemon()
    if lemon is not None:
        try:
            lemon.histogram(name, value, **tags)
        except Exception:
            pass


def event(name: str, **fields) -> None:
    """A structured runtime event (behaviour trace), e.g. cognitive.cycle, world.batch,
    knowledge.share, runtime.checkpoint."""
    if _sink is not None:
        try:
            _sink.event(name, **fields)
        except Exception:
            pass
        return
    lemon = _lemon()
    if lemon is not None:
        try:
            # Lemon has no generic event(); fall back to a counter tagged with the fields.
            lemon.counter(name, increment=1, **{k: str(v) for k, v in fields.items()})
        except Exception:
            pass
    logger.debug("[obs] %s %s", name, fields)


def _current_request_trace_id() -> str:
    """Correlate with services.common.trace_middleware.TraceMiddleware's
    per-request X-Trace-ID (set as a contextvar, readable anywhere in the
    call chain) — WITHOUT this, Lemon's own Tracer auto-generates an
    unrelated trace_id the moment start_span() is called with no current
    trace, giving two disconnected trace-id spaces: the one a client sees
    echoed back in the response header, and the one the planner/execution/
    graph-update spans actually recorded under. Best-effort: returns ""
    (falls back to Lemon's own auto-generated trace_id) if the middleware
    isn't installed or this call is happening outside a request (e.g. a
    background auto-tick)."""
    try:
        from services.common.trace_context import get_trace_id

        return get_trace_id()
    except Exception:
        return ""


def start_span(name: str, component: str = "", **attributes: Any) -> None:
    """Gate 5 (Observability) — the tracing counterpart to counter/gauge/
    event above: same silent-no-op-off-the-running-system shape, so
    kernel/pipeline and kernel/knowledge_graph.py can wrap the four spans
    Lemon should carry (planner, execution, graph update — request is a
    FastAPI middleware, not called from here) without importing Lemon
    directly. No-op under a test sink (Recorder has no span concept;
    tests assert on counter/gauge/event, not span nesting)."""
    if _sink is not None:
        return
    lemon = _lemon()
    if lemon is not None:
        try:
            if lemon.tracer.get_current_trace() is None:
                request_trace_id = _current_request_trace_id()
                lemon.start_trace(name="request", trace_id=request_trace_id or None)
            lemon.start_span(name, component=component, **attributes)
        except Exception:
            pass


def finish_span(status: str = "ok") -> None:
    if _sink is not None:
        return
    lemon = _lemon()
    if lemon is not None:
        try:
            lemon.finish_span(status)
        except Exception:
            pass
