"""Phase 3 — Load Testing: Measure performance at scale.

Benchmark latency, throughput, and memory at various actor counts.
"""

from __future__ import annotations

import os
import time
import tracemalloc
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


class TestLoadActorTick:
    """Benchmark actor tick latency at various scales."""

    def _measure_tick_latency(self, client, n_actors, n_ticks=5):
        actors = [_create_actor(client, f"Load-{n_actors}-{i}", f"task_{i}") for i in range(n_actors)]

        # Warm up
        for aid in actors[: min(3, n_actors)]:
            client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "a",
                    "goal": "b",
                    "reward": 1.0,
                },
            )

        # Measure
        latencies = []
        for _ in range(n_ticks):
            for aid in actors:
                start = time.time()
                r = client.post(
                    f"/api/v1/agentos/actors/{aid}/tick",
                    json={
                        "start": "a",
                        "goal": "b",
                        "reward": 1.0,
                    },
                )
                latencies.append((time.time() - start) * 1000)
                assert r.status_code == 200

        latencies.sort()
        return {
            "n_actors": n_actors,
            "n_ticks": len(latencies),
            "p50": latencies[len(latencies) // 2],
            "p95": latencies[int(len(latencies) * 0.95)],
            "p99": latencies[int(len(latencies) * 0.99)],
            "total_ms": sum(latencies),
            "throughput": len(latencies) / (sum(latencies) / 1000),
        }

    def test_10_actors(self, client):
        result = self._measure_tick_latency(client, 10)
        print(
            f"\n  10 actors: P50={result['p50']:.1f}ms P95={result['p95']:.1f}ms "
            f"P99={result['p99']:.1f}ms throughput={result['throughput']:.0f}/s"
        )
        assert result["p99"] < 50, f"P99 {result['p99']:.1f}ms exceeds 50ms"

    def test_50_actors(self, client):
        result = self._measure_tick_latency(client, 50)
        print(
            f"\n  50 actors: P50={result['p50']:.1f}ms P95={result['p95']:.1f}ms "
            f"P99={result['p99']:.1f}ms throughput={result['throughput']:.0f}/s"
        )
        assert result["p99"] < 100, f"P99 {result['p99']:.1f}ms exceeds 100ms"

    def test_100_actors(self, client):
        result = self._measure_tick_latency(client, 100)
        print(
            f"\n  100 actors: P50={result['p50']:.1f}ms P95={result['p95']:.1f}ms "
            f"P99={result['p99']:.1f}ms throughput={result['throughput']:.0f}/s"
        )
        assert result["p99"] < 200, f"P99 {result['p99']:.1f}ms exceeds 200ms"


class TestLoadSocietyTick:
    """Benchmark society tick with many actors."""

    def test_society_tick_50_actors(self, client):
        for i in range(50):
            _create_actor(client, f"SocLoad-{i}", f"task_{i}")

        r = client.get("/api/v1/agentos/societies")
        sid = r.json()[0]["society_id"]

        # Measure society tick
        start = time.time()
        r = client.post(f"/api/v1/agentos/societies/{sid}/tick")
        elapsed = (time.time() - start) * 1000

        assert r.status_code == 200
        result = r.json()
        print(f"\n  Society tick (50 actors): {elapsed:.1f}ms, actors_ticked={result['actors_ticked']}")
        assert elapsed < 5000, f"Society tick took {elapsed:.0f}ms (>5s)"


class TestLoadPlanetTick:
    """Benchmark planet tick at scale."""

    def test_planet_tick_100_actors(self, client):
        for i in range(100):
            _create_actor(client, f"PlanetLoad-{i}", f"task_{i}")

        start = time.time()
        r = client.post("/api/v1/agentos/planet/tick")
        elapsed = (time.time() - start) * 1000

        assert r.status_code == 200
        result = r.json()
        print(f"\n  Planet tick (100 actors): {elapsed:.1f}ms, actors={result['actors_observed']}")
        assert elapsed < 10000, f"Planet tick took {elapsed:.0f}ms (>10s)"


class TestLoadMemoryGrowth:
    """Measure memory growth with increasing actor count."""

    def test_memory_at_100_actors(self, client):
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        for i in range(100):
            aid = _create_actor(client, f"MemLoad-{i}", f"task_{i}")
            client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "a",
                    "goal": "b",
                    "reward": 1.0,
                },
            )

        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        total1 = sum(s.size for s in snap1.statistics("lineno"))
        total2 = sum(s.size for s in snap2.statistics("lineno"))
        growth_kb = (total2 - total1) / 1024

        print(f"\n  Memory growth (100 actors): {growth_kb:.1f}KB")
        # Should be under 10MB for 100 actors
        assert growth_kb < 10240, f"Memory grew {growth_kb:.0f}KB (>10MB) for 100 actors"


class TestLoadThroughput:
    """Measure maximum throughput."""

    def test_ticks_per_second(self, client):
        for i in range(20):
            _create_actor(client, f"TPS-{i}", f"task_{i}")

        # Warm up
        r = client.get("/api/v1/agentos/societies")
        sid = r.json()[0]["society_id"]

        # Measure planet ticks per second
        start = time.time()
        count = 0
        while time.time() - start < 5:  # Run for 5 seconds
            client.post("/api/v1/agentos/planet/tick")
            count += 1

        elapsed = time.time() - start
        tps = count / elapsed

        print(f"\n  Planet ticks/second: {tps:.1f} (over {elapsed:.1f}s)")
        assert tps >= 0.5, f"Throughput {tps:.1f} ticks/sec is below minimum"
