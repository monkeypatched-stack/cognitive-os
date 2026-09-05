"""Performance Budgets — quantitative SLOs for the Cognitive Kernel.

Every operation has a latency budget. If exceeded, the system must
either degrade gracefully or report the violation.

The budgets are derived from the existing benchmark baseline:
    77 questions/second, 12.91ms average latency (2026-06-27)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class LatencyBudget:
    """SLO for a single operation type."""
    name: str = ""
    target_ms: float = 0.0        # target latency
    p50_ms: float = 0.0           # 50th percentile target
    p95_ms: float = 0.0           # 95th percentile target
    p99_ms: float = 0.0           # 99th percentile target
    max_ms: float = 0.0           # hard deadline (abort if exceeded)
    timeout_ms: float = 0.0       # connection/operation timeout

    def check(self, actual_ms: float) -> tuple[bool, str]:
        """Check if actual latency is within budget."""
        if actual_ms > self.max_ms:
            return False, f"exceeded max ({actual_ms:.0f} > {self.max_ms:.0f}ms)"
        if actual_ms > self.p99_ms:
            return True, f"exceeded p99 ({actual_ms:.0f} > {self.p99_ms:.0f}ms)"
        return True, "within budget"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "target_ms": self.target_ms,
            "p50_ms": self.p50_ms, "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms, "max_ms": self.max_ms,
            "timeout_ms": self.timeout_ms,
        }


# Default performance budgets
PERFORMANCE_BUDGETS = {
    # Cognitive Kernel operations
    "kernel.step": LatencyBudget(
        name="kernel.step", target_ms=50, p50_ms=40, p95_ms=100,
        p99_ms=200, max_ms=500, timeout_ms=1000,
    ),
    "kernel.simulate": LatencyBudget(
        name="kernel.simulate", target_ms=30, p50_ms=25, p95_ms=60,
        p99_ms=100, max_ms=200, timeout_ms=500,
    ),
    "kernel.plan": LatencyBudget(
        name="kernel.plan", target_ms=20, p50_ms=15, p95_ms=40,
        p99_ms=80, max_ms=150, timeout_ms=300,
    ),

    # Solver operations
    "solver.rule_engine": LatencyBudget(
        name="solver.rule_engine", target_ms=5, p50_ms=3, p95_ms=10,
        p99_ms=15, max_ms=30, timeout_ms=50,
    ),
    "solver.graph": LatencyBudget(
        name="solver.graph", target_ms=10, p50_ms=8, p95_ms=20,
        p99_ms=30, max_ms=50, timeout_ms=100,
    ),
    "solver.sat": LatencyBudget(
        name="solver.sat", target_ms=15, p50_ms=10, p95_ms=30,
        p99_ms=50, max_ms=100, timeout_ms=200,
    ),
    "solver.constraint": LatencyBudget(
        name="solver.constraint", target_ms=20, p50_ms=15, p95_ms=40,
        p99_ms=60, max_ms=100, timeout_ms=200,
    ),
    "solver.model_checker": LatencyBudget(
        name="solver.model_checker", target_ms=25, p50_ms=20, p95_ms=50,
        p99_ms=80, max_ms=150, timeout_ms=300,
    ),
    "solver.optimizer": LatencyBudget(
        name="solver.optimizer", target_ms=15, p50_ms=10, p95_ms=30,
        p99_ms=50, max_ms=100, timeout_ms=200,
    ),
    "solver.monte_carlo": LatencyBudget(
        name="solver.monte_carlo", target_ms=50, p50_ms=40, p95_ms=100,
        p99_ms=150, max_ms=300, timeout_ms=500,
    ),
    "solver.jepa": LatencyBudget(
        name="solver.jepa", target_ms=30, p50_ms=25, p95_ms=60,
        p99_ms=100, max_ms=200, timeout_ms=500,
    ),
    "solver.llm": LatencyBudget(
        name="solver.llm", target_ms=2000, p50_ms=1500, p95_ms=4000,
        p99_ms=8000, max_ms=15000, timeout_ms=30000,
    ),

    # Capability execution
    "capability.execute": LatencyBudget(
        name="capability.execute", target_ms=100, p50_ms=80, p95_ms=200,
        p99_ms=400, max_ms=1000, timeout_ms=2000,
    ),
    "capability.rest": LatencyBudget(
        name="capability.rest", target_ms=200, p50_ms=150, p95_ms=400,
        p99_ms=800, max_ms=2000, timeout_ms=5000,
    ),
    "capability.nats": LatencyBudget(
        name="capability.nats", target_ms=50, p50_ms=30, p95_ms=100,
        p99_ms=200, max_ms=500, timeout_ms=1000,
    ),

    # Knowledge operations
    "knowledge.fuse": LatencyBudget(
        name="knowledge.fuse", target_ms=10, p50_ms=8, p95_ms=20,
        p99_ms=30, max_ms=50, timeout_ms=100,
    ),
    "knowledge.retrieve": LatencyBudget(
        name="knowledge.retrieve", target_ms=30, p50_ms=20, p95_ms=60,
        p99_ms=100, max_ms=200, timeout_ms=500,
    ),

    # Agent mesh
    "agent.spawn": LatencyBudget(
        name="agent.spawn", target_ms=5, p50_ms=3, p95_ms=10,
        p99_ms=20, max_ms=50, timeout_ms=100,
    ),
    "agent.execute": LatencyBudget(
        name="agent.execute", target_ms=200, p50_ms=150, p95_ms=400,
        p99_ms=800, max_ms=2000, timeout_ms=5000,
    ),
    "agent.merge": LatencyBudget(
        name="agent.merge", target_ms=10, p50_ms=8, p95_ms=20,
        p99_ms=30, max_ms=50, timeout_ms=100,
    ),

    # Reasoning
    "reasoning.select": LatencyBudget(
        name="reasoning.select", target_ms=5, p50_ms=3, p95_ms=10,
        p99_ms=15, max_ms=30, timeout_ms=50,
    ),

    # Planetary cycle (Gate 9) — per-actor cost. GeographicEntityRuntime.tick()
    # (kernel/geography/runtime.py) awaits each occupant's actor-ticker
    # SERIALLY (`for occupant_id in ...: await self._actor_ticker(...)`,
    # no asyncio.gather), and the actor ticker invokes the LLM planner per
    # actor. Measured live this session: cycle of 10 actors = 30668.9ms
    # (~3067ms/actor) against a local Ollama backend. Because the cost is
    # per-actor and serial, this budget is intentionally per-actor, not a
    # flat cycle budget — multiply by live actor count for the expected
    # total. See docs/adr/016-performance-gate9.md for the scaling risk
    # this implies (actor_count * p95 approaching the 300s auto-tick
    # interval causes tick pile-up, observed live: "Previous planetary
    # tick still running, skipping this cycle").
    "planetary.cycle_per_actor": LatencyBudget(
        name="planetary.cycle_per_actor", target_ms=2500, p50_ms=3000,
        p95_ms=4500, p99_ms=6000, max_ms=10000, timeout_ms=15000,
    ),

    # Evidence fusion
    "evidence.fuse": LatencyBudget(
        name="evidence.fuse", target_ms=15, p50_ms=10, p95_ms=30,
        p99_ms=50, max_ms=100, timeout_ms=200,
    ),

    # End-to-end workload
    "workload.e2e": LatencyBudget(
        name="workload.e2e", target_ms=500, p50_ms=400, p95_ms=1000,
        p99_ms=2000, max_ms=5000, timeout_ms=10000,
    ),
    "workload.codegen": LatencyBudget(
        name="workload.codegen", target_ms=30000, p50_ms=20000, p95_ms=60000,
        p99_ms=120000, max_ms=300000, timeout_ms=600000,
    ),

    # Edge hybrid storage (kernel/edge/local_store.py + moss_retrieval.py) --
    # see tests/unit/test_edge_moss_sqlite_hybrid.py. Overridable per-run via
    # EDGE_<NAME>_P50_BUDGET_MS / _P95_ / _P99_ env vars (that test reads
    # these values as its own defaults, never hardcoding a second copy).
    "edge.sqlite_read": LatencyBudget(
        name="edge.sqlite_read", target_ms=0.05, p50_ms=0.05, p95_ms=0.20,
        p99_ms=0.50, max_ms=5.0, timeout_ms=100,
    ),
    "edge.sqlite_write": LatencyBudget(
        name="edge.sqlite_write", target_ms=0.5, p50_ms=0.5, p95_ms=1.0,
        p99_ms=2.0, max_ms=20.0, timeout_ms=100,
    ),
    "edge.moss_ingest": LatencyBudget(
        name="edge.moss_ingest", target_ms=5, p50_ms=5, p95_ms=10,
        p99_ms=20, max_ms=100, timeout_ms=5000,
    ),
    "edge.moss_retrieve": LatencyBudget(
        name="edge.moss_retrieve", target_ms=5, p50_ms=5, p95_ms=10,
        p99_ms=20, max_ms=100, timeout_ms=5000,
    ),
    "edge.hybrid_tick": LatencyBudget(
        name="edge.hybrid_tick", target_ms=5, p50_ms=5, p95_ms=10,
        p99_ms=20, max_ms=100, timeout_ms=5000,
    ),
    "edge.restart_ready": LatencyBudget(
        name="edge.restart_ready", target_ms=50, p50_ms=50, p95_ms=200,
        p99_ms=500, max_ms=5000, timeout_ms=30000,
    ),
}


@dataclass
class MemoryBudget:
    """SLO for heap growth of a repeated operation. Separate from
    LatencyBudget because the unit is KB, not ms."""
    name: str = ""
    per_unit_kb: float = 0.0      # target growth per single unit (e.g. per actor)
    max_total_kb: float = 0.0     # hard ceiling for the reference batch size
    reference_units: int = 1      # the batch size max_total_kb was measured at

    def check(self, growth_kb: float, units: int) -> tuple[bool, str]:
        budget_kb = self.per_unit_kb * units
        if growth_kb > budget_kb:
            return False, f"exceeded budget ({growth_kb:.0f}KB > {budget_kb:.0f}KB for {units} units)"
        return True, "within budget"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "per_unit_kb": self.per_unit_kb,
            "max_total_kb": self.max_total_kb, "reference_units": self.reference_units,
        }


# Memory-growth budgets (Gate 9). actor.heap_growth reuses the exact
# threshold already asserted live in tests/unit/test_operational_load.py
# (TestLoadMemoryGrowth.test_memory_at_100_actors: growth_kb < 10240 for
# 100 actors) rather than inventing a new number.
MEMORY_BUDGETS = {
    "actor.heap_growth": MemoryBudget(
        name="actor.heap_growth", per_unit_kb=102.4,
        max_total_kb=10240, reference_units=100,
    ),
}


class PerformanceMonitor:
    """Tracks actual latencies against budgets."""

    def __init__(self):
        self._observations: dict[str, list[float]] = {}

    def record(self, operation: str, latency_ms: float) -> tuple[bool, str]:
        """Record a latency observation and check against budget."""
        self._observations.setdefault(operation, []).append(latency_ms)
        budget = PERFORMANCE_BUDGETS.get(operation)
        if budget:
            return budget.check(latency_ms)
        return True, "no budget defined"

    def get_stats(self, operation: str) -> dict[str, Any]:
        """Get latency statistics for an operation."""
        obs = self._observations.get(operation, [])
        if not obs:
            return {"count": 0}
        obs_sorted = sorted(obs)
        n = len(obs_sorted)
        return {
            "count": n,
            "mean_ms": sum(obs) / n,
            "p50_ms": obs_sorted[min(n // 2, n - 1)],
            "p95_ms": obs_sorted[min(int(n * 0.95), n - 1)],
            "p99_ms": obs_sorted[min(int(n * 0.99), n - 1)],
            "max_ms": obs_sorted[-1],
            "budget": PERFORMANCE_BUDGETS.get(operation, {}).to_dict()
                if operation in PERFORMANCE_BUDGETS else None,
        }

    def get_all_stats(self) -> dict[str, dict]:
        return {op: self.get_stats(op) for op in self._observations}
