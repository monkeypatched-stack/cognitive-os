"""COMPOUND-001..003 — compound disruption qualification tests.

Phases 1-6 each closed one qualification gap independently. Phase 7 asks
whether pairs of them hold their OWN invariants when genuinely combined --
exactly the class of bug that never shows up in either mechanism's own
isolated tests. Composing Phase 3 (checkpoint/restart) with Phase 4
(learning inspection) found a real, previously-undiscovered defect:

kernel/pipeline/comparison/integration.py::_learn_transitions had zero
awareness of checkpoint/resume. ActionExecutor.execute() (Phase 3) tags a
replayed (checkpoint-skipped) ActionOutcome's metadata with
resumed_from_checkpoint=True, but nothing in the Compare/Learn stages ever
read it -- the tick pipeline runs compare/learn unconditionally on every
tick, resumed or not, and _execution_to_graph/_learn_transitions treat a
replayed step exactly like a freshly-executed one.

That's provably safe for a genuine mid-execution crash (the tick pipeline
is one fully synchronous stage loop with no pause/resume point -- if the
process actually crashes mid-execute, the original attempt's own
compare/learn never ran at all, so the resumed tick's pass is the first
real observation). It is NOT safe for the OTHER real reason
resume_execution_id exists -- a caller retrying because a RESPONSE was
lost, not because the server crashed (see OrderCreationCapability's own
resume_order_id precedent). If the original tick's compare/learn already
ran, re-processing the same replayed steps blends duplicate evidence into
TransitionModel.learn_from_execution's exponential moving average for one
real observation, weighted twice.

The fix reuses Phase 4's own learning_event_store.py as the idempotency
ledger (load_learning_events_for_execution(execution_id)) rather than
inventing new state -- no prior event for this execution_id means learn
normally (first attempt, or a genuine crash-and-resume); a prior event
already exists means skip (an already-learned-from retry).

COMPOUND-002/003 verify two further compositions that were checked and
found to already work correctly -- real regression coverage for
interactions that were simply never exercised together before, not
fabricated risk.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from src.monkey_brain.kernel.domains import (
    grocery,
)  # noqa: F401 -- registers the grocery vertical
from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.grocery import DelegateTaskCapability
from src.monkey_brain.kernel.domains.vertical_router import build_execution_engine
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.testing.mutation_hooks import (
    clear_mutations,
    register_mutation,
)

ACTOR_ID = "compound_test_actor"


@pytest.fixture(autouse=True)
def _clean_mutation_registry():
    clear_mutations()
    yield
    clear_mutations()


def _sel(action_id, step_index, product_id, execution_id, depends_on=()):
    return Action(
        action_id=action_id,
        capability="ProductSelection",
        step_index=step_index,
        depends_on=depends_on,
        correlation_id=execution_id,
        parameters={"selection": [{"id": product_id, "qty": 1}]},
    )


@pytest.mark.asyncio
async def test_compound001_resumed_execution_does_not_double_learn(monkeypatch):
    """Attempt 1: a real execute() -> Comparator -> Learn pass records one
    real observation for Milk. "The response was lost, the caller
    retries": a second execute() call with the SAME execution_id replays
    Milk from the real Phase 3 checkpoint (metadata.resumed_from_checkpoint
    is True) and ALSO executes a genuinely new step (Pizza). Its own
    Comparator -> Learn pass must not re-blend Milk's already-learned
    evidence a second time, while Pizza -- genuinely new -- learns
    normally."""
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
    from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
    from src.monkey_brain.kernel.pipeline.learning_event_store import (
        load_learning_events_for_execution,
    )
    from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal
    from src.monkey_brain.kernel.pipeline.prediction.transitions import TransitionModel

    monkeypatch.setattr(comparator_module, "get_comparator_runtime", lambda: ComparatorRuntime())

    kg = KnowledgeGraph()
    store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    milk_id = list_product(kg, store, "merchant_a", "Milk", price=3.49, quantity=40)["product_id"]
    pizza_id = list_product(kg, store, "merchant_a", "Pizza", price=7.99, quantity=40)["product_id"]
    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex
    goal = "buy milk and pizza (compound-001)"
    goal_key = canonicalize_goal(goal)

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

    class FakePolicy:
        def __init__(self):
            self._transition_model = TransitionModel()

    policy = FakePolicy()
    actor = Actor(actor_id=ACTOR_ID, tenant_id="acme")
    belief = BeliefState(actor_id=ACTOR_ID, tenant_id="acme")
    # _learn_transitions (comparison/integration.py) computes goal_key from
    # belief.goal.name/description, not plan.goal -- belief.goal is a
    # derived property backed by GoalTimeline, so it must be set via
    # update_goal(), not just constructing Plan(goal=goal, ...) below.
    # Without this, real learning happened under goal_key "" while this
    # test's own goal_key (computed the same way production code does)
    # never matched what was actually learned.
    belief.update_goal(name=goal)

    # Attempt 1: real execute() for Milk only, real Comparator/Learn.
    milk = _sel("a0", 0, milk_id, execution_id)
    context1 = {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}
    result1 = await executor.execute((milk,), context1)
    assert result1.actions[0].success is True
    assert not result1.actions[0].metadata.get("resumed_from_checkpoint")

    plan1 = Plan(
        goal=goal,
        steps=(PlanStep(action="Milk", description="buy milk", confidence=0.9),),
        cost=0.0,
        confidence=0.9,
        risk=0.0,
        planner="llm",
    )
    belief.plan = plan1
    state1 = CognitiveState(actor=actor, belief=belief)
    state1.metrics = {"execution_id": execution_id}
    state1.prediction_result = {
        "candidates": [_predicted("buy milk")],
        "selected": _predicted("buy milk"),
    }
    state1.execution_result = result1

    state1 = await _run_comparison(state1, policy)
    state1 = _apply_transition_learning(state1, policy)

    milk_after_1 = policy._transition_model.known_transitions[(goal_key, "Milk")]
    assert len(milk_after_1) == 1
    events_after_1 = load_learning_events_for_execution(execution_id)
    assert len(events_after_1) == 1
    assert events_after_1[0].action_key == "Milk"

    # Attempt 2: "the response was lost, the caller retries" -- same
    # execution_id, full two-item plan. Milk replays from the real
    # checkpoint (never re-invokes ProductSelection); Pizza executes for
    # the first time.
    pizza = _sel("a1", 1, pizza_id, execution_id, depends_on=(0,))
    context2 = {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}
    result2 = await executor.execute((milk, pizza), context2)
    assert result2.actions[0].success is True
    assert result2.actions[0].metadata.get("resumed_from_checkpoint") is True
    assert result2.actions[1].success is True
    assert not result2.actions[1].metadata.get("resumed_from_checkpoint")

    plan2 = Plan(
        goal=goal,
        steps=(
            PlanStep(action="Milk", description="buy milk", confidence=0.9),
            PlanStep(action="Pizza", description="buy pizza", confidence=0.9, depends_on=(0,)),
        ),
        cost=0.0,
        confidence=0.9,
        risk=0.0,
        planner="llm",
    )
    belief.plan = plan2
    state2 = CognitiveState(actor=actor, belief=belief)
    state2.metrics = {"execution_id": execution_id}
    state2.prediction_result = {
        "candidates": [_predicted("buy milk and pizza")],
        "selected": _predicted("buy milk and pizza"),
    }
    state2.execution_result = result2

    state2 = await _run_comparison(state2, policy)
    state2 = _apply_transition_learning(state2, policy)

    # Milk: unchanged -- not double-blended.
    milk_after_2 = policy._transition_model.known_transitions[(goal_key, "Milk")]
    assert milk_after_2 == milk_after_1
    events_for_milk = [e for e in load_learning_events_for_execution(execution_id) if e.action_key == "Milk"]
    assert len(events_for_milk) == 1

    # Pizza: genuinely new -- learned normally, not suppressed by the fix.
    assert (goal_key, "Pizza") in policy._transition_model.known_transitions
    events_for_pizza = [e for e in load_learning_events_for_execution(execution_id) if e.action_key == "Pizza"]
    assert len(events_for_pizza) == 1


@pytest.mark.asyncio
async def test_compound002_world_mutation_detected_on_a_fresh_step_after_resume():
    """A plan is interrupted after its first step (Milk) completes and is
    checkpointed. Before the "restart" resumes the plan, Pizza -- the
    SECOND, not-yet-completed step -- goes out of stock. The resumed
    tick's OrderConfirmation (Phase 1's real staleness-detection target)
    must still detect this and replan to the real alternative, exactly as
    it does on a non-resumed execution -- Milk being replayed from
    checkpoint rather than freshly selected must not change that."""
    kg = KnowledgeGraph()
    store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    milk_id = list_product(kg, store, "merchant_a", "Milk", price=3.49, quantity=40)["product_id"]
    pizza_id = list_product(kg, store, "merchant_a", "Frozen Cheese Pizza", price=7.99, quantity=10)["product_id"]
    pizza_alt_id = list_product(kg, store, "merchant_a", "Frozen Veggie Pizza", price=8.49, quantity=10)["product_id"]
    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex

    milk = _sel("a0", 0, milk_id, execution_id)
    pizza = _sel("a1", 1, pizza_id, execution_id, depends_on=(0,))
    order_creation = Action(action_id="a2", capability="OrderCreation", step_index=2, depends_on=(0, 1))
    order_confirmation = Action(action_id="a3", capability="OrderConfirmation", step_index=3, depends_on=(2,))

    # "Before the crash": only Milk completes.
    context1 = {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}
    result1 = await executor.execute((milk,), context1)
    assert result1.actions[0].success is True

    def mutate(kg):
        kg.update_entity(pizza_id, attributes={"quantity": 0})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == pizza_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    # "Restart": full plan, same execution_id. Milk replays from
    # checkpoint; Pizza executes fresh and immediately goes stale via the
    # registered mutation, before OrderConfirmation ever runs.
    context2 = {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}
    result2 = await executor.execute((milk, pizza, order_creation, order_confirmation), context2)

    assert result2.actions[0].metadata.get("resumed_from_checkpoint") is True
    assert kg.get_entity(pizza_id).attributes["quantity"] == 0

    confirm_outcome = result2.actions[3]
    assert confirm_outcome.success, f"OrderConfirmation should replan, not fail: {confirm_outcome.error}"
    confirmed_ids = {p["id"] for p in confirm_outcome.result["product"]}
    assert pizza_id not in confirmed_ids
    assert pizza_alt_id in confirmed_ids
    assert milk_id in confirmed_ids


@pytest.mark.asyncio
async def test_compound003_concurrent_delegated_transactions_no_oversell():
    """Two different delegating actors each hand off a real
    (ProductSelection -> OrderCreation) task to the SAME recipient actor
    for the SAME single-unit product, fired concurrently via
    asyncio.gather through DelegateTaskCapability's real in-process
    fallback (Phase 6). Phase 5's contention-safety guarantee (try_reserve's
    CAS) must hold through this dispatch indirection exactly as it does for
    a direct ActionExecutor call -- exactly one delegated transaction gets
    a real reservation, the other an honest backorder."""
    from src.monkey_brain.kernel.society.domain import (
        ActorIdentity,
        ActorProfile,
        ActorType,
    )
    from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

    def _register(pr, name, society_id=None):
        kwargs = {}
        if society_id is not None:
            kwargs["society_id"] = society_id
        return pr.register_actor(
            ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN)),
            **kwargs,
        )

    pr = PlanetaryRuntime()
    club = pr.create_society("Compound Test 003", society_type="community")
    alice = _register(pr, "Alice Compound", society_id=club.society.society_id)
    carol = _register(pr, "Carol Compound", society_id=club.society.society_id)
    _register(pr, "Recipient Compound", society_id=club.society.society_id)

    kg = pr.knowledge_graph
    store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    product_id = list_product(kg, store, "merchant_a", "Limited Edition Mug", price=9.99, quantity=1)["product_id"]

    def _delegate(sender):
        return DelegateTaskCapability().handle(
            {
                "context": {
                    "planetary_runtime": pr,
                    "actor_id": sender.actor_id,
                    "actor_role": sender.actor_id,
                },
                "parameters": {
                    "target_actor": "Recipient Compound",
                    "tasks": [
                        {
                            "capability": "ProductSelection",
                            "parameters": {"selection": [{"id": product_id, "qty": 1}]},
                        },
                        {
                            "capability": "OrderCreation",
                            "parameters": {},
                            "depends_on": [0],
                        },
                    ],
                },
            }
        )

    result_a, result_c = await asyncio.gather(_delegate(alice), _delegate(carol))

    def backordered(result):
        assert result["success_count"] == 2
        order_outcome = result["actions"][1]
        assert order_outcome["success"] is True
        return product_id in {b["product_id"] for b in order_outcome["result"].get("backordered", [])}

    a_backordered = backordered(result_a)
    c_backordered = backordered(result_c)
    assert a_backordered != c_backordered

    import time

    entity = kg.get_entity(product_id)
    active = [r for r in entity.attributes.get("reservations", []) if r.get("until", 0) > time.time()]
    assert len(active) == 1
    assert sum(r.get("qty", 0) for r in active) == 1
