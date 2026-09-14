"""Logging — structured logging for all subsystems.

Every log entry includes:
- Timestamp
- Trace ID
- Span ID
- Workflow ID
- Pipeline ID
- Capability ID
- User ID
- Severity
- Component
- Duration
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class LogEntry:
    """A structured log entry."""

    log_id: str = field(default_factory=lambda: f"log-{uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    severity: str = "info"  # debug | info | warn | error | fatal
    component: str = ""
    message: str = ""
    trace_id: str | None = None
    span_id: str | None = None
    workflow_id: str | None = None
    pipeline_id: str | None = None
    capability_id: str | None = None
    user_id: str | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "log_id": self.log_id,
            "timestamp": self.timestamp,
            "severity": self.severity,
            "component": self.component,
            "message": self.message,
        }
        if self.trace_id:
            d["trace_id"] = self.trace_id
        if self.span_id:
            d["span_id"] = self.span_id
        if self.pipeline_id:
            d["pipeline_id"] = self.pipeline_id
        if self.capability_id:
            d["capability_id"] = self.capability_id
        if self.user_id:
            d["user_id"] = self.user_id
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class StructuredLogger:
    """Structured logging manager.

    Responsibilities:
    - Create structured log entries
    - Enrich logs with trace context
    - Filter by severity
    - Export logs
    """

    def __init__(self, min_level: str = "info"):
        self._min_level = min_level
        self._entries: list[LogEntry] = []
        self._levels = {"debug": 0, "info": 1, "warn": 2, "error": 3, "fatal": 4}

    def _should_log(self, level: str) -> bool:
        return self._levels.get(level, 0) >= self._levels.get(self._min_level, 0)

    def log(
        self,
        severity: str,
        message: str,
        component: str = "",
        trace_id: str | None = None,
        span_id: str | None = None,
        pipeline_id: str | None = None,
        capability_id: str | None = None,
        duration_ms: float | None = None,
        **metadata: Any,
    ) -> LogEntry:
        if not self._should_log(severity):
            return None

        entry = LogEntry(
            severity=severity,
            component=component,
            message=message,
            trace_id=trace_id,
            span_id=span_id,
            pipeline_id=pipeline_id,
            capability_id=capability_id,
            duration_ms=duration_ms,
            metadata=metadata,
        )
        self._entries.append(entry)
        return entry

    def debug(self, message: str, **kwargs: Any) -> LogEntry:
        return self.log("debug", message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> LogEntry:
        return self.log("info", message, **kwargs)

    def warn(self, message: str, **kwargs: Any) -> LogEntry:
        return self.log("warn", message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> LogEntry:
        return self.log("error", message, **kwargs)

    def fatal(self, message: str, **kwargs: Any) -> LogEntry:
        return self.log("fatal", message, **kwargs)

    def get_entries(
        self,
        severity: str | None = None,
        component: str | None = None,
        trace_id: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        entries = self._entries
        if severity:
            entries = [e for e in entries if e.severity == severity]
        if component:
            entries = [e for e in entries if e.component == component]
        if trace_id:
            entries = [e for e in entries if e.trace_id == trace_id]
        return entries[-limit:]

    def summary(self) -> dict:
        counts = {}
        for e in self._entries:
            counts[e.severity] = counts.get(e.severity, 0) + 1
        return {"total": len(self._entries), "by_severity": counts}
