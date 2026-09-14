"""Phase 1 — Soak Test: Run continuously, measure resource stability.

Verifies the system doesn't leak memory, file descriptors, or connections
during sustained operation. A soak test catches slow degradation that
unit tests miss.
"""

from __future__ import annotations

import os
import time
import tracemalloc
import threading
import pytest
from fastapi.testclient import TestClient

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"
os.environ["RATE_LIMIT_RPS"] = "100000"
os.environ["RATE_LIMIT_BURST"] = "200000"


@pytest.fixture(scope="module")
def client():
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parents[2] / ".env")
    from src.monkey_brain.api.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _create_actor(client, name, goal):
    r = client.post(
        "/api/v1/agentos/actors",
        json={
            "name": name,
            "actor_type": "robot",
            "goals": [goal],
        },
    )
    assert r.status_code == 200
    return r.json()["actor_id"]


def _tick_actor(client, aid):
    return client.post(
        f"/api/v1/agentos/actors/{aid}/tick",
        json={
            "start": "a",
            "goal": "b",
            "reward": 1.0,
        },
    )


class TestSoakMemory:
    """Verify memory stays stable under sustained load."""

    def test_memory_stability_100_cycles(self, client):
        tracemalloc.start()
        snapshot1 = tracemalloc.take_snapshot()

        # Create actors
        actors = [_create_actor(client, f"Soak-{i}", f"task_{i}") for i in range(5)]

        # Run 100 cycles of tick + planet tick
        for _ in range(100):
            for aid in actors:
                _tick_actor(client, aid)
            client.post("/api/v1/agentos/planet/tick")

        snapshot2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        # Compare top memory allocations
        stats1 = snapshot1.statistics("lineno")
        stats2 = snapshot2.statistics("lineno")

        total1 = sum(s.size for s in stats1)
        total2 = sum(s.size for s in stats2)

        growth_pct = ((total2 - total1) / total1 * 100) if total1 > 0 else 0

        print(f"\n  Memory: {total1 / 1024:.1f}KB → {total2 / 1024:.1f}KB ({growth_pct:+.1f}%)")
        # Allow up to 50% growth over 100 cycles (conservative for test env)
        assert growth_pct < 50, f"Memory grew {growth_pct:.1f}% over 100 cycles"


class TestSoakWorldEvents:
    """Verify world event list stays bounded."""

    def test_world_events_bounded(self, client):
        actors = [_create_actor(client, f"EventSoak-{i}", f"task_{i}") for i in range(3)]

        # Run 50 cycles
        for _ in range(50):
            for aid in actors:
                _tick_actor(client, aid)
            client.post("/api/v1/agentos/planet/tick")

        # Check world events are bounded (max_events=10000)
        r = client.get("/api/v1/agentos/world/events?limit=10000")
        assert r.status_code == 200
        events = r.json()
        # Should not exceed max_events
        assert events["count"] <= 10000, f"World events: {events['count']} (expected ≤10000)"


class TestSoakLearning:
    """Verify learning statistics stay consistent."""

    def test_learning_experience_bounds(self, client):
        actors = [_create_actor(client, f"LearnSoak-{i}", f"task_{i}") for i in range(3)]

        for _ in range(30):
            for aid in actors:
                _tick_actor(client, aid)
            client.post("/api/v1/agentos/planet/tick")

        # Check statistics
        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.status_code == 200
        stats = r.json()
        # Experiences should be bounded by max_experiences=10000
        assert stats["total_experiences"] <= 10000


class TestSoakConcurrentAccess:
    """Verify no deadlocks under concurrent requests."""

    def test_concurrent_ticks_no_deadlock(self, client):
        actors = [_create_actor(client, f"Concurrent-{i}", f"task_{i}") for i in range(5)]

        errors = []

        def tick_actor(aid):
            try:
                _tick_actor(client, aid)
            except Exception as e:
                errors.append(str(e))

        # Run 20 concurrent tick batches
        for _ in range(20):
            threads = [threading.Thread(target=tick_actor, args=(aid,)) for aid in actors]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            # Check no thread is still alive (deadlock)
            for t in threads:
                assert not t.is_alive(), "Thread deadlocked"

        assert len(errors) == 0, f"Errors during concurrent access: {errors}"


class TestSoakAPIStability:
    """Verify API responses remain stable under load."""

    def test_read_endpoints_stable(self, client):
        # Warm up
        for _ in range(10):
            client.get("/api/v1/agentos/planet")
            client.get("/api/v1/agentos/actors")
            client.get("/api/v1/agentos/runtime/status")

        # Measure latency over 100 requests
        latencies = []
        for _ in range(100):
            start = time.time()
            r = client.get("/api/v1/agentos/planet")
            latencies.append((time.time() - start) * 1000)
            assert r.status_code == 200

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]

        print(f"\n  GET /planet latency: P50={p50:.1f}ms P95={p95:.1f}ms P99={p99:.1f}ms")
        # P99 should be under 100ms for a simple read
        assert p99 < 100, f"P99 latency {p99:.1f}ms exceeds 100ms threshold"
