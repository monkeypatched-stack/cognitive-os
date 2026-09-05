"""TickTrace / TickTraceRecorder (kernel/edge/tick_trace.py)."""
from __future__ import annotations

from src.monkey_brain.kernel.edge.tick_trace import TickTrace, TickTraceRecorder


def test_tick_trace_has_all_required_fields():
    t = TickTrace(tick_id="t1", actor_id="a1")
    d = t.to_dict()
    for key in [
        "tick_id", "actor_id", "context_cache_hit", "retrieval_method", "semantic_cache_hit",
        "world_state_cache_hit", "governance_origin", "policy_version", "delegation_cache_hit",
        "authorization_cache_hit", "negotiation_origin", "execution_origin", "ros_latency_ms",
        "network_round_trips", "network_bytes_in", "network_bytes_out", "stage_latency_ms", "total_latency_ms",
    ]:
        assert key in d


def test_recorder_accumulates_and_emits_via_telemetry():
    class _FakeTelemetry:
        def __init__(self):
            self.events = []

        def event(self, name, **fields):
            self.events.append((name, fields))

    telemetry = _FakeTelemetry()
    recorder = TickTraceRecorder("t1", "a1", telemetry=telemetry)
    recorder.set(context_cache_hit=True, governance_origin="LOCAL")
    recorder.record_stage("context_build", 0.5)
    recorder.record_stage("governance", 0.2)
    trace = recorder.finish(total_latency_ms=1.0)

    assert trace.context_cache_hit is True
    assert trace.governance_origin == "LOCAL"
    assert trace.stage_latency_ms == {"context_build": 0.5, "governance": 0.2}
    assert telemetry.events == [("edge.tick.trace", trace.to_dict())]


def test_recorder_without_telemetry_still_returns_a_trace():
    recorder = TickTraceRecorder("t2", "a2")
    trace = recorder.finish(total_latency_ms=2.0)
    assert trace.tick_id == "t2"
