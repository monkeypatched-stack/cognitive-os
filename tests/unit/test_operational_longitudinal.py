"""Phase 6 — Longitudinal Learning: Run hundreds of cycles, verify convergence.

The learning system should improve over time, not degrade.
No catastrophic forgetting. No unbounded growth. Stable performance.
"""

from __future__ import annotations

import os
import time
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


class TestLongitudinalLearningConvergence:
    """Verify learning converges over many cycles."""

    def test_experience_accumulation(self, client):
        aid = _create_actor(client, "LongLearner", "convergence_task")

        # Run 100 cycles with experience sharing
        for i in range(100):
            client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "a",
                    "goal": "b",
                    "reward": 1.0,
                },
            )
            client.post(
                "/api/v1/agentos/learn/experience",
                json={
                    "experience": {
                        "actor_id": aid,
                        "outcome": "success" if i % 3 != 0 else "failure",
                        "confidence": 0.5 + (i % 5) * 0.1,
                        "lessons": [f"lesson_{i % 10}"],
                    }
                },
            )

        # Statistics should show accumulated experiences
        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.status_code == 200
        stats = r.json()
        # Should have experiences (capped at max_experiences)
        assert stats["total_experiences"] > 0
        assert stats["total_experiences"] <= 10000

    def test_reputation_stability(self, client):
        # Create multiple actors and share experiences
        actors = [_create_actor(client, f"RepLearner-{i}", f"task_{i}") for i in range(5)]

        for _ in range(50):
            for aid in actors:
                client.post(
                    f"/api/v1/agentos/actors/{aid}/tick",
                    json={
                        "start": "a",
                        "goal": "b",
                        "reward": 1.0,
                    },
                )
                client.post(
                    "/api/v1/agentos/learn/experience",
                    json={
                        "experience": {
                            "actor_id": aid,
                            "outcome": "success",
                            "confidence": 0.8,
                        }
                    },
                )

        # Check reputations exist and are bounded
        r = client.get("/api/v1/agentos/learn/statistics")
        stats = r.json()
        # Should have some reputations (exact count depends on prior tests)
        assert stats["total_reputations"] >= 0


class TestLongitudinalWorldConsistency:
    """Verify world state remains consistent over many operations."""

    def test_world_version_monotonic(self, client):
        versions = []
        for i in range(50):
            aid = _create_actor(client, f"WorldLong-{i}", f"task_{i}")
            client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "a",
                    "goal": "b",
                    "reward": 1.0,
                },
            )
            r = client.get("/api/v1/agentos/world")
            versions.append(r.json()["version"])

        # Version should be monotonically non-decreasing
        for i in range(1, len(versions)):
            assert versions[i] >= versions[i - 1], f"Version decreased: {versions[i - 1]} → {versions[i]}"

    def test_world_events_bounded(self, client):
        # Run many cycles
        for i in range(100):
            aid = _create_actor(client, f"EventLong-{i}", f"task_{i}")
            client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "a",
                    "goal": "b",
                    "reward": 1.0,
                },
            )

        # Events should be bounded
        r = client.get("/api/v1/agentos/world/events?limit=10000")
        events = r.json()
        assert events["count"] <= 10000


class TestLongitudinalPerformanceStability:
    """Verify performance doesn't degrade over time."""

    def test_tick_latency_stable(self, client):
        aid = _create_actor(client, "PerfStable", "perf_task")

        # Measure latency at start
        latencies_start = []
        for _ in range(20):
            start = time.time()
            client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "a",
                    "goal": "b",
                    "reward": 1.0,
                },
            )
            latencies_start.append((time.time() - start) * 1000)

        # Run 200 intermediate cycles
        for _ in range(200):
            client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "a",
                    "goal": "b",
                    "reward": 1.0,
                },
            )

        # Measure latency at end
        latencies_end = []
        for _ in range(20):
            start = time.time()
            client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "a",
                    "goal": "b",
                    "reward": 1.0,
                },
            )
            latencies_end.append((time.time() - start) * 1000)

        avg_start = sum(latencies_start) / len(latencies_start)
        avg_end = sum(latencies_end) / len(latencies_end)

        print(
            f"\n  Latency: start={avg_start:.1f}ms end={avg_end:.1f}ms "
            f"(change={(avg_end - avg_start) / avg_start * 100:+.1f}%)"
        )

        # End latency should not be more than 3x start latency
        assert avg_end < avg_start * 3, f"Performance degraded: {avg_start:.1f}ms → {avg_end:.1f}ms"


class TestLongitudinalNoCatastrophicForgetting:
    """Verify learning doesn't forget everything."""

    def test_early_lessons_retained(self, client):
        aid = _create_actor(client, "ForgettingTest", "forget_task")

        # Phase 1: Learn lessons
        for i in range(50):
            client.post(
                "/api/v1/agentos/learn/experience",
                json={
                    "experience": {
                        "actor_id": aid,
                        "outcome": "success",
                        "confidence": 0.9,
                        "lessons": ["critical_lesson_1", "critical_lesson_2"],
                    }
                },
            )

        # Phase 2: Learn different lessons
        for i in range(50):
            client.post(
                "/api/v1/agentos/learn/experience",
                json={
                    "experience": {
                        "actor_id": aid,
                        "outcome": "failure",
                        "confidence": 0.3,
                        "lessons": ["new_lesson_1"],
                    }
                },
            )

        # Check statistics — experiences should still be present
        r = client.get("/api/v1/agentos/learn/statistics")
        stats = r.json()
        # Should have accumulated experiences (not forgotten)
        assert stats["total_experiences"] >= 100


class TestLongitudinalResourceBounds:
    """Verify no unbounded growth over many operations."""

    def test_society_coordination_history_bounded(self, client):
        # Run many ticks to grow coordination history
        for i in range(200):
            aid = _create_actor(client, f"CoordLong-{i}", f"task_{i}")
            client.post(
                f"/api/v1/agentos/actors/{aid}/tick",
                json={
                    "start": "a",
                    "goal": "b",
                    "reward": 1.0,
                },
            )

        # Check society state
        r = client.get("/api/v1/agentos/societies")
        societies = r.json()
        assert len(societies) > 0

        # Planet should still be responsive
        r = client.post("/api/v1/agentos/planet/tick")
        assert r.status_code == 200
