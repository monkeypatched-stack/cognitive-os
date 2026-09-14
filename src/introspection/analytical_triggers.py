"""Analytical trigger engine — detects specific failure topologies in the cognitive OS.

Three built-in triggers, each corresponding to a named failure mode:

  SHADOW_CAPABILITY    — Pipeline bottleneck with flat token usage; agent silently
                         looping on self-reflection or missing short-term memory cache.

  CONVERGENCE_DELAY    — TD-error not declining over successive policy updates;
                         high exploration ratio suggests policy is stuck oscillating
                         between backend strategies under concurrency pressure.

  CONTEXT_DRIFT        — Step-function accuracy drop in the world model paired with
                         elevated drift_score; earliest signal of an upstream model
                         update or regulatory standard change hitting the system
                         without prompt/template recalibration.

The engine is evaluated lazily (called by Lemon.dashboard() or Lemon.evaluate_triggers()).
It also increments internal counters so rate-limited hooks in observe_*() can call it
efficiently without evaluating on every single event.

Custom triggers can be registered with AnalyticalTriggerEngine.register().

All findings are immutable snapshots: dataclasses + to_dict() for JSON serialisation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import mean, stdev
from typing import Any, Callable

from src.introspection.semantic_trace import SemanticEventStore

logger = logging.getLogger("introspection.triggers")

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


# ---------------------------------------------------------------------------
# Finding schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerFinding:
    topology: str  # "shadow_capability" | "convergence_delay" | "context_drift" | custom
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    description: str
    evidence: dict[str, Any]
    recommendation: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "topology": self.topology,
            "severity": self.severity,
            "description": self.description,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Built-in trigger: Shadow Capability Problem
# ---------------------------------------------------------------------------


def _shadow_capability(store: SemanticEventStore) -> list[TriggerFinding]:
    """Detect pipeline bottlenecks whose latency is not caused by LLM work.

    Signature:
      - One or more pipeline steps have latency > 2× the step median (bottleneck)
      - Those bottleneck steps show tokens_used == 0  (no model inference happening)
      - AND at least one of:
          a) short-term cache miss rate in the same pipeline > 50% of recent reads
          b) ≥ 2 reflection events triggered by "uncertainty" in the same pipeline window
    """
    findings: list[TriggerFinding] = []

    for pipeline_id, graph in store.graphs.items():
        bottlenecks = graph.bottlenecks()
        if not bottlenecks:
            continue

        bottleneck_names = {b["step"] for b in bottlenecks}
        pipeline_steps = [s for s in store.steps if s.pipeline_id == pipeline_id]
        bottleneck_steps = [s for s in pipeline_steps if s.step_name in bottleneck_names]

        if not bottleneck_steps:
            continue

        # Condition: flat tokens on bottleneck steps
        flat_tokens = all(s.tokens_used == 0 for s in bottleneck_steps)
        if not flat_tokens:
            continue

        # Evidence A: short-term cache miss rate
        st_reads = [m for m in store.memory if m.memory_type == "short_term" and m.operation == "read"]
        st_misses = [m for m in st_reads if not m.hit]
        miss_rate = len(st_misses) / len(st_reads) if st_reads else 0.0
        cache_problem = miss_rate > 0.5 and len(st_reads) >= 3

        # Evidence B: reflection loop — uncertain self-corrections
        uncertainty_reflections = [r for r in store.reflections if r.triggered_by in ("uncertainty", "contradiction")]
        reflection_loop = len(uncertainty_reflections) >= 2

        if not (cache_problem or reflection_loop):
            continue

        avg_bottleneck_latency = mean(b["latency_ms"] for b in bottlenecks)
        median_latency = bottlenecks[0].get("median_ms", 0.0) if bottlenecks else 0.0

        findings.append(
            TriggerFinding(
                topology="shadow_capability",
                severity="HIGH",
                description=(
                    f"Pipeline {pipeline_id}: {len(bottleneck_names)} step(s) "
                    f"show {round(avg_bottleneck_latency, 1)} ms avg latency "
                    f"({round(avg_bottleneck_latency / median_latency, 1) if median_latency else '?'}× median) "
                    f"with zero token usage — agent loop or cache miss detected"
                ),
                evidence={
                    "pipeline_id": pipeline_id,
                    "bottleneck_steps": sorted(bottleneck_names),
                    "avg_bottleneck_ms": round(avg_bottleneck_latency, 2),
                    "median_step_ms": round(median_latency, 2),
                    "tokens_on_bottleneck": 0,
                    "cache_miss_rate": round(miss_rate, 4),
                    "cache_reads_sampled": len(st_reads),
                    "uncertainty_reflections": len(uncertainty_reflections),
                },
                recommendation=(
                    "1. Inspect short-term memory cache TTL and key construction for the "
                    "bottleneck capabilities. "
                    "2. Add a max_iterations guard to the agent's self-reflection loop. "
                    "3. Verify the MemoryAccessEvent hit=False stream for the affected pipeline "
                    "to isolate whether the problem is cache cold-start, eviction race, "
                    "or structural key mismatch."
                ),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Built-in trigger: Policy Convergence Delay
# ---------------------------------------------------------------------------

_CONVERGENCE_WINDOW = 20  # compare last N vs previous N updates per capability
_CONVERGENCE_THRESHOLD = 0.9  # recent_td must be < prev_td × this factor to be "converging"
_EXPLORATION_HIGH = 0.45  # exploration ratio above this is considered problematic when td is stuck


def _convergence_delay(store: SemanticEventStore) -> list[TriggerFinding]:
    """Detect policy updates that are not driving TD-error toward zero.

    Signature (per capability):
      - At least 2 × _CONVERGENCE_WINDOW learning events available
      - mean(|td_error|) over last N >= mean(|td_error|) over previous N × 0.9
        (i.e., NOT a meaningful improvement)
      - Exploration ratio in recent window > _EXPLORATION_HIGH
        (system is still flailing between alternatives)
    """
    events = list(store.learning)
    if not events:
        return []

    by_cap: dict[str, list] = {}
    for e in events:
        by_cap.setdefault(e.capability, []).append(e)

    findings: list[TriggerFinding] = []
    for cap, cap_events in by_cap.items():
        if len(cap_events) < _CONVERGENCE_WINDOW * 2:
            continue

        n = _CONVERGENCE_WINDOW
        recent = cap_events[-n:]
        prev = cap_events[-2 * n : -n]

        recent_td = mean(abs(e.td_error) for e in recent)
        prev_td = mean(abs(e.td_error) for e in prev)
        explore_rate = sum(1 for e in recent if e.exploration) / len(recent)

        # Already converged — skip
        if recent_td < 0.005:
            continue

        not_converging = recent_td >= prev_td * _CONVERGENCE_THRESHOLD
        high_explore = explore_rate > _EXPLORATION_HIGH

        if not (not_converging and high_explore):
            continue

        # Oscillation check: stdev of recent td_errors
        recent_td_vals = [abs(e.td_error) for e in recent]
        td_stdev = stdev(recent_td_vals) if len(recent_td_vals) >= 2 else 0.0
        oscillating = td_stdev > recent_td * 0.5

        findings.append(
            TriggerFinding(
                topology="convergence_delay",
                severity="HIGH",
                description=(
                    f"Capability '{cap}': TD-error not declining "
                    f"({round(prev_td, 4)} → {round(recent_td, 4)}) "
                    f"with exploration ratio {round(explore_rate, 2)} — "
                    f"policy{'oscillating' if oscillating else ' stuck'} "
                    f"on alternative strategies"
                ),
                evidence={
                    "capability": cap,
                    "td_error_recent": round(recent_td, 6),
                    "td_error_previous": round(prev_td, 6),
                    "td_stdev": round(td_stdev, 6),
                    "oscillating": oscillating,
                    "exploration_ratio": round(explore_rate, 4),
                    "window_size": n,
                    "total_updates": len(cap_events),
                    "recent_rewards": round(mean(e.reward for e in recent), 4),
                },
                recommendation=(
                    "1. Reduce exploration_rate for this capability or implement capability-scoped "
                    "ε-decay. "
                    "2. If the system is routing between Elasticsearch and Neo4j under concurrency "
                    "spikes, add a sticky-routing policy for the contested state. "
                    "3. Check whether the reward signal itself is oscillating "
                    "(conflicting feedback from two downstream subscribers)."
                ),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Built-in trigger: Silent Context Drift
# ---------------------------------------------------------------------------

_DRIFT_WINDOW = 10  # compare last N vs previous N world model events
_ACCURACY_DROP_STEP = 0.15  # accuracy must drop by this much to be "step-function"
_DRIFT_SCORE_ELEVATED = 0.25  # drift_score mean above this confirms systemic shift


def _context_drift(store: SemanticEventStore) -> list[TriggerFinding]:
    """Detect a sudden step-function degradation in world model accuracy.

    Signature:
      - At least 2 × _DRIFT_WINDOW world model events available
      - mean(accuracy) drops by > _ACCURACY_DROP_STEP between windows (step function,
        not gradual degradation which would be a different issue)
      - mean(drift_score) in recent window > _DRIFT_SCORE_ELEVATED
    """
    events = list(store.world)
    if len(events) < _DRIFT_WINDOW * 2:
        return []

    n = _DRIFT_WINDOW
    recent = events[-n:]
    previous = events[-2 * n : -n]

    recent_acc = mean(e.accuracy for e in recent)
    prev_acc = mean(e.accuracy for e in previous)
    recent_drift = mean(e.drift_score for e in recent)
    acc_drop = prev_acc - recent_acc

    if not (acc_drop >= _ACCURACY_DROP_STEP and recent_drift >= _DRIFT_SCORE_ELEVATED):
        return []

    affected_entities = sorted({e.entity for e in recent if e.drift_score > 0.3})

    # Characterise shape: step vs gradual
    # If the drop happened in a single sub-window it's a step; otherwise gradual.
    midpoint_acc = mean(e.accuracy for e in events[-2 * n : -n // 2])
    is_step = (prev_acc - midpoint_acc) < 0.05  # stable until recently

    return [
        TriggerFinding(
            topology="context_drift",
            severity="CRITICAL",
            description=(
                f"{'Step-function' if is_step else 'Rapid'} world model accuracy degradation: "
                f"{round(prev_acc, 3)} → {round(recent_acc, 3)} "
                f"(Δ = {round(acc_drop, 3)}) with drift_score {round(recent_drift, 3)} — "
                f"earliest indicator of upstream model or regulatory standard update"
            ),
            evidence={
                "accuracy_before": round(prev_acc, 4),
                "accuracy_after": round(recent_acc, 4),
                "accuracy_drop": round(acc_drop, 4),
                "drift_score_mean": round(recent_drift, 4),
                "drift_score_max": round(max(e.drift_score for e in recent), 4),
                "affected_entities": affected_entities,
                "is_step_function": is_step,
                "window_size": n,
            },
            recommendation=(
                "1. Audit downstream foundational model version tags — check if a model provider "
                "silently updated a base model or embedding version. "
                "2. Scan your compliance agent rule tables (GDPR, ISO 27001, etc.) for "
                "regulatory standard updates that haven't been reflected in prompt templates. "
                "3. Trigger a full world-model recalibration run using the simulation engine "
                "before re-enabling live traffic routing to affected entities: "
                f"{', '.join(affected_entities[:5]) or 'check affected_entities field'}."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Analytical Trigger Engine
# ---------------------------------------------------------------------------

_BuiltinCheck = Callable[[SemanticEventStore], list[TriggerFinding]]

_BUILTIN_CHECKS: list[tuple[str, _BuiltinCheck]] = [
    ("shadow_capability", _shadow_capability),
    ("convergence_delay", _convergence_delay),
    ("context_drift", _context_drift),
]


class AnalyticalTriggerEngine:
    """Evaluates all registered trigger checks against the semantic event store.

    Built-in checks run automatically.
    Custom checks can be registered with .register(name, fn).

    Rate limiting:
      evaluate_all() tracks how many times it has been called and suppresses
      repeat findings for the same topology within a cooldown window
      (_COOLDOWN_CALLS). This prevents alert storms when the same condition
      persists across many observation cycles.
    """

    _COOLDOWN_CALLS = 20  # suppress a repeat finding for N evaluations

    def __init__(self) -> None:
        self._checks: list[tuple[str, _BuiltinCheck]] = list(_BUILTIN_CHECKS)
        self._call_count: int = 0
        self._last_fired: dict[str, int] = {}  # topology → last call_count when fired
        self._history: list[TriggerFinding] = []

    def register(self, name: str, fn: _BuiltinCheck) -> None:
        """Add a custom trigger function.

        fn(store: SemanticEventStore) -> list[TriggerFinding]
        """
        self._checks.append((name, fn))

    def evaluate_all(self, store: SemanticEventStore) -> list[TriggerFinding]:
        """Run all checks. Returns only new findings (respects cooldown)."""
        self._call_count += 1
        findings: list[TriggerFinding] = []

        for name, check in self._checks:
            last = self._last_fired.get(name, -self._COOLDOWN_CALLS - 1)
            if self._call_count - last < self._COOLDOWN_CALLS:
                continue  # still in cooldown for this topology
            try:
                results = check(store)
            except Exception as exc:
                logger.debug("Trigger %r raised: %s", name, exc)
                continue
            if results:
                self._last_fired[name] = self._call_count
                findings.extend(results)
                self._history.extend(results)
                if len(self._history) > 200:
                    self._history = self._history[-100:]
                for f in results:
                    logger.warning("TRIGGER [%s/%s] %s", f.topology, f.severity, f.description)

        return findings

    def history(self, n: int = 20) -> list[dict[str, Any]]:
        """Return the most recent N trigger findings."""
        return [f.to_dict() for f in self._history[-n:]]

    def summary(self) -> dict[str, Any]:
        by_topology: dict[str, int] = {}
        for f in self._history:
            by_topology[f.topology] = by_topology.get(f.topology, 0) + 1
        return {
            "total_findings": len(self._history),
            "by_topology": by_topology,
            "call_count": self._call_count,
            "registered_checks": [name for name, _ in self._checks],
        }
