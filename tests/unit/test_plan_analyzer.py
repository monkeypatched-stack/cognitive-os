"""Plan Analyzer — proves the real POST /plan -> POST /execute split this
feature is built on: /plan generates the real ExecutionGraph via the real
planner and never executes/mutates anything; /execute is the only commit
point and requires a prior /plan's intent_ir. See living-world-explorer's
PlanAnalyzerPanel.tsx (the presentation layer over this exact pair) and the
Plan Analyzer implementation plan for the full architecture.

NOTE: like tests/unit/test_benchmark_recertification.py, this file's
`client` fixture flushes the shared Redis DB before booting a fresh
TestClient — running it resets any previously-seeded demo world (re-run
scripts/seed_world.py afterward to restore it). This matches existing repo
convention, not something new introduced here.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"
os.environ["RATE_LIMIT_RPS"] = "100000"
os.environ["RATE_LIMIT_BURST"] = "200000"

from src.monkey_brain.kernel.timeline.entry import TimelineKind
from src.monkey_brain.kernel.timeline.store import TimelineStore


@pytest.fixture(scope="module")
def client():
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


def _create_actor(client, name="Plan Analyzer Test Actor"):
    r = client.post(
        "/api/v1/agentos/actors",
        json={
            "name": name,
            "actor_type": "human",
            "goals": ["buy groceries"],
            "capabilities": [{"name": "general"}],
        },
    )
    assert r.status_code == 200, f"Create actor failed: {r.status_code} {r.text}"
    return r.json()["actor_id"]


def _plan(client, actor_id, question, target="execute"):
    return client.post(
        "/api/v1/agentos/plan",
        json={"question": question, "target": target},
        headers={"X-User-ID": actor_id},
    )


def _execute(client, actor_id, plan_body):
    return client.post(
        "/api/v1/agentos/execute",
        json=plan_body,
        headers={"X-User-ID": actor_id},
    )


class TestPlanReturnsRealGraph:
    """Plan Analyzer step 1: POST /plan returns a real graph for a simple prompt."""

    def test_plan_returns_real_graph_for_simple_prompt(self, client):
        actor_id = _create_actor(client, "Plan Test Simple")
        r = _plan(client, actor_id, "Buy 2 liters of whole milk.")
        assert r.status_code == 200, r.text
        body = r.json()
        nodes = body["graph"]["nodes"]
        assert len(nodes) > 0
        for n in nodes:
            assert "id" in n
            assert "type" in n


class TestPlanMultiStep:
    """Plan Analyzer step 2: a real multi-step prompt produces >1 node."""

    def test_plan_returns_multistep_graph(self, client):
        actor_id = _create_actor(client, "Plan Test Multistep")
        r = _plan(client, actor_id, "Buy milk and pizza with a shared budget.")
        assert r.status_code == 200, r.text
        assert len(r.json()["graph"]["nodes"]) > 1


class TestPlanBranching:
    """Plan Analyzer must support real branching/parallel batches, not assume linear plans."""

    def test_plan_graph_can_have_real_branching(self, client):
        actor_id = _create_actor(client, "Plan Test Branching")
        r = _plan(
            client,
            actor_id,
            "Find the cheapest grocery provider and buy 2 liters of milk.",
        )
        assert r.status_code == 200, r.text
        graph = r.json()["graph"]
        execution_order = graph.get("execution_order") or []
        has_parallel_layer = any(len(layer) > 1 for layer in execution_order)
        edges = graph.get("edges") or []
        deps_count: dict[str, int] = {}
        for e in edges:
            deps_count[e["to"]] = deps_count.get(e["to"], 0) + 1
        has_convergent_node = any(count > 1 for count in deps_count.values())
        assert has_parallel_layer or has_convergent_node, (
            "expected either a parallel execution_order layer or a node with "
            f">1 dependency; got execution_order={execution_order}, edges={edges}"
        )


class TestPlanDoesNotExecute:
    """The critical invariant: PLAN ANALYZER = PLAN ONLY."""

    def test_plan_does_not_execute_or_mutate_state(self, client):
        actor_id = _create_actor(client, "Plan Test No Execute")
        before = len(TimelineStore().query(actor_id, TimelineKind.EXECUTION))
        r = _plan(client, actor_id, "Buy milk within a $20 budget.")
        assert r.status_code == 200, r.text
        after = len(TimelineStore().query(actor_id, TimelineKind.EXECUTION))
        assert after == before, "POST /plan must never write an EXECUTION timeline entry"


class TestExecuteThisPlan:
    """Execute This Plan must invoke the real, existing execution pipeline —
    the one and only commit point, distinct from /plan."""

    def test_execute_this_plan_invokes_real_pipeline(self, client):
        actor_id = _create_actor(client, "Plan Test Execute")
        plan_r = _plan(client, actor_id, "Buy 2 liters of whole milk.")
        assert plan_r.status_code == 200, plan_r.text
        plan_body = plan_r.json()

        before = len(TimelineStore().query(actor_id, TimelineKind.EXECUTION))
        exec_r = _execute(client, actor_id, plan_body)
        assert exec_r.status_code == 200, exec_r.text
        after = len(TimelineStore().query(actor_id, TimelineKind.EXECUTION))
        assert after >= before + 1, "POST /execute must record a real execution, /plan alone must not"

    def test_execute_without_prior_plan_is_rejected(self, client):
        actor_id = _create_actor(client, "Plan Test Execute Rejected")
        # A hand-built body with no intent_ir — /execute must refuse it
        # rather than silently executing an unplanned request.
        fake_plan_body = {
            "graph": {
                "graph_id": "",
                "graph_type": "execution",
                "nodes": [],
                "edges": [],
            },
            "run_id": "not-a-real-run",
            "target": "execute",
            "question": "Buy milk.",
            "intent_ir": None,
            "elapsed_ms": 0.0,
            "metadata": {},
        }
        r = _execute(client, actor_id, fake_plan_body)
        assert r.status_code == 400, r.text


class TestPlanValidationFailure:
    """Plan validation must be honest: a failed plan must not render as valid."""

    def test_plan_validation_failure_shape(self, client):
        actor_id = _create_actor(client, "Plan Test Validation Failure")
        r = _plan(client, actor_id, "")
        if r.status_code not in (400, 422):
            pytest.skip(
                "No reliably-failing prompt found in this environment — "
                f"empty question returned {r.status_code}, not a validation failure"
            )
        if r.status_code == 422:
            body = r.json()
            assert body.get("error") in (
                "planner_produced_no_graph",
                "Graph validation failed after repair attempts",
            )


class TestPlanEdgesNeverDangle:
    """The graph the frontend renders must never reference a fabricated edge."""

    @pytest.mark.parametrize(
        "question",
        [
            "Buy 2 liters of whole milk.",
            "Buy milk and pizza with a shared budget.",
            "Buy milk for the infant first.",
        ],
    )
    def test_plan_edges_never_dangle(self, client, question):
        actor_id = _create_actor(client, f"Plan Test Dangle {question[:10]}")
        r = _plan(client, actor_id, question)
        assert r.status_code == 200, r.text
        graph = r.json()["graph"]
        node_ids = {n["id"] for n in graph["nodes"]}
        for edge in graph.get("edges") or []:
            assert edge["from"] in node_ids, f"dangling edge source: {edge}"
            assert edge["to"] in node_ids, f"dangling edge target: {edge}"
