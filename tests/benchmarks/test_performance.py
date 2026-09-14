"""Performance benchmarks for core cognitive endpoints.

Measures latency and throughput for /plan, /execute, /simulate, /compare, /learn.
Run with: pytest tests/benchmarks/test_performance.py -v
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

BASE_URL = "http://localhost:8031"


def _post(path: str, body: dict, timeout: float = 60.0) -> dict:
    """POST to the API and return the response."""
    import httpx

    resp = httpx.post(f"{BASE_URL}{path}", json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


@pytest.fixture(scope="module")
def ensure_server():
    """Check if the server is running."""
    import httpx

    try:
        httpx.get(f"{BASE_URL}/health", timeout=5)
    except Exception:
        pytest.skip("Server not running on port 8031")


@pytest.mark.benchmark
class TestPlanLatency:
    def test_plan_execute(self, ensure_server):
        t0 = time.monotonic()
        result = _post(
            "/api/v1/agentos/plan",
            {"question": "How many machines?", "target": "execute"},
        )
        elapsed = (time.monotonic() - t0) * 1000
        assert "run_id" in result
        assert elapsed < 10000, f"Plan took {elapsed:.0f}ms (>10s)"
        print(f"  /plan?target=execute: {elapsed:.0f}ms")

    def test_plan_simulate(self, ensure_server):
        t0 = time.monotonic()
        result = _post(
            "/api/v1/agentos/plan",
            {"question": "List all inventory", "target": "simulate"},
        )
        elapsed = (time.monotonic() - t0) * 1000
        assert "run_id" in result
        print(f"  /plan?target=simulate: {elapsed:.0f}ms")


@pytest.mark.benchmark
class TestExecuteLatency:
    def test_execute(self, ensure_server):
        plan = _post(
            "/api/v1/agentos/plan",
            {"question": "How many machines?", "target": "execute"},
        )
        t0 = time.monotonic()
        result = _post("/api/v1/agentos/execute", plan)
        elapsed = (time.monotonic() - t0) * 1000
        assert "answer" in result
        print(f"  /execute: {elapsed:.0f}ms")


@pytest.mark.benchmark
class TestSimulateLatency:
    def test_simulate(self, ensure_server):
        plan = _post(
            "/api/v1/agentos/plan",
            {"question": "How many machines?", "target": "simulate"},
        )
        t0 = time.monotonic()
        result = _post("/api/v1/agentos/simulate", plan)
        elapsed = (time.monotonic() - t0) * 1000
        assert "answer" in result
        print(f"  /simulate: {elapsed:.0f}ms")


@pytest.mark.benchmark
class TestCompareLatency:
    def test_compare(self, ensure_server):
        plan = _post(
            "/api/v1/agentos/plan",
            {"question": "How many machines?", "target": "compare"},
        )
        t0 = time.monotonic()
        result = _post("/api/v1/agentos/compare", plan)
        elapsed = (time.monotonic() - t0) * 1000
        assert "comparison" in result
        loss = result.get("comparison", {}).get("epistemic_loss", -1)
        print(f"  /compare: {elapsed:.0f}ms (loss={loss:.4f})")


@pytest.mark.benchmark
class TestKnowledgeAPI:
    def test_export(self, ensure_server):
        t0 = time.monotonic()
        result = _post("/api/v1/agentos/knowledge/export", {})
        elapsed = (time.monotonic() - t0) * 1000
        assert "version" in result
        print(
            f"  /knowledge/export: {elapsed:.0f}ms ({len(result.get('transitions', []))} transitions)"
        )

    def test_import(self, ensure_server):
        export = _post("/api/v1/agentos/knowledge/export", {})
        t0 = time.monotonic()
        result = _post(
            "/api/v1/agentos/knowledge/import", {"bundle": export, "origin": "test"}
        )
        elapsed = (time.monotonic() - t0) * 1000
        assert "status" in result
        print(
            f"  /knowledge/import: {elapsed:.0f}ms ({result.get('accepted', 0)} accepted)"
        )
