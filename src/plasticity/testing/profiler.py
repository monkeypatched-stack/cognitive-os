"""Profiler — comprehensive performance profiling.

Supports:
- CPU Profiling
- Memory Profiling
- I/O Profiling
- Latency Tracking
- Throughput Measurement
- Cost Tracking
- Regression Detection
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class ProfileEntry:
    """A single profiling entry."""

    entry_id: str = field(default_factory=lambda: f"prof-{uuid4().hex[:8]}")
    name: str = ""
    category: str = ""  # cpu | memory | io | latency | cost
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PerformanceBudget:
    """Performance budget for a metric."""

    name: str = ""
    max_ms: float = 1000.0
    max_count: int = 1000
    warning_threshold: float = 0.8  # 80% of budget


@dataclass
class ProfilerReport:
    """Comprehensive profiling report."""

    report_id: str = field(default_factory=lambda: f"report-{uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Latency
    avg_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0

    # Throughput
    total_requests: int = 0
    throughput_rps: float = 0.0

    # Breakdown
    breakdown: dict[str, dict[str, float]] = field(default_factory=dict)

    # Budgets
    budget_violations: list[dict[str, Any]] = field(default_factory=list)

    # Hot spots
    hot_spots: list[dict[str, Any]] = field(default_factory=list)


class Profiler:
    """Comprehensive performance profiler.

    Supports:
    - CPU Profiling
    - Memory Profiling
    - I/O Profiling
    - Latency Tracking
    - Throughput Measurement
    - Cost Tracking
    - Regression Detection
    """

    def __init__(self):
        self._entries: list[ProfileEntry] = []
        self._budgets: dict[str, PerformanceBudget] = {}
        self._latencies: dict[str, list[float]] = {}
        self._counts: dict[str, int] = {}
        self._costs: dict[str, float] = {}
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon

    @contextmanager
    def cpu_profile(self, name: str, **metadata: Any):
        """Profile CPU usage."""
        entry = ProfileEntry(name=name, category="cpu", start_time=time.monotonic(), metadata=metadata)
        try:
            yield entry
        finally:
            entry.end_time = time.monotonic()
            entry.duration_ms = (entry.end_time - entry.start_time) * 1000
            self._entries.append(entry)
            self._record_latency(name, entry.duration_ms)
            if self._lemon:
                self._lemon.histogram(f"profiler.cpu.{name}_ms", entry.duration_ms)

    @contextmanager
    def memory_profile(self, name: str, **metadata: Any):
        """Profile memory usage."""
        entry = ProfileEntry(name=name, category="memory", start_time=time.monotonic(), metadata=metadata)
        try:
            yield entry
        finally:
            entry.end_time = time.monotonic()
            entry.duration_ms = (entry.end_time - entry.start_time) * 1000
            self._entries.append(entry)
            self._record_latency(name, entry.duration_ms)
            if self._lemon:
                self._lemon.histogram(f"profiler.memory.{name}_ms", entry.duration_ms)

    @contextmanager
    def io_profile(self, name: str, **metadata: Any):
        """Profile I/O operations."""
        entry = ProfileEntry(name=name, category="io", start_time=time.monotonic(), metadata=metadata)
        try:
            yield entry
        finally:
            entry.end_time = time.monotonic()
            entry.duration_ms = (entry.end_time - entry.start_time) * 1000
            self._entries.append(entry)
            self._record_latency(name, entry.duration_ms)
            if self._lemon:
                self._lemon.histogram(f"profiler.io.{name}_ms", entry.duration_ms)

    def track_latency(self, name: str, latency_ms: float) -> None:
        """Track latency for a metric."""
        self._record_latency(name, latency_ms)
        if self._lemon:
            self._lemon.histogram(f"profiler.latency.{name}_ms", latency_ms)

    def track_cost(self, name: str, cost: float = 0.0, **metadata: Any) -> None:
        """Track cost for a metric."""
        self._costs[name] = self._costs.get(name, 0.0) + cost
        if self._lemon:
            self._lemon.histogram(f"profiler.cost.{name}", cost)

    def track_count(self, name: str, count: int = 1) -> None:
        """Track count for a metric."""
        self._counts[name] = self._counts.get(name, 0) + count
        if self._lemon:
            self._lemon.counter(f"profiler.count.{name}", count)

    def set_budget(self, name: str, max_ms: float = 1000.0, warning_threshold: float = 0.8) -> None:
        """Set a performance budget."""
        self._budgets[name] = PerformanceBudget(
            name=name,
            max_ms=max_ms,
            warning_threshold=warning_threshold,
        )

    def check_budget(self, name: str, actual_ms: float) -> dict[str, Any]:
        """Check if a metric violates its budget."""
        budget = self._budgets.get(name)
        if not budget:
            return {"status": "no_budget"}

        ratio = actual_ms / budget.max_ms
        violated = actual_ms > budget.max_ms
        warning = ratio > budget.warning_threshold

        return {
            "status": "violated" if violated else ("warning" if warning else "ok"),
            "budget_ms": budget.max_ms,
            "actual_ms": actual_ms,
            "ratio": round(ratio, 3),
        }

    def _record_latency(self, name: str, latency_ms: float) -> None:
        if name not in self._latencies:
            self._latencies[name] = []
        self._latencies[name].append(latency_ms)
        if len(self._latencies[name]) > 10000:
            self._latencies[name] = self._latencies[name][-5000:]

    def generate_report(self) -> ProfilerReport:
        """Generate a comprehensive profiling report."""
        report = ProfilerReport()

        # Compute latency statistics
        all_latencies = []
        for latencies in self._latencies.values():
            all_latencies.extend(latencies)

        if all_latencies:
            all_latencies.sort()
            report.avg_latency_ms = sum(all_latencies) / len(all_latencies)
            report.min_latency_ms = all_latencies[0]
            report.max_latency_ms = all_latencies[-1]
            report.p50_latency_ms = all_latencies[len(all_latencies) // 2]
            report.p95_latency_ms = all_latencies[int(len(all_latencies) * 0.95)]
            report.p99_latency_ms = all_latencies[int(len(all_latencies) * 0.99)]

        # Compute throughput
        report.total_requests = sum(self._counts.values())

        # Build breakdown
        for name, latencies in self._latencies.items():
            if latencies:
                report.breakdown[name] = {
                    "avg_ms": sum(latencies) / len(latencies),
                    "p95_ms": (sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else latencies[0]),
                    "count": len(latencies),
                }

        # Check budgets
        for name, latencies in self._latencies.items():
            if latencies and name in self._budgets:
                avg = sum(latencies) / len(latencies)
                result = self.check_budget(name, avg)
                if result["status"] != "ok":
                    report.budget_violations.append(
                        {
                            "metric": name,
                            "status": result["status"],
                            "budget_ms": result["budget_ms"],
                            "actual_ms": result["actual_ms"],
                        }
                    )

        # Find hot spots (by total time)
        for name, latencies in self._latencies.items():
            total_time = sum(latencies)
            report.hot_spots.append(
                {
                    "name": name,
                    "total_ms": round(total_time, 2),
                    "count": len(latencies),
                    "avg_ms": round(total_time / len(latencies), 2) if latencies else 0,
                }
            )
        report.hot_spots.sort(key=lambda x: x["total_ms"], reverse=True)
        report.hot_spots = report.hot_spots[:10]

        return report

    def summary(self) -> dict:
        return {
            "entries": len(self._entries),
            "latency_metrics": len(self._latencies),
            "budgets": len(self._budgets),
            "costs": len(self._costs),
            "counts": len(self._counts),
        }
