"""Metrics & Observability — Phase 5 Production Hardening.

Provides:
- Request/response metrics
- Latency tracking (P50, P95, P99)
- Error rate monitoring
- Resource usage tracking
- Health status reporting
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from collections import deque

logger = logging.getLogger("agentos.metrics")


@dataclass
class LatencyStats:
    """Latency percentile statistics."""

    count: int = 0
    min_ms: float = float("inf")
    max_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    avg_ms: float = 0.0


@dataclass
class MetricsSample:
    """Single metrics sample."""

    timestamp: float
    latency_ms: float
    error: bool = False
    error_type: str = ""


class MetricsCollector:
    """Collects operational metrics for monitoring.

    Tracks:
    - Request counts and types
    - Latency percentiles (P50, P95, P99)
    - Error rates and types
    - Resource usage
    """

    def __init__(self, name: str, window_size: int = 1000) -> None:
        """Initialize metrics collector.

        Args:
            name: Component name
            window_size: Number of samples to keep for percentile calculation
        """
        self.name = name
        self.window_size = window_size
        self.samples: deque[MetricsSample] = deque(maxlen=window_size)

        # Counters
        self.total_requests = 0
        self.total_errors = 0
        self.total_latency_ms = 0.0

        # Error tracking
        self.error_counts: dict[str, int] = {}

        # Start time
        self.started_at = time.time()

    def record_success(self, latency_ms: float) -> None:
        """Record successful request."""
        self.total_requests += 1
        self.total_latency_ms += latency_ms
        self.samples.append(
            MetricsSample(
                timestamp=time.time(),
                latency_ms=latency_ms,
                error=False,
            )
        )

    def record_error(self, latency_ms: float, error_type: str = "unknown") -> None:
        """Record failed request."""
        self.total_requests += 1
        self.total_errors += 1
        self.total_latency_ms += latency_ms
        self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1
        self.samples.append(
            MetricsSample(
                timestamp=time.time(),
                latency_ms=latency_ms,
                error=True,
                error_type=error_type,
            )
        )

    def get_stats(self) -> dict[str, Any]:
        """Get current metrics statistics."""
        if not self.samples:
            return {
                "name": self.name,
                "total_requests": 0,
                "total_errors": 0,
                "error_rate": "0.0%",
                "latency": None,
            }

        # Calculate latency percentiles
        latencies = sorted([s.latency_ms for s in self.samples])
        count = len(latencies)

        latency_stats = LatencyStats(
            count=count,
            min_ms=latencies[0],
            max_ms=latencies[-1],
            p50_ms=latencies[count // 2],
            p95_ms=latencies[int(count * 0.95)],
            p99_ms=latencies[int(count * 0.99)],
            avg_ms=(self.total_latency_ms / self.total_requests if self.total_requests > 0 else 0.0),
        )

        error_rate = (self.total_errors / self.total_requests * 100) if self.total_requests > 0 else 0.0

        return {
            "name": self.name,
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "error_rate": f"{error_rate:.1f}%",
            "latency": {
                "min_ms": f"{latency_stats.min_ms:.2f}",
                "max_ms": f"{latency_stats.max_ms:.2f}",
                "avg_ms": f"{latency_stats.avg_ms:.2f}",
                "p50_ms": f"{latency_stats.p50_ms:.2f}",
                "p95_ms": f"{latency_stats.p95_ms:.2f}",
                "p99_ms": f"{latency_stats.p99_ms:.2f}",
            },
            "errors": self.error_counts,
            "uptime_seconds": int(time.time() - self.started_at),
        }


class HealthChecker:
    """Monitors system health with configurable thresholds."""

    def __init__(self) -> None:
        self.collectors: dict[str, MetricsCollector] = {}
        self.thresholds = {
            "error_rate_critical": 10.0,  # 10% = critical
            "error_rate_warning": 1.0,  # 1% = warning
            "latency_p99_critical": 5000.0,  # 5s = critical
            "latency_p99_warning": 1000.0,  # 1s = warning
        }

    def register_collector(self, collector: MetricsCollector) -> None:
        """Register a metrics collector."""
        self.collectors[collector.name] = collector

    def get_health_status(self) -> dict[str, Any]:
        """Get overall system health status."""
        status = "healthy"
        issues = []

        for name, collector in self.collectors.items():
            stats = collector.get_stats()

            # Check error rate
            error_rate = float(stats.get("error_rate", "0%").rstrip("%"))
            if error_rate >= self.thresholds["error_rate_critical"]:
                status = "critical"
                issues.append(f"{name}: error rate {error_rate:.1f}% (critical)")
            elif error_rate >= self.thresholds["error_rate_warning"]:
                if status != "critical":
                    status = "degraded"
                issues.append(f"{name}: error rate {error_rate:.1f}% (warning)")

            # Check P99 latency
            if stats.get("latency"):
                p99_str = stats["latency"].get("p99_ms", "0")
                p99 = float(p99_str)
                if p99 >= self.thresholds["latency_p99_critical"]:
                    status = "critical"
                    issues.append(f"{name}: P99 latency {p99:.0f}ms (critical)")
                elif p99 >= self.thresholds["latency_p99_warning"]:
                    if status != "critical":
                        status = "degraded"
                    issues.append(f"{name}: P99 latency {p99:.0f}ms (warning)")

        return {
            "status": status,
            "collectors": {name: c.get_stats() for name, c in self.collectors.items()},
            "issues": issues,
        }


# Global instances
_health_checker: HealthChecker | None = None


def get_health_checker() -> HealthChecker:
    """Get or create singleton health checker."""
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker()
    return _health_checker


def reset_health_checker() -> None:
    """Reset health checker (for testing)."""
    global _health_checker
    _health_checker = None
