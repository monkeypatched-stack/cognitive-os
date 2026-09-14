"""Tracing — distributed trace management.

Every request receives a globally unique Trace ID.
Every operation inherits the same Trace ID.
Supports nested spans for hierarchical tracing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class Span:
    """A single span in a trace."""

    span_id: str = field(default_factory=lambda: f"span-{uuid4().hex[:12]}")
    trace_id: str = ""
    parent_span_id: str | None = None
    name: str = ""
    component: str = ""
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    status: str = "ok"  # ok | error | cancelled
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def finish(self, status: str = "ok") -> None:
        self.end_time = time.monotonic()
        self.status = status

    @property
    def duration_ms(self) -> float:
        if self.end_time is None:
            return (time.monotonic() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "component": self.component,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "attributes": self.attributes,
            "events": self.events,
        }


@dataclass
class Trace:
    """A complete trace of a request."""

    trace_id: str = field(default_factory=lambda: f"trace-{uuid4().hex[:12]}")
    name: str = ""
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    spans: list[Span] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def finish(self) -> None:
        self.end_time = time.monotonic()

    @property
    def duration_ms(self) -> float:
        if self.end_time is None:
            return (time.monotonic() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000

    def add_span(self, name: str, component: str = "", parent_span_id: str | None = None) -> Span:
        span = Span(
            trace_id=self.trace_id,
            parent_span_id=parent_span_id,
            name=name,
            component=component,
        )
        self.spans.append(span)
        return span

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "name": self.name,
            "duration_ms": round(self.duration_ms, 2),
            "span_count": len(self.spans),
            "spans": [s.to_dict() for s in self.spans],
            "metadata": self.metadata,
        }


class Tracer:
    """Distributed tracing manager.

    Responsibilities:
    - Create and manage traces
    - Create and manage spans
    - Track nested span relationships
    """

    def __init__(self):
        self._traces: dict[str, Trace] = {}
        self._current_trace: Trace | None = None
        self._current_span: Span | None = None

    def start_trace(self, name: str = "", trace_id: str | None = None, **metadata: Any) -> Trace:
        """Start a new trace.

        `trace_id`: when given, this Trace uses it verbatim instead of
        minting a fresh `trace-{uuid4()}` — lets a caller that already has
        an external trace identifier (e.g. ExecutionContext.trace_id) make
        this the SAME trace_id, rather than the two ever being independent,
        unsynchronized identifiers for what's conceptually one request.
        """
        trace = (
            Trace(trace_id=trace_id, name=name, metadata=metadata) if trace_id else Trace(name=name, metadata=metadata)
        )
        self._traces[trace.trace_id] = trace
        self._current_trace = trace
        return trace

    def start_span(self, name: str, component: str = "", **attributes: Any) -> Span:
        """Start a new span within the current trace."""
        if self._current_trace is None:
            self.start_trace()

        parent_id = self._current_span.span_id if self._current_span else None
        span = self._current_trace.add_span(
            name=name,
            component=component,
            parent_span_id=parent_id,
        )
        span.attributes.update(attributes)
        self._current_span = span
        return span

    def finish_span(self, status: str = "ok") -> None:
        """Finish the current span."""
        if self._current_span:
            self._current_span.finish(status)
            # Move to parent span
            if self._current_trace:
                for s in reversed(self._current_trace.spans):
                    if s.span_id == self._current_span.parent_span_id:
                        self._current_span = s
                        return
            self._current_span = None

    def finish_trace(self) -> None:
        """Finish the current trace."""
        if self._current_trace:
            self._current_trace.finish()
            self._current_trace = None
            self._current_span = None

    def get_trace(self, trace_id: str) -> Trace | None:
        return self._traces.get(trace_id)

    def get_current_trace(self) -> Trace | None:
        return self._current_trace

    def summary(self) -> dict:
        return {
            "total_traces": len(self._traces),
            "current_trace": (self._current_trace.trace_id if self._current_trace else None),
        }
