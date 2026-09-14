"""Prometheus-compatible metrics endpoint for production monitoring.

Exposes /metrics in OpenMetrics text format for Prometheus scraping.
Metrics are collected from Lemon (observability) and process-level counters.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Response

logger = logging.getLogger("agentos.metrics")
router = APIRouter()

_start_time = time.time()
_request_count = 0
_request_errors = 0
_checkpoint_count = 0


def _inc_requests() -> None:
    global _request_count
    _request_count += 1


def _inc_errors() -> None:
    global _request_errors
    _request_errors += 1


def _set_checkpoints(n: int) -> None:
    global _checkpoint_count
    _checkpoint_count = n


def _format_metrics() -> str:
    """Format all metrics in OpenMetrics text format."""
    uptime = time.time() - _start_time
    lines = [
        "# HELP agentos_uptime_seconds Total uptime in seconds",
        "# TYPE agentos_uptime_seconds gauge",
        f"agentos_uptime_seconds {uptime:.2f}",
        "",
        "# HELP agentos_request_total Total HTTP requests",
        "# TYPE agentos_request_total counter",
        f"agentos_request_total {_request_count}",
        "",
        "# HELP agentos_request_errors_total Total HTTP request errors",
        "# TYPE agentos_request_errors_total counter",
        f"agentos_request_errors_total {_request_errors}",
        "",
        "# HELP agentos_checkpoint_store_count Number of active checkpoints",
        "# TYPE agentos_checkpoint_store_count gauge",
        f"agentos_checkpoint_store_count {_checkpoint_count}",
        "",
    ]
    return "\n".join(lines) + "\n"


@router.get("/metrics")
async def metrics():
    """Prometheus-compatible metrics endpoint."""
    return Response(
        content=_format_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
