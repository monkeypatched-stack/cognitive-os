"""Runtime Telemetry — structured trace for every execution.

Every execution should emit structured telemetry:

Intent → IntentIR → Execution Graph ID → Selected Policy → Knowledge Sources →
Grounding Confidence → Repository Calls → Capability Invocations → Provider Calls →
LLM Calls → Execution Time → Response → Failures

This information should make every execution reproducible.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.telemetry")


@dataclass
class TelemetrySpan:
    """A single span in the execution trace."""

    name: str
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    status: str = "ok"
    metadata: dict[str, Any] = field(default_factory=dict)

    def finish(self, status: str = "ok") -> None:
        self.end_time = time.monotonic()
        self.status = status

    @property
    def duration_ms(self) -> float:
        if self.end_time is None:
            return 0.0
        return (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "metadata": self.metadata,
        }


@dataclass
class ExecutionTrace:
    """Complete trace for a single execution."""

    trace_id: str
    intent: str = ""
    intent_ir: dict[str, Any] | None = None
    execution_graph_id: str = ""
    selected_policy: str = ""
    knowledge_sources: list[str] = field(default_factory=list)
    grounding_confidence: float = 0.0
    repository_calls: int = 0
    capability_invocations: int = 0
    provider_calls: int = 0
    llm_calls: int = 0
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    response: dict[str, Any] | None = None
    failures: list[str] = field(default_factory=list)
    spans: list[TelemetrySpan] = field(default_factory=list)

    def add_span(self, name: str, metadata: dict[str, Any] | None = None) -> TelemetrySpan:
        """Add a new span to the trace."""
        span = TelemetrySpan(name=name, metadata=metadata or {})
        self.spans.append(span)
        return span

    def record_llm_call(self) -> None:
        self.llm_calls += 1

    def record_repository_call(self) -> None:
        self.repository_calls += 1

    def record_provider_call(self) -> None:
        self.provider_calls += 1

    def record_capability_invocation(self) -> None:
        self.capability_invocations += 1

    def record_failure(self, error: str) -> None:
        self.failures.append(error)

    def finish(self) -> None:
        self.end_time = time.monotonic()

    @property
    def duration_ms(self) -> float:
        if self.end_time is None:
            return 0.0
        return (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "intent": self.intent,
            "intent_ir": self.intent_ir,
            "execution_graph_id": self.execution_graph_id,
            "selected_policy": self.selected_policy,
            "knowledge_sources": self.knowledge_sources,
            "grounding_confidence": self.grounding_confidence,
            "repository_calls": self.repository_calls,
            "capability_invocations": self.capability_invocations,
            "provider_calls": self.provider_calls,
            "llm_calls": self.llm_calls,
            "duration_ms": round(self.duration_ms, 2),
            "response": self.response,
            "failures": self.failures,
            "spans": [s.to_dict() for s in self.spans],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


class TelemetryCollector:
    """Collects and manages execution traces."""

    def __init__(self, max_traces: int = 1000):
        self._traces: list[ExecutionTrace] = []
        self._max_traces = max_traces

    def start_trace(self, trace_id: str) -> ExecutionTrace:
        """Start a new execution trace."""
        trace = ExecutionTrace(trace_id=trace_id)
        self._traces.append(trace)
        if len(self._traces) > self._max_traces:
            self._traces = self._traces[-self._max_traces :]
        return trace

    def get_trace(self, trace_id: str) -> ExecutionTrace | None:
        """Get a trace by ID."""
        for trace in reversed(self._traces):
            if trace.trace_id == trace_id:
                return trace
        return None

    def get_recent_traces(self, limit: int = 10) -> list[ExecutionTrace]:
        """Get recent traces."""
        return self._traces[-limit:]

    def summary(self) -> dict[str, Any]:
        """Get summary of all traces."""
        if not self._traces:
            return {"total_traces": 0}

        total = len(self._traces)
        failures = sum(1 for t in self._traces if t.failures)
        avg_duration = sum(t.duration_ms for t in self._traces) / total

        return {
            "total_traces": total,
            "failures": failures,
            "success_rate": (total - failures) / total if total > 0 else 1.0,
            "avg_duration_ms": round(avg_duration, 2),
        }


# Module-level singleton
_collector: TelemetryCollector | None = None


def get_telemetry() -> TelemetryCollector:
    """Get the global telemetry collector."""
    global _collector
    if _collector is None:
        _collector = TelemetryCollector()
    return _collector
