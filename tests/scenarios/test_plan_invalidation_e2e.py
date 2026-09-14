"""Production Hardening — Plan Invalidation / Stale-World Revalidation:
end-to-end regression tests driving the REAL `_run_decide`
(kernel/pipeline/comparison/integration.py), matching
test_plan_hysteresis_goal_scoping.py's own established convention (real
dataclasses, real KnowledgeGraph, no mocks, Redis calls are non-fatal
no-ops without a live Redis so these run standalone).

Covers the sprint spec's Tests 1-7 under "PLAN INVALIDATION / WORLD-STATE
REVALIDATION". "Restart" is simulated the same honest way
test_checkpoint_restart.py already established: a CurrentPlanRecord
round-tripped through to_dict()/from_dict() (its real serialization
boundary) rather than actually killing the interpreter.
"""

from __future__ import annotations

import asyncio

from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
from src.monkey_brain.kernel.pipeline.actor import Actor
from src.monkey_brain.kernel.pipeline.belief_state import BeliefState, Plan, PlanStep
from src.monkey_brain.kernel.pipeline.comparison.integration import (
    ComparisonIntegratedPolicy,
    _run_decide,
)
from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
    CurrentPlanRecord,
    plan_to_dict,
)
from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal
from src.monkey_brain.kernel.pipeline.planning.plan_staleness import (
    capture_entity_versions,
)
from src.monkey_brain.kernel.pipeline.audit_trail import query_audit_timeline
from src.monkey_brain.kernel.timeline.store import TimelineStore

ACTOR_ID = "arjun"


def setup_function(_fn):
    TimelineStore.reset_for_testing()


def _seeded_kg(quantity: int = 5, price: float = 3.49):
    kg = KnowledgeGraph()
    store = onboard_merchant(kg, "m1", "Store", delivery_fee=1.0)["store_id"]
    milk_id = list_product(kg, store, "m1", "Milk", price=price, quantity=quantity)["product_id"]
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


def _state(*, plan, prediction_result, kg, belief_goal: str, execution_id: str = "exec_1") -> CognitiveState:
    actor = Actor(actor_id=ACTOR_ID, tenant_id="acme")
    belief = BeliefState(actor_id=ACTOR_ID, tenant_id="acme")
    belief.update_goal(name=belief_goal)
    state = CognitiveState(actor=actor, belief=belief)
    state.plan = plan
    state.prediction_result = prediction_result
    state.metrics = {"execution_id": execution_id}
    state.context = {"knowledge_graph": kg}
    return state


def _standing_record(kg, plan: Plan, *, plan_id: str = "standing", score: float = 0.8) -> CurrentPlanRecord:
    return CurrentPlanRecord(
        plan_id=plan_id,
        actor_id=ACTOR_ID,
        goal=plan.goal,
        steps=tuple(s.action for s in plan.steps),
        step_descriptions=tuple(s.description for s in plan.steps),
        score=score,
        plan=plan_to_dict(plan),
        entity_versions=capture_entity_versions(kg, plan),
    )


def _prediction_result(probability: float = 0.6, expected_utility: float = 0.5) -> dict:
    return {
        "selected": {
            "probability": probability,
            "prediction": {"expected_utility": expected_utility},
        }
    }


class TestUnchangedWorldExecutesNormally:
    """Test 1: create plan with stock=5, execute without world change — PASS."""

    def test_fresh_plan_replaces_and_captures_entity_versions(self):
        kg, milk_id = _seeded_kg(quantity=5)
        policy = ComparisonIntegratedPolicy()
        plan = _plan("buy milk", milk_id)
        state = _state(
            plan=plan,
            prediction_result=_prediction_result(),
            kg=kg,
            belief_goal="buy milk",
        )

        result = asyncio.run(_run_decide(state, policy))

        assert result.plan is plan
        assert result.metrics["decide_action"] == "replace"
        goal_key = canonicalize_goal("buy milk")
        record = policy._current_plans[goal_key]
        assert record.entity_versions == {milk_id: 0}
        assert "plan_stale" not in result.metrics


class TestDepletedStockBlocksExecution:
    """Test 2: create plan with stock=5, deplete to 0, attempt to keep
    executing the cached plan. Expected: PLAN_STALE, no order/payment
    (state.plan stays empty, never the stale plan) — and, per Test 5, a
    fresh replacement plan when one IS available this tick."""

    def test_stale_current_plan_is_not_assigned_for_execution_when_nothing_fresh(self):
        kg, milk_id = _seeded_kg(quantity=5)
        plan = _plan("buy milk", milk_id)
        current = _standing_record(kg, plan, score=0.9)

        kg.update_entity(milk_id, attributes={"quantity": 0})

        policy = ComparisonIntegratedPolicy()
        policy._current_plans[canonicalize_goal("buy milk")] = current
        # Nothing fresh generated this tick (has_new_plan=False) — the
        # realistic "incremental scheduling skipped, nothing new to
        # compare" case for a bare autonomous tick.
        state = _state(plan=None, prediction_result=None, kg=kg, belief_goal="buy milk")

        result = asyncio.run(_run_decide(state, policy))

        assert result.plan is None
        assert result.metrics["plan_stale"]["is_stale"] is True
        assert "out of stock" in result.metrics["plan_stale"]["affected_assumptions"][0]["reason"]

    def test_stale_current_plan_is_replaced_when_a_fresh_plan_exists(self):
        """Test 5: stale plan -> invalidate -> a genuinely fresh plan
        generated this tick is accepted unconditionally (mirrors the
        existing last_execution_failed bypass), not required to clear the
        normal hysteresis score margin over a plan we already know is
        unsafe. The old plan is invalidated, not overwritten — both
        remain in the durable audit trail (Test 5's "plan_001 invalidated
        / plan_002 active")."""
        kg, milk_id = _seeded_kg(quantity=5)
        old_plan = _plan("buy milk", milk_id)
        current = _standing_record(kg, old_plan, plan_id="plan_001", score=0.95)

        kg.update_entity(milk_id, attributes={"quantity": 0})

        policy = ComparisonIntegratedPolicy()
        policy._current_plans[canonicalize_goal("buy milk")] = current

        new_plan = _plan("buy milk", milk_id)  # freshly (re)planned this tick
        state = _state(
            plan=new_plan,
            prediction_result=_prediction_result(0.3, 0.1),
            kg=kg,
            belief_goal="buy milk",
            execution_id="exec_2",
        )

        result = asyncio.run(_run_decide(state, policy))

        assert result.plan is new_plan
        assert result.metrics["decide_action"] == "replace"
        assert "stale" in result.metrics["decide_reason"]

        timeline = query_audit_timeline(ACTOR_ID, "exec_2")
        statuses = {(e["plan_id"], e["status"]) for e in timeline if e["kind"] == "plan"}
        assert ("plan_001", "invalidated") in statuses
        new_plan_id = result.metrics["decide_new_plan_id"]
        assert (new_plan_id, "generated") in statuses


class TestPriceChangeBlocksExecution:
    """Test 3: price change beyond original assumptions -> stale, no
    side effect (this check treats any tracked-entity change as stale,
    not a budget-specific comparison — the invariant it proves is
    identical: no consequential side effect from outdated assumptions)."""

    def test_price_change_marks_plan_stale(self):
        kg, milk_id = _seeded_kg(quantity=5, price=3.49)
        plan = _plan("buy milk", milk_id)
        current = _standing_record(kg, plan, score=0.9)
        kg.update_entity(milk_id, attributes={"price": 999.0})

        policy = ComparisonIntegratedPolicy()
        policy._current_plans[canonicalize_goal("buy milk")] = current
        state = _state(plan=None, prediction_result=None, kg=kg, belief_goal="buy milk")

        result = asyncio.run(_run_decide(state, policy))

        assert result.plan is None
        assert result.metrics["plan_stale"]["is_stale"] is True


class TestProviderAvailabilityChangeBlocksExecution:
    """Test 4: the referenced product's own availability changing
    (store closed / delisted) is caught the same way stock/price are —
    any change to an entity the plan actually depends on. remove_product
    soft-delists (attribute change + version bump) rather than hard-
    deleting the KG entity, so this lands on the generic "changed since
    cached" path, not the separate "no longer exists" one — both are
    is_stale=True, which is the actual invariant being proven."""

    def test_delisted_product_is_stale(self):
        kg, milk_id = _seeded_kg(quantity=5)
        plan = _plan("buy milk", milk_id)
        current = _standing_record(kg, plan, score=0.9)

        from src.monkey_brain.kernel.domains.commerce import remove_product

        remove_product(kg, milk_id, "m1")

        policy = ComparisonIntegratedPolicy()
        policy._current_plans[canonicalize_goal("buy milk")] = current
        state = _state(plan=None, prediction_result=None, kg=kg, belief_goal="buy milk")

        result = asyncio.run(_run_decide(state, policy))

        assert result.plan is None
        assert result.metrics["plan_stale"]["is_stale"] is True

    def test_entity_that_truly_no_longer_resolves_is_stale(self):
        """A referenced entity absent from the live KG entirely (a
        genuinely different world snapshot, e.g. post-restart against a
        pruned catalog) hits the other branch explicitly."""
        kg, milk_id = _seeded_kg(quantity=5)
        plan = _plan("buy milk", milk_id)
        current = _standing_record(kg, plan, score=0.9)

        fresh_kg = KnowledgeGraph()  # milk_id doesn't exist here at all

        policy = ComparisonIntegratedPolicy()
        policy._current_plans[canonicalize_goal("buy milk")] = current
        state = _state(plan=None, prediction_result=None, kg=fresh_kg, belief_goal="buy milk")

        result = asyncio.run(_run_decide(state, policy))

        assert result.plan is None
        assert result.metrics["plan_stale"]["is_stale"] is True
        assert result.metrics["plan_stale"]["affected_assumptions"][0]["reason"] == "no longer exists"


class TestRestartThenWorldChangeDetectsStaleness:
    """Test 6: restart after plan creation, then world change, then
    attempt execution -> stale plan detected after restart. "Restart" is
    simulated via CurrentPlanRecord's real to_dict()/from_dict() Redis
    serialization boundary, the same honest convention
    test_checkpoint_restart.py already established."""

    def test_reloaded_record_from_before_restart_is_still_correctly_detected_stale(
        self,
    ):
        kg, milk_id = _seeded_kg(quantity=5)
        plan = _plan("buy milk", milk_id)
        original_record = _standing_record(kg, plan, plan_id="pre-restart", score=0.9)

        # Simulate "process restarted, reloaded from Redis."
        reloaded_record = CurrentPlanRecord.from_dict(original_record.to_dict())
        assert reloaded_record.entity_versions == original_record.entity_versions

        kg.update_entity(milk_id, attributes={"quantity": 0})

        policy = ComparisonIntegratedPolicy()
        policy._current_plans[canonicalize_goal("buy milk")] = reloaded_record
        state = _state(plan=None, prediction_result=None, kg=kg, belief_goal="buy milk")

        result = asyncio.run(_run_decide(state, policy))

        assert result.plan is None
        assert result.metrics["plan_stale"]["is_stale"] is True


class TestCachedStalePlanNeverReturnedAsExecutable:
    """Test 7: cached stale plan requested again for the same goal ->
    never returned as executable, across repeated attempts."""

    def test_repeated_requests_never_execute_the_stale_plan(self):
        kg, milk_id = _seeded_kg(quantity=5)
        plan = _plan("buy milk", milk_id)
        current = _standing_record(kg, plan, score=0.9)
        kg.update_entity(milk_id, attributes={"quantity": 0})

        policy = ComparisonIntegratedPolicy()
        policy._current_plans[canonicalize_goal("buy milk")] = current

        for _ in range(3):
            state = _state(plan=None, prediction_result=None, kg=kg, belief_goal="buy milk")
            result = asyncio.run(_run_decide(state, policy))
            assert result.plan is None
            assert result.metrics["plan_stale"]["is_stale"] is True
