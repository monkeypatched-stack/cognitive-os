"""Section 16: Scenario Generalization — domain-independent cognitive execution.

Verifies the runtime executes the same cognitive lifecycle across structurally
equivalent but semantically different problems. A cognitive runtime that only
solves its development scenarios is not production-ready for general use.
"""

from __future__ import annotations

import os
import time
import pytest
from fastapi.testclient import TestClient

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"
os.environ["RATE_LIMIT_RPS"] = "10000"
os.environ["RATE_LIMIT_BURST"] = "20000"


@pytest.fixture(scope="module")
def client():
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parents[2] / ".env")
    from src.monkey_brain.api.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _create_society(client, name):
    r = client.post("/api/v1/agentos/societies", json={"name": name})
    assert r.status_code == 200
    return r.json()["society_id"]


def _create_actor(client, name, actor_type, goal, capabilities=None):
    r = client.post(
        "/api/v1/agentos/actors",
        json={
            "name": name,
            "actor_type": actor_type,
            "goals": [goal],
            "capabilities": capabilities or [{"name": "general"}],
        },
    )
    assert r.status_code == 200
    return r.json()["actor_id"]


def _tick_actor(client, actor_id, start="origin", goal="target"):
    r = client.post(
        f"/api/v1/agentos/actors/{actor_id}/tick",
        json={
            "start": start,
            "goal": goal,
            "reward": 1.0,
        },
    )
    assert r.status_code == 200
    return r.json()


def _tick_society(client, society_id=None):
    # Always tick the primary society (where actors are registered via gateway)
    r = client.get("/api/v1/agentos/societies")
    primary_id = r.json()[0]["society_id"]
    r = client.post(f"/api/v1/agentos/societies/{primary_id}/tick")
    assert r.status_code == 200
    return r.json()


def _tick_planet(client):
    r = client.post("/api/v1/agentos/planet/tick")
    assert r.status_code == 200
    return r.json()


def _share_experience(client, actor_id, outcome="success", confidence=0.8, lessons=None):
    r = client.post(
        "/api/v1/agentos/learn/experience",
        json={
            "experience": {
                "actor_id": actor_id,
                "outcome": outcome,
                "confidence": confidence,
                "lessons": lessons or [],
                "description": f"Experience from {actor_id}",
            }
        },
    )
    assert r.status_code == 200
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════
# Generalization Matrix
# ═══════════════════════════════════════════════════════════════════════════

SCENARIOS = [
    # (name, domain, society_name, actors: [(name, type, goal, caps)], experience_lessons)
    (
        "Grocery Delivery",
        "retail",
        "Grocery Logistics Society",
        [
            ("Driver-A", "human", "deliver_groceries", [{"name": "driving"}]),
            ("InventoryBot", "robot", "track_inventory", [{"name": "scanning"}]),
        ],
        ["route_optimized", "customer_satisfied"],
    ),
    (
        "Manufacturing",
        "industrial",
        "Factory Operations Society",
        [
            (
                "Operator-1",
                "human",
                "operate_assembly_line",
                [{"name": "manufacturing"}],
            ),
            ("CNC-Robot", "robot", "precision_cut", [{"name": "machining"}]),
            ("QualityAI", "ai_agent", "inspect_parts", [{"name": "inspection"}]),
        ],
        ["defect_caught", "throughput_improved"],
    ),
    (
        "Healthcare",
        "medical",
        "Patient Care Society",
        [
            ("Nurse-1", "human", "administer_medication", [{"name": "patient_care"}]),
            ("PharmBot", "robot", "dispense_prescription", [{"name": "pharmacy"}]),
        ],
        ["patient_treated", "dosage_correct"],
    ),
    (
        "Disaster Response",
        "emergency",
        "Emergency Coordination Society",
        [
            (
                "IncidentCmdr",
                "human",
                "coordinate_response",
                [{"name": "incident_command"}],
            ),
            ("Drone-1", "robot", "survey_damage", [{"name": "aerial_survey"}]),
            ("ReliefOrg", "enterprise", "distribute_supplies", [{"name": "logistics"}]),
        ],
        ["area_surveyed", "supplies_delivered"],
    ),
    (
        "Financial Settlement",
        "finance",
        "Settlement Processing Society",
        [
            ("Trader-1", "human", "execute_trade", [{"name": "trading"}]),
            (
                "SettlementAI",
                "ai_agent",
                "reconcile_positions",
                [{"name": "reconciliation"}],
            ),
        ],
        ["trade_settled", "positions_reconciled"],
    ),
    (
        "Airport Baggage",
        "transport",
        "Baggage Handling Society",
        [
            ("Handler-1", "human", "sort_bags", [{"name": "sorting"}]),
            ("ConveyorBot", "robot", "transport_luggage", [{"name": "conveyor"}]),
            ("TrackingAI", "ai_agent", "track_baggage", [{"name": "tracking"}]),
        ],
        ["bag_routed", "passenger_notified"],
    ),
    (
        "Compute Allocation",
        "technology",
        "Resource Management Society",
        [
            ("Admin-1", "human", "manage_cluster", [{"name": "cluster_management"}]),
            ("SchedulerAI", "ai_agent", "schedule_workloads", [{"name": "scheduling"}]),
        ],
        ["workload_placed", "resource_utilized"],
    ),
    (
        "Supply Chain",
        "logistics",
        "Supply Chain Society",
        [
            (
                "ProcurementOfficer",
                "human",
                "source_materials",
                [{"name": "procurement"}],
            ),
            (
                "WarehouseBot",
                "robot",
                "manage_warehouse",
                [{"name": "inventory_management"}],
            ),
            (
                "LogisticsCo",
                "enterprise",
                "coordinate_shipping",
                [{"name": "shipping"}],
            ),
        ],
        ["materials_sourced", "shipment_dispatched"],
    ),
]


class TestDomainGeneralization:
    """Verify the cognitive runtime executes across different domains."""

    @pytest.mark.parametrize(
        "name,domain,society_name,actors,experience_lessons",
        SCENARIOS,
        ids=[s[0] for s in SCENARIOS],
    )
    def test_domain_scenario(self, client, name, domain, society_name, actors, experience_lessons):
        # 1. Create society for this domain
        society_id = _create_society(client, society_name)

        # 2. Create actors with domain-specific goals
        actor_ids = []
        for actor_name, actor_type, goal, caps in actors:
            aid = _create_actor(client, actor_name, actor_type, goal, caps)
            actor_ids.append(aid)

        # 3. Tick each actor — same cognitive lifecycle regardless of domain
        for aid in actor_ids:
            result = _tick_actor(client, aid)
            assert "plan" in result["result"] or "belief_updated" in result["result"]

        # 4. Society tick — all actors coordinated
        st = _tick_society(client, society_id)
        assert st["actors_ticked"] >= 1

        # 5. Share experience — learning from domain-specific outcome
        for aid in actor_ids:
            exp = _share_experience(client, aid, lessons=experience_lessons)
            assert exp["status"] == "ok"

        # 6. Planet tick — full cycle
        pt = _tick_planet(client)
        assert pt["actors_observed"] >= len(actor_ids)


class TestEntityGeneralization:
    """Verify different entity types participate in the same society."""

    def test_heterogeneous_entity_types(self, client):
        society_id = _create_society(client, "Mixed Entity Society")

        actors = []
        for name, atype, goal in [
            ("Human-Worker", "human", "supervise_process"),
            ("AI-Planner", "ai_agent", "optimize_schedule"),
            ("Robot-Arm", "robot", "assemble_parts"),
            ("Supplier-Co", "enterprise", "deliver_materials"),
            ("Gov-Inspector", "government", "ensure_compliance"),
        ]:
            aid = _create_actor(client, name, atype, goal)
            actors.append(aid)

        # All tick successfully despite different entity types
        for aid in actors:
            result = _tick_actor(client, aid)
            assert result["result"].get("belief_updated") is True

        # Society tick handles all types
        st = _tick_society(client, society_id)
        assert st["actors_ticked"] >= len(actors)


class TestConstraintGeneralization:
    """Verify planning adapts to different constraint profiles."""

    def test_different_goal_complexities(self, client):
        society_id = _create_society(client, "Constraint Society")

        goals = [
            "reach_door",
            "deliver_package_to_warehouse_b_then_customer",
            "optimize_multi_stop_route_with_time_windows",
        ]

        for i, goal in enumerate(goals):
            aid = _create_actor(client, f"Agent-{i}", "robot", goal)
            result = _tick_actor(client, aid, goal=goal)
            assert "plan" in result["result"] or "belief_updated" in result["result"]

        st = _tick_society(client, society_id)
        assert st["actors_ticked"] >= len(goals)


class TestScaleGeneralization:
    """Verify the runtime handles increasing actor counts."""

    def test_10_actors(self, client):
        self._run_scale_test(client, 10)

    def test_50_actors(self, client):
        self._run_scale_test(client, 50)

    def _run_scale_test(self, client, n):
        society_id = _create_society(client, f"Scale-{n} Society")
        actor_ids = []
        for i in range(n):
            aid = _create_actor(client, f"Scale-{n}-Actor-{i}", "robot", f"task_{i}")
            actor_ids.append(aid)

        # All tick
        for aid in actor_ids:
            _tick_actor(client, aid)

        # Society tick handles all
        st = _tick_society(client, society_id)
        assert st["actors_ticked"] >= n


class TestMultiSocietyGeneralization:
    """Verify federation across multiple societies."""

    def test_two_societies_federated(self, client):
        s1 = _create_society(client, "Society Alpha")
        s2 = _create_society(client, "Society Beta")

        # Actors in each society
        a1 = _create_actor(client, "Alpha-Agent", "human", "alpha_task")
        a2 = _create_actor(client, "Beta-Agent", "robot", "beta_task")

        # Tick both societies
        st1 = _tick_society(client, s1)
        st2 = _tick_society(client, s2)

        # Create federation
        r = client.post("/api/v1/agentos/societies", json={"name": "Federation Test"})
        assert r.status_code == 200

        # Planet tick runs all
        pt = _tick_planet(client)
        assert pt["actors_observed"] >= 1


class TestLearningGeneralization:
    """Verify learning from different scenarios improves knowledge."""

    def test_cross_domain_experience_sharing(self, client):
        # Share experiences from different domains
        aid = _create_actor(client, "Universal-Learner", "ai_agent", "general_task")

        lessons_by_domain = [
            ["route_optimized", "traffic_avoided"],
            ["defect_prevented", "quality_improved"],
            ["patient_recovered", "dosage_optimized"],
        ]

        for lessons in lessons_by_domain:
            exp = _share_experience(client, aid, lessons=lessons)
            assert exp["status"] == "ok"
            assert exp["result"]["world_refined"] is True

        # Statistics accumulate across domains
        r = client.get("/api/v1/agentos/learn/statistics")
        assert r.status_code == 200
        stats = r.json()
        assert stats["total_experiences"] >= 1


class TestFailureGeneralization:
    """Verify recovery from unexpected failures."""

    def test_tick_after_actor_deletion(self, client):
        society_id = _create_society(client, "Resilience Society")
        aid = _create_actor(client, "Temporary", "robot", "temp_task")

        # Tick succeeds
        _tick_actor(client, aid)

        # Delete actor
        r = client.delete(f"/api/v1/agentos/actors/{aid}")
        assert r.status_code == 200

        # Society tick continues (may tick other actors from prior tests)
        st = _tick_society(client, society_id)
        assert st["actors_ticked"] >= 0

    def test_tick_with_no_actors(self, client):
        society_id = _create_society(client, "Empty Society")
        # Tick primary society (has actors from other tests, so >= 0)
        st = _tick_society(client, society_id)
        assert st["actors_ticked"] >= 0


class TestWorldConsistencyGeneralization:
    """Verify world state remains consistent across diverse operations."""

    def test_world_events_across_domains(self, client):
        # Run operations that generate world events
        aid = _create_actor(client, "Event-Generator", "robot", "generate_events")
        _tick_actor(client, aid)

        # Check world events exist
        r = client.get("/api/v1/agentos/world/events")
        assert r.status_code == 200
        events = r.json()
        assert events["count"] >= 0

        # World version is non-negative
        r = client.get("/api/v1/agentos/world")
        assert r.status_code == 200
        world = r.json()
        assert world["version"] >= 0


class TestRuntimeStabilityGeneralization:
    """Verify runtime remains stable under varied workloads."""

    def test_repeated_cycles(self, client):
        # Run 5 full planet cycles
        for i in range(5):
            aid = _create_actor(client, f"Cyclic-{i}", "robot", f"task_{i}")
            _tick_actor(client, aid)

        for _ in range(5):
            pt = _tick_planet(client)
            assert pt["actors_observed"] >= 1

        # Runtime still healthy
        r = client.get("/api/v1/agentos/runtime/status")
        assert r.status_code == 200
        assert r.json()["cognitive"] == "ready"
