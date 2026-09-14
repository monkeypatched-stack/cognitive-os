"""Consolidated Monitoring Module — Single source of truth using Lemon.

This module consolidates all monitoring functionality by importing from
Lemon (the existing cognitive observability hub).

DO NOT create new monitoring implementations here. Import from:
- introspection/lemon.py (cognitive observability hub)
- introspection/metrics.py (metrics collection)
- kernel/compile/metrics.py (kernel metrics)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agentos.monitoring")


# ═══════════════════════════════════════════════════════════════════════════════
# Re-export from kernel (DO NOT duplicate)
# ═══════════════════════════════════════════════════════════════════════════════

# Lemon (Cognitive Observability Hub)
try:
    from src.introspection.lemon import Lemon

    LEMON_AVAILABLE = True
except ImportError:
    LEMON_AVAILABLE = False
    logger.warning("Lemon not available - monitoring degraded")

# Metrics
try:
    from src.introspection.metrics import MetricsCollector

    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════════
# Unified Monitoring Interface
# ═══════════════════════════════════════════════════════════════════════════════


class MonitoringService:
    """Unified monitoring interface using Lemon.

    Composes existing Lemon components into a single entry point.
    """

    def __init__(self):
        self._lemon: Any = None
        self._metrics: Any = None

    async def initialize(self, elasticsearch_url: str | None = None) -> None:
        """Initialize monitoring with Lemon."""
        if LEMON_AVAILABLE:
            self._lemon = Lemon()
            if elasticsearch_url:
                try:
                    await self._lemon.connect_elasticsearch(elasticsearch_url)
                    logger.info("Lemon connected to Elasticsearch: %s", elasticsearch_url)
                except Exception as e:
                    logger.warning("Lemon Elasticsearch connection failed: %s", e)
            logger.info("Monitoring initialized with Lemon")
        else:
            logger.warning("Lemon not available - using fallback metrics")
            if METRICS_AVAILABLE:
                self._metrics = MetricsCollector()

    # ── Metrics ─────────────────────────────────────────────────

    def increment(self, name: str, value: int = 1) -> None:
        """Increment a counter."""
        if self._lemon:
            self._lemon.counter(name, value)
        elif self._metrics:
            self._metrics.counter(name, value)

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge value."""
        if self._lemon:
            self._lemon.gauge(name, value)
        elif self._metrics:
            self._metrics.gauge(name, value)

    def record_histogram(self, name: str, value: float) -> None:
        """Record a histogram value."""
        if self._lemon:
            self._lemon.histogram(name, value)
        elif self._metrics:
            self._metrics.histogram(name, value)

    # ── Tracing ─────────────────────────────────────────────────

    def start_trace(self, name: str) -> str:
        """Start a distributed trace."""
        if self._lemon:
            return self._lemon.start_trace(name)
        return ""

    def start_span(self, trace_id: str, name: str) -> str:
        """Start a span within a trace."""
        if self._lemon:
            return self._lemon.start_span(trace_id, name)
        return ""

    def finish_span(self, span_id: str, status: str = "ok") -> None:
        """Finish a span."""
        if self._lemon:
            self._lemon.finish_span(span_id, status)

    def finish_trace(self, trace_id: str) -> None:
        """Finish a trace."""
        if self._lemon:
            self._lemon.finish_trace(trace_id)

    # ── Health ──────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """Run health check."""
        if self._lemon:
            return await self._lemon.health_check()
        return {"status": "healthy"}

    async def overall_health(self) -> dict[str, Any]:
        """Get overall health status."""
        if self._lemon:
            return await self._lemon.overall_health()
        return {"status": "healthy"}

    # ── Semantic Events ─────────────────────────────────────────

    def observe_intent(self, intent: str, confidence: float = 1.0) -> None:
        """Record intent observation."""
        if self._lemon:
            self._lemon.observe_intent(intent, confidence)

    def observe_goal(self, goal: str, status: str = "active") -> None:
        """Record goal observation."""
        if self._lemon:
            self._lemon.observe_goal(goal, status)

    def observe_pipeline_step(self, step: str, status: str = "completed") -> None:
        """Record pipeline step."""
        if self._lemon:
            self._lemon.observe_pipeline_step(step, status)

    # ── Dashboard ───────────────────────────────────────────────

    def dashboard(self) -> dict[str, Any]:
        """Get dashboard data."""
        if self._lemon:
            return self._lemon.dashboard()
        return {"status": "no lemon"}

    def summary(self) -> dict[str, Any]:
        """Get monitoring summary."""
        if self._lemon:
            return self._lemon.summary()
        return {"status": "no lemon"}


# ═══════════════════════════════════════════════════════════════════════════════
# Gaps identified (to be implemented)
# ═══════════════════════════════════════════════════════════════════════════════

# 1. OpenTelemetry bridge
#    Status: Lemon has OpenTelemetry bridge, but not wired to new systems
#    Action: Wire KnowledgeGraph operations to Lemon's OpenTelemetry bridge

# 2. Prometheus metrics endpoint
#    Status: Exists at api/routes/metrics.py
#    Action: Verify it includes new system metrics
