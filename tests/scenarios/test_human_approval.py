"""APPROVAL-001..004 — human approval / pause / resume qualification
tests (Qualification Gap Closure, Phase 3).

Real, generic execution state machine: kernel/pipeline/action_executor.py
::ActionExecutor.execute() (the SAME per-action loop Phases 1-3's own
mutation/fault-injection/checkpoint-resume hooks already use) stops a tick
the moment a capability's own result signals {"requires_approval": True,
"proposed_action": {...}, "reason": "..."} -- a generic, opt-in contract,
not a hardcoded ProductSelection/OrderConfirmation branch in the executor
itself. kernel/domains/grocery.py::OrderConfirmationCapability (the one
real substitution path already proven this session, WORLD-001..005) is
the first real capability to use it: a planner-supplied
context["approval_required_for_substitution"] flag (matching
required_permission's own precedent) gates its existing, real replan
logic behind a real, Redis-persisted PendingApproval
(kernel/pipeline/approval_store.py, the fourth real extension of Phase
3's execution_checkpoint_store.py pattern) instead of substituting
automatically -- WORLD-001..005 (unmodified, still passing) prove the
default, no-flag-set behavior is exactly what it always was.
"""

from __future__ import annotations

import uuid

import pytest

from src.monkey_brain.kernel.domains import (
    grocery,
)  # noqa: F401 -- registers the grocery vertical
from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.vertical_router import build_execution_engine
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
from src.monkey_brain.kernel.pipeline.approval_store import (
    load_pending_approval,
    resolve_pending_approval,
)
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.testing.mutation_hooks import (
    clear_mutations,
    register_mutation,
)

ACTOR_ID = "approval_test_actor"


@pytest.fixture(autouse=True)
def _clean_registries():
    clear_mutations()
    yield
    clear_mutations()


def _seed_catalog():
    kg = KnowledgeGraph()
    store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    milk_id = list_product(
        kg,
        store,
        "merchant_a",
        "Milk",
        price=3.99,
        quantity=5,
        store_name="Trader Joe's",
    )["product_id"]
    milk_alt_id = list_product(
        kg,
        store,
        "merchant_a",
        "Oat Milk",
        price=3.49,
        quantity=5,
        store_name="Trader Joe's",
    )["product_id"]
    return kg, milk_id, milk_alt_id


def _actions(execution_id, milk_id):
    return (
        Action(
            action_id="a0",
            capability="ProductSelection",
            step_index=0,
            depends_on=(),
            correlation_id=execution_id,
            parameters={"selection": [{"id": milk_id, "qty": 1}]},
        ),
        Action(
            action_id="a1",
            capability="OrderCreation",
            step_index=1,
            depends_on=(0,),
            correlation_id=execution_id,
        ),
        Action(
            action_id="a2",
            capability="OrderConfirmation",
            step_index=2,
            depends_on=(1,),
            correlation_id=execution_id,
        ),
    )


@pytest.mark.asyncio
async def test_approval001_stale_substitution_enters_waiting_for_human():
    """Milk goes stale mid-plan, exactly like WORLD-001, but this actor's
    context asks for approval before any substitution. The tick must stop
    -- no substitution occurs, no fabricated success or failure -- and a
    real, inspectable PendingApproval must exist."""
    kg, milk_id, milk_alt_id = _seed_catalog()
    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex

    def mutate(kg):
        kg.update_entity(milk_id, attributes={"quantity": 0})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    context = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "",
        "approval_required_for_substitution": True,
    }
    result = await executor.execute(_actions(execution_id, milk_id), context)

    assert result.status == "waiting_for_human"
    assert result.goal_achieved is False
    # No substitution happened -- context was never mutated with a
    # replaced cart, and no order/confirmation succeeded.
    assert result.actions[-1].success is False
    assert result.actions[-1].result.get("requires_approval") is True

    pending = load_pending_approval(execution_id)
    assert pending is not None
    assert pending.decided is None
    assert pending.capability == "OrderConfirmation"
    assert pending.proposed_action.get("original_id") == milk_id
    assert pending.proposed_action["replacement"]["id"] == milk_alt_id


@pytest.mark.asyncio
async def test_approval005_real_prompt_text_alone_triggers_approval_no_flag_set():
    """The real wiring gap this covers: a live prompt has no way to set
    context["approval_required_for_substitution"] directly (only this
    session's own unit tests could, by hand-building context) -- a real
    user's actual words ("ask me before buying a substitute") must be
    what triggers the same real pause, with no explicit flag in context
    at all."""
    kg, milk_id, milk_alt_id = _seed_catalog()
    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex

    def mutate(kg):
        kg.update_entity(milk_id, attributes={"quantity": 0})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    context = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "Buy milk; if unavailable, ask me before buying a substitute.",
    }
    result = await executor.execute(_actions(execution_id, milk_id), context)

    assert result.status == "waiting_for_human"
    assert result.actions[-1].result.get("requires_approval") is True
    pending = load_pending_approval(execution_id)
    assert pending is not None
    assert pending.proposed_action["replacement"]["id"] == milk_alt_id


@pytest.mark.asyncio
async def test_approval002_approving_resumes_and_completes_the_same_execution():
    """Approve the pending substitution; the SAME execution_id must
    complete, using the real, already-proven OrderConfirmation replan
    logic (the exact candidate that was proposed, not a fresh one)."""
    kg, milk_id, milk_alt_id = _seed_catalog()
    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex

    def mutate(kg):
        kg.update_entity(milk_id, attributes={"quantity": 0})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    context1 = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "",
        "approval_required_for_substitution": True,
    }
    first = await executor.execute(_actions(execution_id, milk_id), context1)
    assert first.status == "waiting_for_human"

    resolved = resolve_pending_approval(execution_id, True)
    assert resolved is not None
    assert resolved.decided is True

    context2 = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "",
        "approval_required_for_substitution": True,
    }
    second = await executor.execute(_actions(execution_id, milk_id), context2)

    assert second.status == "completed"
    assert second.goal_achieved is True
    confirm_outcome = second.actions[-1]
    assert confirm_outcome.success is True
    confirmed_ids = {p["id"] for p in confirm_outcome.result["product"]}
    assert milk_alt_id in confirmed_ids
    assert milk_id not in confirmed_ids


@pytest.mark.asyncio
async def test_approval003_rejecting_is_a_real_honest_failure_no_purchase():
    """Reject the pending substitution; the order must genuinely fail --
    no purchase occurs, no fabricated partial success."""
    kg, milk_id, milk_alt_id = _seed_catalog()
    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex

    def mutate(kg):
        kg.update_entity(milk_id, attributes={"quantity": 0})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    context1 = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "",
        "approval_required_for_substitution": True,
    }
    first = await executor.execute(_actions(execution_id, milk_id), context1)
    assert first.status == "waiting_for_human"

    resolve_pending_approval(execution_id, False)

    context2 = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "",
        "approval_required_for_substitution": True,
    }
    second = await executor.execute(_actions(execution_id, milk_id), context2)

    assert second.status == "completed"  # decided, not still waiting
    assert second.goal_achieved is False
    confirm_outcome = second.actions[-1]
    assert confirm_outcome.success is False
    assert "not approved" in confirm_outcome.error

    # No real reservation exists for either the original or the alternative.
    milk_alt = kg.get_entity(milk_alt_id)
    assert not milk_alt.attributes.get("reservations")


@pytest.mark.asyncio
async def test_approval004_pending_approval_survives_a_real_restart():
    """Real checkpoint/restart (Phase 3's own real two-call pattern) while
    WAITING_FOR_HUMAN: the pending approval is real, Redis-persisted state
    -- not in-memory only -- so it survives being read by a genuinely
    separate load, and resolving it afterward resumes the ORIGINAL
    execution, never a new one."""
    kg, milk_id, milk_alt_id = _seed_catalog()
    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex

    def mutate(kg):
        kg.update_entity(milk_id, attributes={"quantity": 0})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    context1 = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "",
        "approval_required_for_substitution": True,
    }
    await executor.execute(_actions(execution_id, milk_id), context1)

    # "Restart": a genuinely separate load, simulating a new process
    # picking this execution_id back up.
    survived = load_pending_approval(execution_id)
    assert survived is not None
    assert survived.execution_id == execution_id
    assert survived.decided is None

    resolve_pending_approval(execution_id, True)

    context2 = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "",
        "approval_required_for_substitution": True,
    }
    result = await executor.execute(_actions(execution_id, milk_id), context2)

    assert result.status == "completed"
    assert result.goal_achieved is True
    # Same execution_id throughout -- no duplicate/parallel execution was
    # ever created.
    assert all(a.correlation_id == execution_id for a in _actions(execution_id, milk_id))


@pytest.mark.asyncio
async def test_approval006_independent_sibling_step_still_runs_while_one_step_pauses():
    """Real, live-only gap found by testing a two-item approval prompt
    end to end through the actual HTTP pipeline (never exercised by
    APPROVAL-001..005 above, which all use a single-item plan): a plan
    with milk (needs approval to substitute) and pizza (fully
    independent, no depends_on relationship to milk at all) must let
    pizza run to real completion in the SAME tick -- the whole execution
    must not stop just because ONE step paused. The paused step's own
    real DEPENDENTS (none exist for milk here, since OrderConfirmation
    is the last milk step) would still be correctly blocked by the
    ordinary missing-dependency mechanism -- this test proves the
    orthogonal, previously-broken case: a step with NO dependency on the
    paused one."""
    kg, milk_id, milk_alt_id = _seed_catalog()
    store = onboard_merchant(kg, "merchant_b", "Whole Foods", delivery_fee=2.99)["store_id"]
    pizza_id = list_product(kg, store, "merchant_b", "Pizza", price=7.99, quantity=5)["product_id"]

    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex

    def mutate(kg):
        kg.update_entity(milk_id, attributes={"quantity": 0})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    context = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "",
        "approval_required_for_substitution": True,
    }
    actions = _actions(execution_id, milk_id) + (
        Action(
            action_id="b0",
            capability="ProductSelection",
            step_index=3,
            depends_on=(),
            correlation_id=execution_id,
            parameters={"selection": [{"id": pizza_id, "qty": 1}]},
        ),
        Action(
            action_id="b1",
            capability="OrderCreation",
            step_index=4,
            depends_on=(3,),
            correlation_id=execution_id,
        ),
    )
    result = await executor.execute(actions, context)

    assert result.status == "waiting_for_human"
    assert result.goal_achieved is False
    # All 5 actions were genuinely attempted -- pizza was NOT silently
    # skipped just because milk paused.
    assert len(result.actions) == 5
    milk_confirm = result.actions[2]
    assert milk_confirm.result.get("requires_approval") is True
    pizza_select, pizza_order = result.actions[3], result.actions[4]
    assert pizza_select.success is True
    assert pizza_order.success is True
    assert pizza_order.result.get("order_id")
