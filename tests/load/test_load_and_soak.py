"""Load and soak tests — sustained throughput and resource leak detection."""

from __future__ import annotations

import asyncio
import gc
import os
import sys
import time

import pytest

_root = os.path.join(os.path.dirname(__file__), "..", "..")
for p in ("src", "packages/cerebellum", "domains/manufacturing/knowledge"):
    _full = os.path.join(_root, p)
    if _full not in sys.path:
        sys.path.insert(0, _full)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestLoadHealthEndpoint:
    """Sustained load on the health endpoint."""

    def test_health_200_requests_sequential(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)
        latencies = []

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for _ in range(200):
                    t0 = time.monotonic()
                    resp = await client.get("/health")
                    latencies.append(time.monotonic() - t0)
                    assert resp.status_code in (200, 401, 403)

        _run(run())
        avg = sum(latencies) / len(latencies)
        p99 = sorted(latencies)[int(len(latencies) * 0.99)]
        assert avg < 1.0, f"Average latency {avg:.3f}s exceeds 1s"
        assert p99 < 2.0, f"P99 latency {p99:.3f}s exceeds 2s"

    def test_health_100_concurrent(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:

                async def req():
                    t0 = time.monotonic()
                    resp = await client.get("/health")
                    return time.monotonic() - t0, resp.status_code

                tasks = [req() for _ in range(100)]
                return await asyncio.gather(*tasks, return_exceptions=True)

        results = _run(run())
        valid = [(lat, code) for lat, code in results if isinstance(code, int)]
        assert len(valid) == 100
        avg = sum(lat for lat, _ in valid) / len(valid)
        assert avg < 2.0


class TestLoadQueryEndpoint:
    """Sustained load on the query endpoint."""

    def test_query_sustained_throughput(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)
        success_count = 0

        async def run():
            nonlocal success_count
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for i in range(50):
                    resp = await client.post("/api/v1/agentos/query", json={"question": f"load test {i}"})
                    if resp.status_code in (200, 404, 422, 401, 403):
                        success_count += 1

        _run(run())
        assert success_count == 50, f"Only {success_count}/50 requests succeeded"


class TestMemoryLeak:
    """Detect memory leaks from repeated operations."""

    def test_keystore_no_memory_leak(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        gc.collect()
        import tracemalloc

        tracemalloc.start()
        snapshot1 = tracemalloc.take_snapshot()
        ks = SecureKeystore(
            master_key="leak-test-key-32-bytes-long!!!",
            db_path=str(tmp_path / "leak.json"),
        )
        for i in range(1000):
            ks.add_key(f"user-{i % 10}", "svc", f"k-{i}", f"v-{i}")
            ks.list_keys(f"user-{i % 10}")
        gc.collect()
        snapshot2 = tracemalloc.take_snapshot()
        stats = snapshot2.compare_to(snapshot1, "lineno")
        top = sum(s.size_diff for s in stats[:10])
        tracemalloc.stop()
        assert top < 50 * 1024 * 1024, f"Memory grew by {top / 1024 / 1024:.1f}MB"

    def test_url_validator_no_memory_leak(self):
        from cerebellum.capabilities.api.webhook import _is_safe_url

        gc.collect()
        import tracemalloc

        tracemalloc.start()
        snapshot1 = tracemalloc.take_snapshot()
        for _ in range(10000):
            _is_safe_url("http://example.com/hook")
            _is_safe_url("http://169.254.169.254/")
        gc.collect()
        snapshot2 = tracemalloc.take_snapshot()
        stats = snapshot2.compare_to(snapshot1, "lineno")
        top = sum(s.size_diff for s in stats[:10])
        tracemalloc.stop()
        assert top < 10 * 1024 * 1024, f"Memory grew by {top / 1024:.1f}KB"


class TestSoak:
    """Soak tests — run operations for extended period to detect gradual degradation."""

    def test_health_endpoint_soak(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)
        latencies = []

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for batch in range(10):
                    for _ in range(20):
                        t0 = time.monotonic()
                        resp = await client.get("/health")
                        latencies.append(time.monotonic() - t0)
                        assert resp.status_code in (200, 401, 403)

        _run(run())
        first_20 = latencies[:20]
        last_20 = latencies[-20:]
        avg_first = sum(first_20) / len(first_20)
        avg_last = sum(last_20) / len(last_20)
        assert avg_last < avg_first * 3, f"Performance degraded: first={avg_first:.3f}s, last={avg_last:.3f}s"

    def test_keystore_soak(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="soak-test-key-32-bytes-long!!!",
            db_path=str(tmp_path / "soak.json"),
        )
        errors = []
        for i in range(500):
            try:
                ks.add_key(f"user-{i % 5}", "svc", f"k-{i}", f"v-{i}")
                if i % 10 == 0:
                    ks.list_keys(f"user-{i % 5}")
                if i % 25 == 0:
                    keys = ks.list_keys(f"user-{i % 5}")
                    if keys:
                        ks.get_key(keys[0]["key_id"], f"user-{i % 5}")
            except Exception as e:
                errors.append(e)
        assert not errors, f"Errors during soak: {errors[:5]}"
