"""Per-tick observability trace (Section 19).

A plain, serializable record of where a single cognitive tick's time and
network usage went -- built by the caller (the actor runtime's tick loop)
from values it already has (cache_hit flags returned by the Cached*
wrappers in this package, governance origin from LocalGovernanceOutcome,
etc.), not derived by this module. This module only defines the shape and
a cheap way to emit it through the existing async telemetry path
(kernel/edge/telemetry.py) -- it never blocks the tick and never
replaces AuditLog (this is a performance/observability record, not a
governance or audit record).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class TickTrace:
    tick_id: str
    actor_id: str

    context_cache_hit: bool = False
    retrieval_method: str = ""  # "vector" | "keyword" | "keyword_fallback" | ""
    semantic_cache_hit: bool = False
    world_state_cache_hit: bool = False

    governance_origin: str = ""  # "LOCAL" | "CENTRAL" | "ESCALATED"
    policy_version: str = ""
    delegation_cache_hit: bool = False
    authorization_cache_hit: bool = False

    negotiation_origin: str = ""  # "LOCAL" | "CENTRAL" | ""
    execution_origin: str = ""  # "LOCAL" | "REMOTE" | ""
    ros_latency_ms: float | None = None

    network_round_trips: int = 0
    network_bytes_in: int = 0
    network_bytes_out: int = 0

    stage_latency_ms: dict[str, float] = field(default_factory=dict)
    total_latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TickTraceRecorder:
    """Accumulates one TickTrace's fields across a tick's stages, then
    emits it once, asynchronously, at the end -- so instrumentation never
    adds a synchronous call per stage on the hot path."""

    def __init__(self, tick_id: str, actor_id: str, *, telemetry: Any = None) -> None:
        self._tick_id = tick_id
        self._actor_id = actor_id
        self._fields: dict[str, Any] = {}
        self._stage_latency_ms: dict[str, float] = {}
        self._telemetry = telemetry

    def set(self, **fields: Any) -> None:
        self._fields.update(fields)

    def record_stage(self, stage: str, latency_ms: float) -> None:
        self._stage_latency_ms[stage] = latency_ms

    def finish(self, total_latency_ms: float) -> TickTrace:
        trace = TickTrace(
            tick_id=self._tick_id, actor_id=self._actor_id,
            stage_latency_ms=dict(self._stage_latency_ms), total_latency_ms=total_latency_ms,
            **self._fields,
        )
        if self._telemetry is not None:
            self._telemetry.event("edge.tick.trace", **trace.to_dict())
        return trace
