"""Performance Tester — benchmarks and performance testing.

Measures:
- Throughput
- Latency
- Resource usage
- Scalability
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4


@dataclass
class PerformanceResult:
    """Result of a performance test."""

    test_id: str = field(default_factory=lambda: f"perf-{uuid4().hex[:8]}")
    name: str = ""
    iterations: int = 0
    total_time_ms: float = 0.0
    avg_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    throughput: float = 0.0  # ops/sec
    success_rate: float = 1.0
    errors: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class PerformanceTester:
    """Benchmarks and performance testing.

    Measures:
    - Throughput
    - Latency
    - Resource usage
    - Scalability
    """

    def __init__(self):
        self._results: list[PerformanceResult] = []
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon

    async def benchmark(
        self,
        name: str,
        fn: Callable,
        iterations: int = 100,
        warmup: int = 10,
    ) -> PerformanceResult:
        """Run a benchmark."""
        result = PerformanceResult(name=name, iterations=iterations)

        # Warmup
        for _ in range(warmup):
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn()
                else:
                    fn()
            except Exception:
                pass

        # Benchmark
        latencies = []
        errors = 0

        start_total = time.monotonic()
        for _ in range(iterations):
            start = time.monotonic()
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn()
                else:
                    fn()
                latency = (time.monotonic() - start) * 1000
                latencies.append(latency)
            except Exception:
                errors += 1

        result.total_time_ms = (time.monotonic() - start_total) * 1000
        result.errors = errors
        result.success_rate = (iterations - errors) / iterations if iterations else 0

        if latencies:
            latencies.sort()
            result.avg_latency_ms = sum(latencies) / len(latencies)
            result.min_latency_ms = latencies[0]
            result.max_latency_ms = latencies[-1]
            result.p50_latency_ms = latencies[len(latencies) // 2]
            result.p95_latency_ms = latencies[int(len(latencies) * 0.95)]
            result.p99_latency_ms = latencies[int(len(latencies) * 0.99)]
            result.throughput = (
                iterations / (result.total_time_ms / 1000)
                if result.total_time_ms > 0
                else 0
            )

        self._results.append(result)

        if self._lemon:
            self._lemon.counter("testing.benchmarks_run")
            self._lemon.histogram("testing.throughput", result.throughput)
            self._lemon.histogram("testing.latency_p50", result.p50_latency_ms)

        return result

    def get_results(self) -> list[PerformanceResult]:
        return list(self._results)

    def summary(self) -> dict:
        if not self._results:
            return {"total": 0}

        throughputs = [r.throughput for r in self._results]
        latencies = [r.avg_latency_ms for r in self._results]

        return {
            "total": len(self._results),
            "avg_throughput": sum(throughputs) / len(throughputs),
            "avg_latency_ms": sum(latencies) / len(latencies),
        }
