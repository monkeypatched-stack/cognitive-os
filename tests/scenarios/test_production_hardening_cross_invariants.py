"""Production Hardening — cross-system invariants across all three
features (plan invalidation, API-level idempotency, durable
auditability) together, matching the sprint spec's section 5 end-to-end
scenarios A-D.
"""

from __future__ import annotations

import asyncio
import threading

# Deliberately NOT forcing IDEMPOTENCY_STORE_BACKEND=memory here (unlike
# test_idempotency.py/test_prompt_idempotency_e2e.py) — Scenario D's
# restart-durability test needs the real "auto" backend selection so it
# can genuinely exercise Redis when reachable, the same way
# TestScenarioD_Restart's TimelineStore restart test does.

import pytest  # noqa: E402
from fastapi import Depends, FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.monkey_brain.api.idempotency import IdempotencyStore, idempotent  # noqa: E402
from src.monkey_brain.kernel.domains.commerce import (
    list_product,
    onboard_merchant,
)  # noqa: E402
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph  # noqa: E402
from src.monkey_brain.kernel.models import PromptRequest, PromptResponse  # noqa: E402
from src.monkey_brain.kernel.pipeline.actor import Actor  # noqa: E402
from src.monkey_brain.kernel.pipeline.audit_trail import (
    query_audit_timeline,
)  # noqa: E402
from src.monkey_brain.kernel.pipeline.belief_state import (
    BeliefState,
    Plan,
    PlanStep,
)  # noqa: E402
from src.monkey_brain.kernel.pipeline.comparison.integration import (  # noqa: E402
    ComparisonIntegratedPolicy,
    _run_decide,
)
from src.monkey_brain.kernel.pipeline.execution_state import (
    CognitiveState,
)  # noqa: E402
from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
    CurrentPlanRecord,
    plan_to_dict,
)  # noqa: E402
from src.monkey_brain.kernel.pipeline.planning.goal_key import (
    canonicalize_goal,
)  # noqa: E402
from src.monkey_brain.kernel.pipeline.planning.plan_staleness import (
    capture_entity_versions,
)  # noqa: E402
from src.monkey_brain.kernel.timeline.entry import TimelineKind  # noqa: E402
from src.monkey_brain.kernel.timeline.store import TimelineStore  # noqa: E402

ACTOR_ID = "arjun"


def setup_function(_fn):
    TimelineStore.reset_for_testing()
    IdempotencyStore._instance = None


def _seeded_kg(quantity: int = 5):
    kg = KnowledgeGraph()
    store = onboard_merchant(kg, "m1", "Store", delivery_fee=1.0)["store_id"]
    milk_id = list_product(kg, store, "m1", "Milk", price=3.49, quantity=quantity)["product_id"]
    return kg, milk_id


def _plan(goal: str, milk_id: str) -> Plan:
    return Plan(
        goal=goal,
        steps=(
            PlanStep(
                action="ProductSelection",
                parameters={"selection": [{"id": milk_id, "qty": 1}]},
            ),
            PlanStep(action="OrderCreation", parameters={}),
        ),
        cost=0.0,
        confidence=0.8,
        risk=0.0,
        planner="llm",
    )


def _state(*, plan, kg, execution_id: str) -> CognitiveState:
    actor = Actor(actor_id=ACTOR_ID, tenant_id="acme")
    belief = BeliefState(actor_id=ACTOR_ID, tenant_id="acme")
    belief.update_goal(name="buy milk")
    state = CognitiveState(actor=actor, belief=belief)
    state.plan = plan
    state.prediction_result = None
    state.metrics = {"execution_id": execution_id}
    state.context = {"knowledge_graph": kg}
    return state


class TestScenarioA_StaleInventory:
    """Plan to buy milk (stock=5) -> inventory becomes 0 -> execute old
    plan. Expected: PLAN_STALE, no order, no payment, audit contains
    PLAN_INVALIDATED."""

    def test_stale_inventory_produces_no_side_effect_and_a_durable_invalidation_event(
        self,
    ):
        kg, milk_id = _seeded_kg(quantity=5)
        plan = _plan("buy milk", milk_id)
        current = CurrentPlanRecord(
            plan_id="plan_A",
            actor_id=ACTOR_ID,
            goal="buy milk",
            steps=tuple(s.action for s in plan.steps),
            score=0.9,
            plan=plan_to_dict(plan),
            entity_versions=capture_entity_versions(kg, plan),
        )

        kg.update_entity(milk_id, attributes={"quantity": 0})

        policy = ComparisonIntegratedPolicy()
        policy._current_plans[canonicalize_goal("buy milk")] = current
        state = _state(plan=None, kg=kg, execution_id="exec_A")

        result = asyncio.run(_run_decide(state, policy))

        # No order, no payment: nothing consequential to execute this tick.
        assert result.plan is None
        assert result.metrics["plan_stale"]["is_stale"] is True

        timeline = query_audit_timeline(ACTOR_ID, "exec_A")
        invalidated = [e for e in timeline if e["kind"] == "plan" and e["status"] == "invalidated"]
        assert len(invalidated) == 1
        assert invalidated[0]["plan_id"] == "plan_A"


class TestScenarioB_ClientTimeout:
    """Client sends purchase -> server creates order -> response lost ->
    client retries same request with same Idempotency-Key. Expected: one
    order, one payment, one execution; second request = IDEMPOTENCY_REPLAY
    (durably recorded)."""

    def _app(self, calls: dict):
        app = FastAPI()

        @app.post("/prompt")
        @idempotent("prompt.execute")
        async def unified_prompt(
            request: Request,
            payload: PromptRequest,
            user_id: str = Depends(lambda: ACTOR_ID),
        ) -> PromptResponse:
            calls["n"] += 1
            return PromptResponse(
                question=payload.question or "",
                query_result={"order_id": f"ORD-{calls['n']}"},
                execution_summary={"actions_taken": 1},
            )

        return app

    def test_timeout_retry_is_one_purchase_and_records_idempotency_replay(self):
        calls = {"n": 0}
        client = TestClient(self._app(calls))

        first = client.post(
            "/prompt",
            json={"question": "buy milk"},
            headers={"Idempotency-Key": "scenario-b"},
        )
        retry = client.post(
            "/prompt",
            json={"question": "buy milk"},
            headers={"Idempotency-Key": "scenario-b"},
        )

        assert first.json()["query_result"]["order_id"] == retry.json()["query_result"]["order_id"]
        assert calls["n"] == 1

        timeline = query_audit_timeline(ACTOR_ID, "scenario-b")
        replays = [
            e for e in timeline if e["kind"] == "decision" and e.get("selected_strategy") == "idempotency_replay"
        ]
        assert len(replays) == 1


class TestScenarioC_ConcurrentDuplicate:
    """Two clients simultaneously submit the same request + same
    Idempotency-Key. Expected: one logical transaction — real OS-thread
    concurrency (not asyncio.gather, which can't preempt mid-call),
    matching the exact convention grocery.py's own wallet-CAS regression
    test already established for proving this kind of race."""

    def test_only_one_of_two_truly_concurrent_requests_executes(self):
        calls = {"n": 0}
        call_lock = threading.Lock()
        app = FastAPI()

        @app.post("/prompt")
        @idempotent("prompt.execute")
        async def unified_prompt(
            request: Request,
            payload: PromptRequest,
            user_id: str = Depends(lambda: ACTOR_ID),
        ) -> PromptResponse:
            import time

            with call_lock:
                calls["n"] += 1
                n = calls["n"]
            time.sleep(0.05)  # widen the race window so both threads are genuinely in-flight together
            return PromptResponse(
                question=payload.question or "",
                query_result={"order_id": f"ORD-{n}"},
                execution_summary={"actions_taken": 1},
            )

        client = TestClient(app)
        results: list = []

        def fire():
            r = client.post(
                "/prompt",
                json={"question": "buy milk"},
                headers={"Idempotency-Key": "scenario-c"},
            )
            results.append(r)

        t1 = threading.Thread(target=fire)
        t2 = threading.Thread(target=fire)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        statuses = sorted(r.status_code for r in results)
        # Exactly one succeeds (200); the other either gets an explicit
        # in-progress 409 (genuinely raced the reservation) or, if it
        # landed after completion, the replayed 200 with the SAME body —
        # never a second distinct execution.
        assert calls["n"] == 1
        if statuses == [200, 200]:
            assert results[0].json() == results[1].json()
        else:
            assert statuses == [200, 409]


class TestScenarioD_Restart:
    """Create plan, persist, restart, change world state, attempt
    execution -> stale plan detected. Then retry the same top-level
    request with the same Idempotency-Key -> no duplicate side effect.
    Audit history survives restart."""

    def test_plan_staleness_survives_serialization_boundary(self):
        """ "Restart" simulated via CurrentPlanRecord's real Redis
        serialization boundary (to_dict/from_dict), matching
        test_checkpoint_restart.py's own established convention."""
        kg, milk_id = _seeded_kg(quantity=5)
        plan = _plan("buy milk", milk_id)
        original = CurrentPlanRecord(
            plan_id="plan_D",
            actor_id=ACTOR_ID,
            goal="buy milk",
            steps=tuple(s.action for s in plan.steps),
            score=0.9,
            plan=plan_to_dict(plan),
            entity_versions=capture_entity_versions(kg, plan),
        )
        reloaded = CurrentPlanRecord.from_dict(original.to_dict())  # "restart"

        kg.update_entity(milk_id, attributes={"quantity": 0})

        policy = ComparisonIntegratedPolicy()
        policy._current_plans[canonicalize_goal("buy milk")] = reloaded
        state = _state(plan=None, kg=kg, execution_id="exec_D")
        result = asyncio.run(_run_decide(state, policy))

        assert result.plan is None
        assert result.metrics["plan_stale"]["is_stale"] is True

    def test_audit_history_survives_timeline_store_restart_when_redis_reachable(self):
        """TimelineStore.reset_for_testing() forces a fresh backend
        selection on next use — exactly the process-restart proxy this
        session's other durability tests use. If the resolved backend is
        Redis (this dev environment's default, "auto" preferring Redis
        when reachable), previously-written entries must still be there;
        if Redis isn't reachable here, the in-memory fallback correctly
        has nothing left, which is skipped rather than falsely asserted."""
        from src.monkey_brain.kernel.pipeline.audit_trail import record_plan_event

        record_plan_event(
            "generated",
            plan_id="plan_D",
            actor_id=ACTOR_ID,
            execution_id="exec_D2",
            goal="buy milk",
        )

        TimelineStore.reset_for_testing()  # simulated restart

        store = TimelineStore()
        is_redis = type(store._backend).__name__ == "_RedisTimelineBackend"
        if not is_redis:
            pytest.skip("Redis not reachable in this environment — durability not exercised")

        timeline = query_audit_timeline(ACTOR_ID, "exec_D2")
        assert len(timeline) == 1
        assert timeline[0]["plan_id"] == "plan_D"

    def test_retry_after_restart_with_same_idempotency_key_is_still_one_side_effect(
        self,
    ):
        calls = {"n": 0}
        app = FastAPI()

        @app.post("/prompt")
        @idempotent("prompt.execute")
        async def unified_prompt(
            request: Request,
            payload: PromptRequest,
            user_id: str = Depends(lambda: ACTOR_ID),
        ) -> PromptResponse:
            calls["n"] += 1
            return PromptResponse(
                question=payload.question or "",
                query_result={"order_id": f"ORD-{calls['n']}"},
                execution_summary={"actions_taken": 1},
            )

        client = TestClient(app)
        first = client.post(
            "/prompt",
            json={"question": "buy milk"},
            headers={"Idempotency-Key": "scenario-d"},
        )

        # "Restart": IdempotencyStore is a process-wide singleton backed by
        # Redis when reachable ("auto" default) — resetting the singleton
        # reference (not the underlying Redis data) is the same
        # process-restart proxy used throughout this suite. Only assert
        # durability when the resolved backend is actually Redis.
        store_before = IdempotencyStore()
        is_redis = type(store_before._backend).__name__ == "_RedisIdempotencyBackend"
        IdempotencyStore._instance = None

        retry = client.post(
            "/prompt",
            json={"question": "buy milk"},
            headers={"Idempotency-Key": "scenario-d"},
        )

        if is_redis:
            assert retry.json() == first.json()
            assert calls["n"] == 1
        else:
            pytest.skip("Redis not reachable in this environment — cross-restart idempotency not exercised")
