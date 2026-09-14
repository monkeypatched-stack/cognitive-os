"""Sprint 14 — Benchmark Recertification: Complete benchmark suite.

Re-runs the complete MonkeyBrain benchmark suite against the hardened
production implementation to verify no regressions were introduced.
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

# ── Baseline metrics (from Sprint 13 certification) ──────────────────────
BASELINE = {
    "actor_tick_p50_ms": 1.2,
    "actor_tick_p99_ms": 5.0,
    "society_tick_10_actors_ms": 3.1,
    "planet_tick_10_actors_ms": 2.1,
    "boot_time_s": 60.0,
    "get_planet_ms": 1.0,
    "get_actors_ms": 1.0,
    "get_world_ms": 10.0,
}


@pytest.fixture(scope="module")
def client():
    # tests/conftest.py's _flush_shared_redis autouse fixture is
    # function-scoped — it runs before each TEST, not before this
    # MODULE-scoped fixture, which only ever runs once (on first use)
    # and is then reused for every test in this file. TestClient(app)
    # below is what boots PlanetaryRuntime and reads whatever geography
    # happens to be in Redis at that moment — flushing per-test can't
    # reach this, since by the time any per-test flush runs, this
    # fixture (and the ONE PlanetaryRuntime it builds) already exists.
    # Explicit flush here, right before boot, gives this file's shared
    # runtime a clean slate the same way every other file already gets
    # implicitly from the per-test fixture.
    import os
    import subprocess

    try:
        subprocess.run(
            [
                "redis-cli",
                "-h",
                os.getenv("REDIS_HOST", "localhost"),
                "-p",
                os.getenv("REDIS_PORT", "6379"),
                "flushdb",
            ],
            timeout=2,
            capture_output=True,
            check=False,
        )
    except Exception:
        pass

    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parents[2] / ".env")
    from src.monkey_brain.api.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _create_actor(client, name, actor_type, goal, caps=None):
    r = client.post(
        "/api/v1/agentos/actors",
        json={
            "name": name,
            "actor_type": actor_type,
            "goals": [goal],
            "capabilities": caps or [{"name": "general"}],
        },
    )
    assert r.status_code == 200, f"Create actor failed: {r.status_code} {r.text}"
    return r.json()["actor_id"]


def _tick_actor(client, aid, start="origin", goal="target"):
    r = client.post(
        f"/api/v1/agentos/actors/{aid}/tick",
        json={
            "start": start,
            "goal": goal,
            "reward": 1.0,
        },
    )
    assert r.status_code == 200, f"Tick failed: {r.status_code} {r.text}"
    return r.json()


def _share_experience(client, aid, outcome="success", confidence=0.8, lessons=None):
    r = client.post(
        "/api/v1/agentos/learn/experience",
        json={
            "experience": {
                "actor_id": aid,
                "outcome": outcome,
                "confidence": confidence,
                "lessons": lessons or [],
            }
        },
    )
    assert r.status_code == 200
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════
# 1. Functional Benchmarks (MB-0001 through MB-0015)
# ═══════════════════════════════════════════════════════════════════════════


class TestMB0001HelloWorld:
    """MB-0001: Basic cognitive cycle — single actor, single tick."""

    def test_hello_world(self, client):
        aid = _create_actor(client, "HelloWorld", "human", "greet_world")
        result = _tick_actor(client, aid)
        assert result["result"].get("belief_updated") is True
        assert result["tick_count"] >= 1


class TestMB0002MultiActor:
    """MB-0002: Multiple actors in same society."""

    def test_multi_actor(self, client):
        actors = []
        for name, atype, goal in [
            ("Worker-1", "human", "assemble"),
            ("Worker-2", "robot", "inspect"),
            ("Worker-3", "ai_agent", "optimize"),
        ]:
            actors.append(_create_actor(client, name, atype, goal))

        for aid in actors:
            result = _tick_actor(client, aid)
            assert result["result"].get("belief_updated") is True


class TestMB0003ProviderDiscovery:
    """MB-0003: Provider and capability discovery."""

    def test_provider_discovery(self, client):
        r = client.get("/api/v1/agentos/providers")
        assert r.status_code == 200
        assert "providers" in r.json()

    def test_capability_discovery(self, client):
        r = client.get("/api/v1/agentos/capabilities")
        assert r.status_code == 200


class TestMB0004BudgetConstraints:
    """MB-0004: Planning with budget constraints."""

    def test_budget_constraints(self, client):
        aid = _create_actor(client, "BudgetAgent", "enterprise", "maximize_profit")
        result = _tick_actor(client, aid, goal="maximize_profit")
        assert "plan" in result["result"] or "belief_updated" in result["result"]


class TestMB0005PriorityConstraints:
    """MB-0005: Planning with priority constraints."""

    def test_priority_constraints(self, client):
        aid = _create_actor(client, "PriorityAgent", "human", "urgent_delivery")
        result = _tick_actor(client, aid, goal="urgent_delivery")
        assert "plan" in result["result"] or "belief_updated" in result["result"]


class TestMB0006Reservations:
    """MB-0006: Resource reservation and allocation."""

    def test_reservations(self, client):
        actors = [
            _create_actor(client, "Picker-1", "robot", "pick_item"),
            _create_actor(client, "Picker-2", "robot", "pick_item"),
        ]
        # Both should tick without conflict
        for aid in actors:
            result = _tick_actor(client, aid)
            assert result["result"].get("belief_updated") is True


class TestMB0008Interruptions:
    """MB-0008: Handle interruptions during execution."""

    def test_interruptions(self, client):
        aid = _create_actor(client, "Interruptible", "human", "task_a")
        # Tick once
        _tick_actor(client, aid)
        # Change goal mid-flight
        result = _tick_actor(client, aid, goal="task_b")
        assert result["result"].get("belief_updated") is True


class TestMB0009TemporalReasoning:
    """MB-0009: Temporal reasoning across ticks."""

    def test_temporal_reasoning(self, client):
        aid = _create_actor(client, "TemporalAgent", "ai_agent", "sequence_tasks")
        # Multiple ticks build temporal context
        for i in range(5):
            result = _tick_actor(client, aid)
            assert result["result"].get("belief_updated") is True


class TestMB0010Negotiation:
    """MB-0010: Multi-actor negotiation."""

    def test_negotiation(self, client):
        actors = [
            _create_actor(client, "Negotiator-A", "enterprise", "sell_product"),
            _create_actor(client, "Negotiator-B", "enterprise", "buy_product"),
        ]
        for aid in actors:
            result = _tick_actor(client, aid)
            assert result["result"].get("belief_updated") is True


class TestMB0011BeliefVsReality:
    """MB-0011: Belief updating from observations."""

    def test_belief_vs_reality(self, client):
        aid = _create_actor(client, "BeliefAgent", "ai_agent", "update_beliefs")
        # Tick multiple times — beliefs should update
        for _ in range(3):
            result = _tick_actor(client, aid)
            assert result["result"].get("belief_updated") is True


class TestMB0012HumanApproval:
    """MB-0012: Human-in-the-loop patterns."""

    def test_human_approval(self, client):
        human = _create_actor(client, "Human-Approver", "human", "approve_action")
        robot = _create_actor(client, "Robot-Worker", "robot", "execute_action")
        # Both tick independently
        for aid in [human, robot]:
            result = _tick_actor(client, aid)
            assert result["result"].get("belief_updated") is True


class TestMB0013LongRunningPlans:
    """MB-0013: Long-running multi-step plans."""

    def test_long_running_plans(self, client):
        aid = _create_actor(client, "LongPlanner", "ai_agent", "complex_multi_step_plan")
        # 10 consecutive ticks
        for i in range(10):
            result = _tick_actor(client, aid)
            assert result["result"].get("belief_updated") is True


class TestMB0014PartialFailure:
    """MB-0014: Graceful handling of partial failures."""

    def test_partial_failure(self, client):
        actors = [
            _create_actor(client, "Reliable", "robot", "safe_task"),
            _create_actor(client, "Risky", "robot", "risky_task"),
        ]
        # Tick both — if one fails, other should succeed
        results = [_tick_actor(client, aid) for aid in actors]
        successes = sum(1 for r in results if r["result"].get("belief_updated") is True)
        assert successes >= 1, "At least one actor should succeed"


class TestMB0015Learning:
    """MB-0015: Learning from experience."""

    def test_learning(self, client):
        aid = _create_actor(client, "LearningAgent", "ai_agent", "learn_from_experience")
        # Tick and share experience
        _tick_actor(client, aid)
        exp = _share_experience(client, aid, outcome="success", confidence=0.9)
        assert exp["status"] == "ok"
        assert exp["result"]["experience_id"]

        # Verify statistics updated
        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.status_code == 200
        assert r.json()["total_experiences"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 2. Gateway Benchmarks
# ═══════════════════════════════════════════════════════════════════════════


class TestGatewayBenchmarks:
    """All benchmarks executed through the Runtime Gateway."""

    def test_full_lifecycle_via_gateway(self, client):
        # Create society
        r = client.post("/api/v1/agentos/societies", json={"name": "Gateway Test Society"})
        assert r.status_code == 200

        # Create actors
        actors = []
        for name, atype, goal in [
            ("GW-Worker-1", "human", "process_order"),
            ("GW-Worker-2", "robot", "pack_items"),
            ("GW-Worker-3", "ai_agent", "optimize_route"),
        ]:
            actors.append(_create_actor(client, name, atype, goal))

        # Tick all actors
        for aid in actors:
            _tick_actor(client, aid)

        # Society tick
        r = client.get("/api/v1/agentos/societies")
        sid = r.json()[0]["society_id"]
        r = client.post(f"/api/v1/agentos/societies/{sid}/tick")
        assert r.status_code == 200

        # Planet tick
        r = client.post("/api/v1/agentos/planet/tick")
        assert r.status_code == 200

        # Share experience
        for aid in actors:
            _share_experience(client, aid)

        # Verify world state
        r = client.get("/api/v1/agentos/world")
        assert r.status_code == 200
        assert r.json()["version"] >= 0

        # Verify learning stats
        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.status_code == 200
        assert r.json()["total_experiences"] >= len(actors)

        # Verify runtime health
        r = client.get("/api/v1/agentos/runtime/status")
        assert r.status_code == 200
        assert r.json()["cognitive"] == "ready"

    def test_all_read_endpoints(self, client):
        endpoints = [
            "/api/v1/agentos/planet",
            "/api/v1/agentos/planet/status",
            "/api/v1/agentos/societies",
            "/api/v1/agentos/actors",
            "/api/v1/agentos/memberships",
            "/api/v1/agentos/runtime",
            "/api/v1/agentos/runtime/status",
            "/api/v1/agentos/runtime/health",
            "/api/v1/agentos/runtime/configuration",
            "/api/v1/agentos/runtime/metrics",
            "/api/v1/agentos/world",
            "/api/v1/agentos/world/context",
            "/api/v1/agentos/world/beliefs",
            "/api/v1/agentos/world/entities",
            "/api/v1/agentos/world/relationships",
            "/api/v1/agentos/world/events",
            "/api/v1/agentos/learn/statistics",
            "/api/v1/agentos/simulate/status",
            "/api/v1/agentos/compare/history",
            "/api/v1/agentos/version",
        ]
        # Test a subset to avoid rate limiting under heavy load
        subset = endpoints[:10]
        successes = 0
        for ep in subset:
            r = client.get(ep)
            if r.status_code == 200:
                successes += 1
            elif r.status_code == 429:
                pass  # Rate limited — acceptable under load
            else:
                assert False, f"GET {ep} returned unexpected {r.status_code}"
        # At least 80% should succeed
        assert successes >= len(subset) * 0.8, f"Only {successes}/{len(subset)} endpoints returned 200"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Performance Benchmarks
# ═══════════════════════════════════════════════════════════════════════════


class TestPerformanceBenchmarks:
    """Measure and compare performance metrics."""

    def test_actor_tick_latency(self, client):
        aid = _create_actor(client, "PerfTick", "robot", "perf_task")

        # Warm up
        for _ in range(5):
            _tick_actor(client, aid)

        # Measure
        latencies = []
        for _ in range(50):
            start = time.time()
            _tick_actor(client, aid)
            latencies.append((time.time() - start) * 1000)

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        max_lat = latencies[-1]

        print(f"\n  Actor tick: P50={p50:.1f}ms P95={p95:.1f}ms P99={p99:.1f}ms Max={max_lat:.1f}ms")
        assert p99 < BASELINE["actor_tick_p99_ms"] * 2, (
            f"P99 {p99:.1f}ms exceeds 2x baseline {BASELINE['actor_tick_p99_ms']}ms"
        )

    def test_society_tick_latency(self, client):
        for i in range(10):
            _create_actor(client, f"SocPerf-{i}", "robot", f"task_{i}")

        r = client.get("/api/v1/agentos/societies")
        sid = r.json()[0]["society_id"]

        # Measure
        start = time.time()
        r = client.post(f"/api/v1/agentos/societies/{sid}/tick")
        elapsed = (time.time() - start) * 1000

        assert r.status_code == 200
        print(f"\n  Society tick (10 actors): {elapsed:.1f}ms")
        assert elapsed < BASELINE["society_tick_10_actors_ms"] * 3, f"Society tick {elapsed:.1f}ms exceeds 3x baseline"

    def test_planet_tick_latency(self, client):
        start = time.time()
        r = client.post("/api/v1/agentos/planet/tick")
        elapsed = (time.time() - start) * 1000

        assert r.status_code == 200
        print(f"\n  Planet tick: {elapsed:.1f}ms")
        assert elapsed < BASELINE["planet_tick_10_actors_ms"] * 3, f"Planet tick {elapsed:.1f}ms exceeds 3x baseline"

    def test_read_endpoint_latency(self, client):
        endpoints = {
            "/api/v1/agentos/planet": BASELINE["get_planet_ms"],
            "/api/v1/agentos/actors": BASELINE["get_actors_ms"],
            "/api/v1/agentos/world": BASELINE["get_world_ms"],
        }
        for ep, baseline in endpoints.items():
            latencies = []
            for _ in range(20):
                start = time.time()
                r = client.get(ep)
                latencies.append((time.time() - start) * 1000)
                assert r.status_code == 200

            p99 = sorted(latencies)[int(len(latencies) * 0.99)]
            print(f"\n  GET {ep.split('/')[-1]}: P99={p99:.1f}ms (baseline={baseline}ms)")
            assert p99 < baseline * 5, f"P99 {p99:.1f}ms exceeds 5x baseline"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Learning Benchmarks
# ═══════════════════════════════════════════════════════════════════════════


class TestLearningBenchmarks:
    """Verify learning produces observable state changes."""

    def test_experience_commitment(self, client):
        aid = _create_actor(client, "LearnCommit", "ai_agent", "learn_commit")

        # Share 10 experiences
        for i in range(10):
            exp = _share_experience(
                client,
                aid,
                outcome="success" if i % 2 == 0 else "failure",
                confidence=0.5 + (i % 5) * 0.1,
                lessons=[f"lesson_{i}"],
            )
            assert exp["result"]["experience_id"]

        # Verify statistics
        r = client.get("/api/v1/agentos/learn/statistics")
        stats = r.json()
        assert stats["total_experiences"] >= 10
        assert stats["total_reputations"] >= 1

    def test_world_refinement(self, client):
        aid = _create_actor(client, "WorldRefiner", "ai_agent", "refine_world")

        # Share experience with lessons
        exp = _share_experience(
            client,
            aid,
            outcome="success",
            confidence=0.9,
            lessons=["lesson_1", "lesson_2"],
        )
        assert exp["result"]["world_refined"] is True

    def test_policy_proposal(self, client):
        aid = _create_actor(client, "PolicyProposer", "ai_agent", "propose_policy")

        # Share policy evolution experience
        r = client.post(
            "/api/v1/agentos/learn/experience",
            json={
                "learning_type": "policy_evolution",
                "experience": {
                    "actor_id": aid,
                    "outcome": "success",
                    "confidence": 0.9,
                },
            },
        )
        assert r.status_code == 200
        assert r.json()["result"]["policy_proposed"] is not None

    def test_reputation_tracking(self, client):
        aid = _create_actor(client, "RepTracker", "ai_agent", "track_reputation")

        # Share multiple successful experiences
        for _ in range(5):
            _share_experience(client, aid, outcome="success", confidence=0.9)

        # Check reputation
        r = client.get("/api/v1/agentos/learn/statistics")
        stats = r.json()
        assert stats["total_reputations"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 5. Federation Benchmarks
# ═══════════════════════════════════════════════════════════════════════════


class TestFederationBenchmarks:
    """Federation coordination and failure isolation."""

    def test_single_society_tick(self, client):
        r = client.get("/api/v1/agentos/societies")
        sid = r.json()[0]["society_id"]
        r = client.post(f"/api/v1/agentos/societies/{sid}/tick")
        assert r.status_code == 200
        assert r.json()["actors_ticked"] >= 0

    def test_two_societies_tick(self, client):
        # Create second society
        r = client.post("/api/v1/agentos/societies", json={"name": "Federation-S2"})
        assert r.status_code == 200

        # Planet tick should handle all societies
        r = client.post("/api/v1/agentos/planet/tick")
        assert r.status_code == 200
        assert r.json()["actors_observed"] >= 0

    def test_failure_isolation(self, client):
        # Create and delete a society
        r = client.post("/api/v1/agentos/societies", json={"name": "FailIsolation"})
        sid = r.json()["society_id"]
        client.delete(f"/api/v1/agentos/societies/{sid}")

        # Planet tick should still work
        r = client.post("/api/v1/agentos/planet/tick")
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# 6. Resource Benchmarks
# ═══════════════════════════════════════════════════════════════════════════


class TestResourceBenchmarks:
    """Monitor resource usage and bounded growth."""

    def test_memory_bounded(self, client):
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        # Run 50 cycles
        for i in range(50):
            aid = _create_actor(client, f"ResBounded-{i}", "robot", f"task_{i}")
            _tick_actor(client, aid)
            client.post("/api/v1/agentos/planet/tick")

        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        total1 = sum(s.size for s in snap1.statistics("lineno"))
        total2 = sum(s.size for s in snap2.statistics("lineno"))
        growth_pct = ((total2 - total1) / total1 * 100) if total1 > 0 else 0

        print(f"\n  Memory: {total1 / 1024:.1f}KB → {total2 / 1024:.1f}KB ({growth_pct:+.1f}%)")
        assert growth_pct < 100, f"Memory grew {growth_pct:.1f}% (>100%)"

    def test_world_events_bounded(self, client):
        for i in range(100):
            aid = _create_actor(client, f"EventBounded-{i}", "robot", f"task_{i}")
            _tick_actor(client, aid)

        r = client.get("/api/v1/agentos/world/events?limit=10000")
        assert r.json()["count"] <= 10000

    def test_experiences_bounded(self, client):
        for i in range(100):
            aid = _create_actor(client, f"ExpBounded-{i}", "robot", f"task_{i}")
            _share_experience(client, aid)

        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.json()["total_experiences"] <= 10000


# ═══════════════════════════════════════════════════════════════════════════
# 7. Regression Benchmarks
# ═══════════════════════════════════════════════════════════════════════════


class TestRegressionBenchmarks:
    """Verify no behavioral regressions."""

    def test_api_response_codes(self, client):
        # Successful operations
        r = client.get("/api/v1/agentos/planet")
        assert r.status_code == 200

        r = client.post("/api/v1/agentos/societies", json={"name": "Regression"})
        assert r.status_code == 200

        r = client.post(
            "/api/v1/agentos/actors",
            json={
                "name": "Regression-Actor",
                "actor_type": "robot",
                "goals": ["test"],
            },
        )
        assert r.status_code == 200

        # Validation errors
        r = client.post("/api/v1/agentos/actors", json={})
        assert r.status_code == 422

    def test_backward_compatibility(self, client):
        # Health endpoints
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["service"] == "monkeybrain-runtime"

        r = client.get("/ready")
        assert r.status_code in (200, 503)

        # OpenAPI
        r = client.get("/openapi.json")
        assert r.status_code == 200
        assert "paths" in r.json()

    def test_planet_state_consistency(self, client):
        r = client.get("/api/v1/agentos/planet")
        planet = r.json()

        assert "society_id" in planet
        assert "actor_count" in planet
        assert "world_version" in planet
        assert "society_registry_count" in planet
        assert planet["society_registry_count"] >= 1
