"""Metrics — performance and operational metrics collection.

Captures execution time, latency, throughput, resource usage,
and AI-specific metrics across all subsystems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from collections import defaultdict


@dataclass
class Metric:
    """A single metric measurement."""

    metric_id: str = field(default_factory=lambda: f"metric-{uuid4().hex[:8]}")
    name: str = ""
    value: float = 0.0
    unit: str = ""  # ms | count | bytes | ratio | tokens
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tags: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class MetricsCollector:
    """Collects and aggregates metrics across all subsystems.

    Responsibilities:
    - Record metric values
    - Aggregate metrics (count, sum, avg, min, max)
    - Track rate metrics (requests/sec)
    - Export metrics for dashboards
    """

    def __init__(self):
        self._metrics: dict[str, list[Metric]] = defaultdict(list)
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)

    def record(self, name: str, value: float, unit: str = "", **tags: str) -> Metric:
        """Record a metric measurement."""
        metric = Metric(name=name, value=value, unit=unit, tags=tags)
        self._metrics[name].append(metric)
        return metric

    def counter(self, name: str, increment: int = 1, **tags: str) -> None:
        """Increment a counter."""
        key = f"{name}:{':'.join(f'{k}={v}' for k, v in sorted(tags.items()))}"
        self._counters[key] += increment

    def gauge(self, name: str, value: float, **tags: str) -> None:
        """Set a gauge value."""
        key = f"{name}:{':'.join(f'{k}={v}' for k, v in sorted(tags.items()))}"
        self._gauges[key] = value

    def histogram(self, name: str, value: float, **tags: str) -> None:
        """Record a histogram value."""
        key = f"{name}:{':'.join(f'{k}={v}' for k, v in sorted(tags.items()))}"
        self._histograms[key].append(value)
        if len(self._histograms[key]) > 1000:
            self._histograms[key] = self._histograms[key][-500:]

    def get_counter(self, name: str, **tags: str) -> int:
        key = f"{name}:{':'.join(f'{k}={v}' for k, v in sorted(tags.items()))}"
        return self._counters.get(key, 0)

    def get_gauge(self, name: str, **tags: str) -> float | None:
        key = f"{name}:{':'.join(f'{k}={v}' for k, v in sorted(tags.items()))}"
        return self._gauges.get(key)

    def get_histogram_stats(self, name: str, **tags: str) -> dict:
        key = f"{name}:{':'.join(f'{k}={v}' for k, v in sorted(tags.items()))}"
        values = self._histograms.get(key, [])
        if not values:
            return {"count": 0}
        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
            "p50": sorted(values)[len(values) // 2],
            "p95": (sorted(values)[int(len(values) * 0.95)] if len(values) > 1 else values[0]),
            "p99": (sorted(values)[int(len(values) * 0.99)] if len(values) > 1 else values[0]),
        }

    def summary(self) -> dict:
        return {
            "counters": len(self._counters),
            "gauges": len(self._gauges),
            "histograms": len(self._histograms),
            "total_metrics": sum(len(v) for v in self._metrics.values()),
        }

    def export(self) -> dict[str, Any]:
        """Export all metrics for dashboards."""
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {k: self.get_histogram_stats(k.split(":")[0]) for k in self._histograms},
        }
