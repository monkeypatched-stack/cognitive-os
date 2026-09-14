"""FAULT-001/002 — deterministic fault-injection qualification tests.

kernel/testing/fault_injection.py closes a real gap: ActionExecutor already
had a fault-injection lever (`failure_rate`), but it's a flat *random*
probability on a single, globally-shared ActionExecutor instance (every
actor/society gets the same one) — unusable for "fail THIS action, for
THIS actor, deterministically." Same registry idiom as Phase 1's
mutation_hooks.py, checked in ActionExecutor._execute_action before the
capability bus is ever invoked, so a forced failure is a genuine "this
action did not run" outcome, not a capability lying about its result.

FAULT-001 also regression-tests a REAL production bug found while tracing
this: kernel/pipeline/comparison/integration.py::_execution_to_graph
reported a blocked-by-dependency step (never actually executed) with
success=False, indistinguishable from a genuine failure by the time it
reached the Comparator and then _learn_transitions — even though
_learn_transitions already had explicit, correct handling to skip
"no observation" nodes; it just never received one. Fixed by excluding a
blocked step from the execution graph entirely (comparator_runtime.py's
_compare_node_outcomes then correctly reports actual_success=None via
absence, not a status-field fallback that always resolves to False).
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.domains import (
    grocery,
)  # noqa: F401 -- registers the grocery vertical
from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.vertical_router import build_execution_engine
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.testing.fault_injection import (
    clear_forced_failures,
    register_forced_failure,
)

ACTOR_ID = "fault_test_actor"


@pytest.fixture(autouse=True)
def _clean_fault_registry():
    clear_forced_failures()
    yield
    clear_forced_failures()


def _seed_grocery(kg):
    store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    milk_id = list_product(kg, store, "merchant_a", "Milk", price=3.49, quantity=40)["product_id"]
    eggs_id = list_product(kg, store, "merchant_a", "Eggs", price=4.79, quantity=40)["product_id"]
    bread_id = list_product(kg, store, "merchant_a", "Bread", price=3.99, quantity=40)["product_id"]
    return milk_id, eggs_id, bread_id


def _sel(action_id, step_index, product_id, depends_on=()):
    return Action(
        action_id=action_id,
        capability="ProductSelection",
        step_index=step_index,
        depends_on=depends_on,
        parameters={"selection": [{"id": product_id, "qty": 1}]},
    )


@pytest.mark.asyncio
async def test_fault001_partial_failure_execution_shape():
    """ "Buy milk, then eggs, then bread. Only continue if the previous
    purchase succeeds." Forcing eggs to fail must leave milk genuinely
    succeeded, eggs genuinely (and honestly) failed, and bread never
    executed at all (blocked_by_dependency) — not silently skipped, not
    treated as a third failure."""
    kg = KnowledgeGraph()
    milk_id, eggs_id, bread_id = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    context = {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}

    register_forced_failure(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == eggs_id for s in a.parameters.get("selection", []))
        ),
        error="Simulated provider outage for eggs",
    )

    actions = (
        _sel("a0", 0, milk_id),
        _sel("a1", 1, eggs_id, depends_on=(0,)),
        _sel("a2", 2, bread_id, depends_on=(1,)),
    )

    result = await executor.execute(actions, context)

    assert result.actions[0].success is True
    assert result.actions[1].success is False
    assert result.actions[1].error == "Simulated provider outage for eggs"
    assert result.actions[1].result.get("forced_failure") is True
    # The capability bus was never invoked for eggs -- confirmed by the
    # forced_failure marker being the ONLY thing in result, not a real
    # ProductSelection payload.
    assert "selected" not in result.actions[1].result
    assert result.actions[2].success is False
    assert result.actions[2].result == {"blocked_by_dependency": 1}
    assert result.actions[2].error.startswith("blocked: dependency step 1")
    assert result.success_count == 1
    assert result.failure_count == 2


@pytest.mark.asyncio
async def test_fault001_blocked_step_is_not_learned_as_a_failure(monkeypatch):
    """The Comparator/Learning half of FAULT-001: feed the exact
    partial-failure ExecutionResult above through the real _run_comparison
    -> _apply_transition_learning path and verify milk and eggs both
    produce real learned evidence, but bread -- never executed -- produces
    NONE. This is the regression test for the _execution_to_graph fix."""
    import src.monkey_brain.kernel.comparator_runtime as comparator_module
    from src.monkey_brain.kernel.comparator_runtime import ComparatorRuntime
    from src.monkey_brain.kernel.pipeline.actor import Actor
    from src.monkey_brain.kernel.pipeline.belief_state import (
        BeliefState,
        Plan,
        PlanStep,
    )
    from src.monkey_brain.kernel.pipeline.comparison.integration import (
        _apply_transition_learning,
        _run_comparison,
    )
    from src.monkey_brain.kernel.pipeline.execution import (
        ActionOutcome,
        ExecutionResult,
    )
    from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
    from src.monkey_brain.kernel.pipeline.prediction.transitions import TransitionModel

    monkeypatch.setattr(comparator_module, "get_comparator_runtime", lambda: ComparatorRuntime())

    goal = "buy groceries in order"
    plan = Plan(
        goal=goal,
        steps=(
            PlanStep(action="Milk", description="buy milk", confidence=0.9),
            PlanStep(action="Eggs", description="buy eggs", confidence=0.9, depends_on=(0,)),
            PlanStep(action="Bread", description="buy bread", confidence=0.9, depends_on=(1,)),
        ),
        cost=0.0,
        confidence=0.9,
        risk=0.0,
        planner="llm",
    )
    actor = Actor(actor_id=ACTOR_ID, tenant_id="acme")
    belief = BeliefState(actor_id=ACTOR_ID, tenant_id="acme")
    # _learn_transitions computes goal_key from belief.goal, not plan.goal
    # -- see test_compound_disruption.py's identical fix for the full
    # account.
    belief.update_goal(name=goal)
    belief.plan = plan
    state = CognitiveState(actor=actor, belief=belief)
    state.metrics = {"execution_id": "fault-001-learning"}

    def _predicted(desc):
        return {
            "prediction": {
                "world_snapshot": {},
                "predicted_outcomes": [{"description": desc, "success": True, "probability": 0.9}],
                "expected_utility": 0.5,
            },
            "scenario_label": "Baseline",
            "probability": 0.9,
        }

    state.prediction_result = {
        "candidates": [_predicted("buy milk")],
        "selected": _predicted("buy milk"),
    }
    state.execution_result = ExecutionResult(
        actions=(
            ActionOutcome(
                action_id=f"{ACTOR_ID}_step_0",
                success=True,
                result={"selected": True},
                latency_ms=1.0,
            ),
            ActionOutcome(
                action_id=f"{ACTOR_ID}_step_1",
                success=False,
                error="Simulated provider outage for eggs",
                result={"forced_failure": True, "capability": "ProductSelection"},
                latency_ms=1.0,
            ),
            ActionOutcome(
                action_id=f"{ACTOR_ID}_step_2",
                success=False,
                error="blocked: dependency step 1 did not succeed",
                result={"blocked_by_dependency": 1},
                latency_ms=0.0,
            ),
        ),
        success_count=1,
        failure_count=2,
        goal_achieved=False,
    )

    class FakePolicy:
        def __init__(self):
            self._transition_model = TransitionModel()

    policy = FakePolicy()

    result_state = await _run_comparison(state, policy)
    assert result_state.comparison_result is not None
    node_diffs = result_state.comparison_result["node_diffs"]
    assert node_diffs["Milk"]["actual_success"] is True
    assert node_diffs["Eggs"]["actual_success"] is False
    # The bread node must report NO observation at all -- absent, or
    # explicitly None -- never a verified False.
    assert node_diffs.get("Bread", {}).get("actual_success") is None

    result_state = _apply_transition_learning(result_state, policy)
    known = policy._transition_model.known_transitions
    assert (goal, "Milk") in known
    assert (goal, "Eggs") in known
    assert (goal, "Bread") not in known


@pytest.mark.asyncio
async def test_fault002_provider_failure_is_represented_honestly_no_fake_recovery():
    """ "Buy milk. If the selected provider fails, find another provider
    and continue." Forcing the ProductSelection failure must never be
    silently papered over with a fabricated success.

    Scoped honestly: automatic replanning to an alternative provider
    WITHIN the same tick is not attempted here. A cognitive tick is one
    synchronous stage loop (Phase 1 finding) with no generic mid-tick
    replan hook -- only OrderConfirmationCapability's own domain-specific
    staleness path exists, and it reacts to AVAILABILITY going stale
    between selection and confirmation, not to a ProductSelection call
    itself failing outright. Real cross-tick recovery would need a second
    genuine planning round, which is out of scope for fault-injection
    infrastructure. This test asserts what IS real: an honest failure,
    never a fake success.
    """
    kg = KnowledgeGraph()
    milk_id, _, _ = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    context = {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}

    register_forced_failure(
        ACTOR_ID,
        trigger=lambda a: a.capability == "ProductSelection",
        error="Simulated provider outage",
    )

    actions = (_sel("a0", 0, milk_id),)
    result = await executor.execute(actions, context)

    assert result.actions[0].success is False
    assert result.actions[0].error == "Simulated provider outage"
    assert result.goal_achieved is False
    # No side effect suggesting a purchase actually happened.
    assert "selected" not in (result.actions[0].result or {})
