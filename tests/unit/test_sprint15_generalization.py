"""Sprint 15 — Scenario Generalization: Expanded domain-independent testing.

Expands the generalization test suite with deeper coverage across:
- More domain scenarios
- Entity type combinations
- Constraint variations
- Scale boundaries
- Failure recovery patterns
- Learning transfer
- Knowledge generalization
- Workflow generalization
"""

from __future__ import annotations

import os
import time
import pytest
from fastapi.testclient import TestClient

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"
os.environ["RATE_LIMIT_RPS"] = "100000"
os.environ["RATE_LIMIT_BURST"] = "200000"
# Hermetic runtime: do not boot against a developer's live Redis / persisted
# actors (see tests/scale/test_actor_scale.py and tests/conftest.py).
os.environ["TIMELINE_STORE_BACKEND"] = "memory"
os.environ["REDIS_PORT"] = "1"

_RUN_SLOW_SCALE_TESTS = os.getenv("RUN_SCALE_TESTS") == "1"
_skip_slow_scale = pytest.mark.skipif(
    not _RUN_SLOW_SCALE_TESTS,
    reason="slow scale tier; set RUN_SCALE_TESTS=1 to run",
)


@pytest.fixture(scope="module")
def client():
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
    assert r.status_code == 200
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
    assert r.status_code == 200
    return r.json()


def _share_experience(client, aid, outcome="success", confidence=0.8, lessons=None, learning_type=None):
    body = {
        "experience": {
            "actor_id": aid,
            "outcome": outcome,
            "confidence": confidence,
            "lessons": lessons or [],
        }
    }
    if learning_type:
        body["learning_type"] = learning_type
    r = client.post("/api/v1/agentos/learn/experience", json=body)
    assert r.status_code == 200
    return r.json()


def _tick_planet(client):
    r = client.post("/api/v1/agentos/planet/tick")
    assert r.status_code == 200
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════
# 1. Expanded Domain Scenarios
# ═══════════════════════════════════════════════════════════════════════════


class TestExpandedDomains:
    """Deeper domain coverage with more realistic scenarios."""

    DOMAINS = [
        # (name, actors: [(name, type, goal, caps)])
        (
            "Telecom Network Optimization",
            [
                (
                    "NetworkEng",
                    "human",
                    "optimize_bandwidth",
                    [{"name": "network_engineering"}],
                ),
                (
                    "TrafficAI",
                    "ai_agent",
                    "balance_load",
                    [{"name": "traffic_analysis"}],
                ),
            ],
        ),
        (
            "Legal Contract Review",
            [
                ("Lawyer-1", "human", "review_contract", [{"name": "legal_review"}]),
                ("ContractAI", "ai_agent", "extract_clauses", [{"name": "nlp"}]),
            ],
        ),
        (
            "Agricultural Supply Chain",
            [
                ("FarmMgr", "human", "manage_harvest", [{"name": "agriculture"}]),
                ("DroneBot", "robot", "survey_crops", [{"name": "aerial_imaging"}]),
                (
                    "LogisticsCo",
                    "enterprise",
                    "coordinate_distribution",
                    [{"name": "logistics"}],
                ),
            ],
        ),
        (
            "Energy Grid Management",
            [
                ("GridOp", "human", "balance_load", [{"name": "grid_operations"}]),
                ("ForecastAI", "ai_agent", "predict_demand", [{"name": "forecasting"}]),
            ],
        ),
        (
            "Educational Content Delivery",
            [
                ("Teacher-1", "human", "create_curriculum", [{"name": "education"}]),
                (
                    "ContentAI",
                    "ai_agent",
                    "personalize_learning",
                    [{"name": "adaptive_learning"}],
                ),
            ],
        ),
    ]

    @pytest.mark.parametrize("name,actors", DOMAINS, ids=[d[0] for d in DOMAINS])
    def test_domain(self, client, name, actors):
        for actor_name, actor_type, goal, caps in actors:
            aid = _create_actor(client, actor_name, actor_type, goal, caps)
            result = _tick_actor(client, aid, goal=goal)
            assert result["result"].get("belief_updated") is True

        # Planet tick coordinates all
        pt = _tick_planet(client)
        assert pt["actors_observed"] >= len(actors)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Entity Type Combinations
# ═══════════════════════════════════════════════════════════════════════════


class TestEntityCombinations:
    """Verify all actor type pairs work together."""

    TYPE_PAIRS = [
        ("human", "robot"),
        ("human", "ai_agent"),
        ("human", "enterprise"),
        ("human", "government"),
        ("robot", "ai_agent"),
        ("robot", "enterprise"),
        ("ai_agent", "enterprise"),
        ("enterprise", "government"),
    ]

    @pytest.mark.parametrize("type_a,type_b", TYPE_PAIRS)
    def test_type_pair(self, client, type_a, type_b):
        aid_a = _create_actor(client, f"Pair-{type_a}", type_a, f"task_{type_a}")
        aid_b = _create_actor(client, f"Pair-{type_b}", type_b, f"task_{type_b}")

        r1 = _tick_actor(client, aid_a)
        r2 = _tick_actor(client, aid_b)
        assert r1["result"].get("belief_updated") is True
        assert r2["result"].get("belief_updated") is True


# ═══════════════════════════════════════════════════════════════════════════
# 3. Constraint Variations
# ═══════════════════════════════════════════════════════════════════════════


class TestConstraintVariations:
    """Different planning constraint profiles."""

    CONSTRAINTS = [
        ("single_step", "reach_door"),
        ("two_step", "pick_item_then_deliver"),
        ("multi_step", "navigate_warehouse_pick_pack_ship"),
        ("time_constrained", "deliver_before_deadline"),
        ("resource_constrained", "optimize_with_limited_fuel"),
        ("priority_based", "urgent_before_routine"),
        ("multi_objective", "minimize_cost_maximize_speed"),
    ]

    @pytest.mark.parametrize("name,goal", CONSTRAINTS)
    def test_constraint(self, client, name, goal):
        aid = _create_actor(client, f"Constraint-{name}", "robot", goal)
        result = _tick_actor(client, aid, goal=goal)
        assert "plan" in result["result"] or "belief_updated" in result["result"]


# ═══════════════════════════════════════════════════════════════════════════
# 4. Scale Boundaries
# ═══════════════════════════════════════════════════════════════════════════


class TestScaleBoundaries:
    """Test at various scale boundaries."""

    def test_25_actors(self, client):
        self._run_scale(client, 25)

    def test_100_actors(self, client):
        self._run_scale(client, 100)

    @_skip_slow_scale
    def test_200_actors(self, client):
        self._run_scale(client, 200)

    def _run_scale(self, client, n):
        actors = [_create_actor(client, f"Scale-{n}-{i}", "robot", f"task_{i}") for i in range(n)]

        # All tick
        for aid in actors:
            _tick_actor(client, aid)

        # Planet tick coordinates all
        pt = _tick_planet(client)
        assert pt["actors_observed"] >= n


# ═══════════════════════════════════════════════════════════════════════════
# 5. Failure Recovery Patterns
# ═══════════════════════════════════════════════════════════════════════════


class TestFailureRecoveryPatterns:
    """Recovery from various failure modes."""

    def test_partial_actor_failure(self, client):
        """Some actors succeed, others may fail."""
        actors = [_create_actor(client, f"Partial-{i}", "robot", f"task_{i}") for i in range(10)]

        successes = 0
        for aid in actors:
            result = _tick_actor(client, aid)
            if result["result"].get("belief_updated") is True:
                successes += 1

        # At least 50% should succeed
        assert successes >= len(actors) * 0.5

    def test_rapid_create_delete_cycle(self, client):
        """Create and delete actors rapidly."""
        for i in range(20):
            aid = _create_actor(client, f"Rapid-{i}", "robot", f"task_{i}")
            _tick_actor(client, aid)
            # Delete is done via the delete endpoint
            client.delete(f"/api/v1/agentos/actors/{aid}")

        # System should be stable
        r = client.get("/api/v1/agentos/runtime/status")
        assert r.status_code == 200

    def test_tick_after_all_actors_removed(self, client):
        """Planet tick with no actors should not crash."""
        pt = _tick_planet(client)
        assert pt["actors_observed"] >= 0


# ═══════════════════════════════════════════════════════════════════════════
# 6. Learning Transfer
# ═══════════════════════════════════════════════════════════════════════════


class TestLearningTransfer:
    """Verify learning transfers across scenarios."""

    def test_cross_domain_lesson_sharing(self, client):
        """Lessons from one domain should be shareable to another."""
        aid = _create_actor(client, "TransferLearner", "ai_agent", "cross_domain_task")

        # Share lessons from different domains
        domains = [
            (["route_optimized", "traffic_avoided"], "success"),
            (["defect_prevented", "quality_improved"], "success"),
            (["patient_treated", "dosage_correct"], "success"),
            (["budget_optimized", "cost_reduced"], "failure"),
        ]

        for lessons, outcome in domains:
            exp = _share_experience(client, aid, outcome=outcome, lessons=lessons)
            assert exp["status"] == "ok"

        # Statistics should accumulate
        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.status_code == 200
        assert r.json()["total_experiences"] >= 4

    def test_reputation_across_scenarios(self, client):
        """Reputation should track across different scenarios."""
        aid = _create_actor(client, "RepTransfer", "ai_agent", "multi_scenario_task")

        # Consistent success
        for _ in range(10):
            _share_experience(client, aid, outcome="success", confidence=0.9)

        # Check reputation exists
        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.json()["total_reputations"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 7. Knowledge Generalization
# ═══════════════════════════════════════════════════════════════════════════


class TestKnowledgeGeneralization:
    """Handle unseen concepts gracefully."""

    def test_unseen_goal(self, client):
        """Actor with a completely novel goal should still tick."""
        aid = _create_actor(client, "NovelGoal", "ai_agent", "quantum_entanglement_routing")
        result = _tick_actor(client, aid)
        assert result["result"].get("belief_updated") is True

    def test_unseen_capability(self, client):
        """Actor with novel capabilities should be registered."""
        aid = _create_actor(
            client,
            "NovelCap",
            "robot",
            "task",
            caps=[{"name": "teleportation"}, {"name": "time_travel"}],
        )
        result = _tick_actor(client, aid)
        assert result["result"].get("belief_updated") is True

    def test_unknown_entity_type(self, client):
        """Actor with unknown type should be handled."""
        aid = _create_actor(client, "UnknownType", "alien", "explore_galaxy")
        result = _tick_actor(client, aid)
        assert result["result"].get("belief_updated") is True


# ═══════════════════════════════════════════════════════════════════════════
# 8. Workflow Generalization
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowGeneralization:
    """Different workflow patterns should all execute."""

    WORKFLOWS = [
        ("sequential", ["step_1", "step_2", "step_3"]),
        ("parallel", ["task_a", "task_b"]),
        ("conditional", ["if_true_do_this", "if_false_do_that"]),
        ("iterative", ["repeat_until_done"]),
    ]

    @pytest.mark.parametrize("name,goals", WORKFLOWS)
    def test_workflow(self, client, name, goals):
        for i, goal in enumerate(goals):
            aid = _create_actor(client, f"Workflow-{name}-{i}", "robot", goal)
            result = _tick_actor(client, aid, goal=goal)
            assert result["result"].get("belief_updated") is True


# ═══════════════════════════════════════════════════════════════════════════
# 9. World State Consistency
# ═══════════════════════════════════════════════════════════════════════════


class TestWorldStateConsistency:
    """World state remains consistent across diverse operations."""

    def test_version_monotonicity(self, client):
        """World version should never decrease."""
        versions = []
        for i in range(30):
            aid = _create_actor(client, f"VersionTest-{i}", "robot", f"task_{i}")
            _tick_actor(client, aid)
            r = client.get("/api/v1/agentos/world")
            versions.append(r.json()["version"])

        for i in range(1, len(versions)):
            assert versions[i] >= versions[i - 1]

    def test_events_append_only(self, client):
        """World events should only be appended, never removed."""
        r1 = client.get("/api/v1/agentos/world/events?limit=10000")
        count1 = r1.json()["count"]

        # Generate more events
        for i in range(10):
            aid = _create_actor(client, f"EventTest-{i}", "robot", f"task_{i}")
            _tick_actor(client, aid)

        r2 = client.get("/api/v1/agentos/world/events?limit=10000")
        count2 = r2.json()["count"]

        assert count2 >= count1


# ═══════════════════════════════════════════════════════════════════════════
# 10. Runtime Stability Under Diversity
# ═══════════════════════════════════════════════════════════════════════════


class TestRuntimeStabilityUnderDiversity:
    """Runtime remains stable under diverse workloads."""

    def test_mixed_workload(self, client):
        """Mix of different actor types and goals."""
        mixed = [
            ("Human-1", "human", "supervise"),
            ("Robot-1", "robot", "assemble"),
            ("AI-1", "ai_agent", "optimize"),
            ("Enterprise-1", "enterprise", "manage"),
            ("Gov-1", "government", "regulate"),
        ]

        for name, atype, goal in mixed:
            aid = _create_actor(client, name, atype, goal)
            _tick_actor(client, aid)

        # Planet tick handles all
        pt = _tick_planet(client)
        assert pt["actors_observed"] >= len(mixed)

    def test_rapid_goal_changes(self, client):
        """Actor changes goals rapidly between ticks."""
        aid = _create_actor(client, "GoalChanger", "ai_agent", "initial_goal")

        goals = ["goal_a", "goal_b", "goal_c", "goal_d", "goal_e"]
        for goal in goals:
            result = _tick_actor(client, aid, goal=goal)
            assert result["result"].get("belief_updated") is True


# ═══════════════════════════════════════════════════════════════════════════
# 11. Policy Evolution
# ═══════════════════════════════════════════════════════════════════════════


class TestPolicyEvolution:
    """Verify policy evolution through learning."""

    def test_policy_proposal_from_learning(self, client):
        """Policy evolution experiences should propose policies."""
        aid = _create_actor(client, "PolicyEvolver", "ai_agent", "evolve_policy")

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

    def test_world_refinement_from_lessons(self, client):
        """Lessons should refine the world."""
        aid = _create_actor(client, "WorldRefiner", "ai_agent", "refine_world")

        exp = _share_experience(
            client,
            aid,
            outcome="success",
            confidence=0.9,
            lessons=["lesson_1", "lesson_2"],
        )
        assert exp["result"]["world_refined"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 12. Generalization Matrix (Summary)
# ═══════════════════════════════════════════════════════════════════════════


class TestGeneralizationMatrix:
    """Final summary test — all dimensions pass."""

    def test_all_dimensions_pass(self, client):
        """Verify all generalization dimensions produce valid results."""
        results = {}

        # Domain
        aid = _create_actor(client, "Matrix-Domain", "robot", "generic_task")
        r = _tick_actor(client, aid)
        results["domain"] = r["result"].get("belief_updated") is True

        # Entity
        aid = _create_actor(client, "Matrix-Entity", "ai_agent", "generic_task")
        r = _tick_actor(client, aid)
        results["entity"] = r["result"].get("belief_updated") is True

        # Constraint
        aid = _create_actor(client, "Matrix-Constraint", "robot", "complex_multi_step_constrained_task")
        r = _tick_actor(client, aid)
        results["constraint"] = r["result"].get("belief_updated") is True

        # Scale
        actors = [_create_actor(client, f"Matrix-Scale-{i}", "robot", f"task_{i}") for i in range(10)]
        all_ticked = all(_tick_actor(client, a)["result"].get("belief_updated") is True for a in actors)
        results["scale"] = all_ticked

        # Learning
        exp = _share_experience(client, aid, lessons=["test_lesson"])
        results["learning"] = exp["status"] == "ok"

        # World
        r = client.get("/api/v1/agentos/world")
        results["world"] = r.status_code == 200

        # Runtime
        r = client.get("/api/v1/agentos/runtime/status")
        results["runtime"] = r.json()["cognitive"] == "ready"

        # All dimensions should pass
        for dim, passed in results.items():
            assert passed, f"Generalization dimension '{dim}' failed"
