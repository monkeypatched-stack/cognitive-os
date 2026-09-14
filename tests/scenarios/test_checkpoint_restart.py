"""RECOVERY-001..003 — execution checkpoint/restart qualification tests.

kernel/pipeline/execution_checkpoint_store.py closes a real gap: a tick is
one fully synchronous stage loop (Phase 1 finding) with no pause/resume
point, so a crash between two steps of a multi-step plan today loses all
progress -- a retried request would re-run every step from scratch,
including ones that already had a real, irreversible side effect (a stock
reservation, a wallet charge).

The fix scope is deliberately narrow (see the Phase 3 plan's "different in
kind" note): the CALLER must explicitly say it's resuming a specific prior
attempt (meta.resume_execution_id, mirroring OrderCreationCapability's own
resume_order_id precedent at the order level) -- this is not automatic
crash detection.

These tests simulate "the process restarted between two calls" the only
honest way without actually killing the interpreter: two separate
ActionExecutor.execute() calls sharing the same execution_id (the real,
only piece of state that survives a restart -- Redis, not the Python
process). The first call represents everything that completed before a
hypothetical crash; the second, everything a resumed request executes.
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
from src.monkey_brain.kernel.pipeline.execution import Action

ACTOR_ID = "recovery_test_actor"


def _seed_grocery(kg):
    store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    milk_id = list_product(kg, store, "merchant_a", "Milk", price=3.49, quantity=40)["product_id"]
    eggs_id = list_product(kg, store, "merchant_a", "Eggs", price=4.79, quantity=40)["product_id"]
    pizza_id = list_product(kg, store, "merchant_a", "Pizza", price=7.99, quantity=40)["product_id"]
    bread_id = list_product(kg, store, "merchant_a", "Bread", price=3.99, quantity=40)["product_id"]
    return milk_id, eggs_id, pizza_id, bread_id


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
async def test_recovery001_completed_step_is_not_re_executed_on_resume():
    """Buy milk and pizza. Execute milk (real success, checkpoint
    written). "Restart" (a new execute() call, same execution_id) with
    the full plan: milk must NOT be re-dispatched to its capability, and
    pizza must execute for the first time."""
    kg = KnowledgeGraph()
    milk_id, _, pizza_id, _ = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex

    milk = _sel("a0", 0, milk_id, execution_id)
    pizza = _sel("a1", 1, pizza_id, execution_id, depends_on=(0,))

    context_attempt1 = {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}
    result1 = await executor.execute((milk,), context_attempt1)
    assert result1.actions[0].success is True

    from src.monkey_brain.kernel.pipeline.execution_checkpoint_store import (
        load_execution_checkpoint,
    )

    checkpoint = load_execution_checkpoint(execution_id)
    assert checkpoint is not None
    assert "0" in checkpoint.completed_steps

    context_attempt2 = {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}
    result2 = await executor.execute((milk, pizza), context_attempt2)

    assert result2.actions[0].success is True
    assert result2.actions[0].metadata.get("resumed_from_checkpoint") is True
    assert result2.actions[1].success is True
    assert not result2.actions[1].metadata.get("resumed_from_checkpoint")
    # Both items' selections reached context, not just the freshly
    # executed one -- the replayed step still ran through the normal
    # context_projector path.
    selected_ids = {p["id"] for p in context_attempt2["selected_product"]}
    assert milk_id in selected_ids
    assert pizza_id in selected_ids


@pytest.mark.asyncio
async def test_recovery002_partial_resume_respects_dependency_order():
    """Milk, eggs, pizza, bread in that order, each depending on the
    previous. First attempt completes milk and eggs; "restart"; the
    resumed attempt must skip milk/eggs and genuinely execute pizza and
    bread, honoring depends_on against the CHECKPOINTED steps' success
    (not just freshly-executed ones)."""
    kg = KnowledgeGraph()
    milk_id, eggs_id, pizza_id, bread_id = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex

    milk = _sel("a0", 0, milk_id, execution_id)
    eggs = _sel("a1", 1, eggs_id, execution_id, depends_on=(0,))
    pizza = _sel("a2", 2, pizza_id, execution_id, depends_on=(1,))
    bread = _sel("a3", 3, bread_id, execution_id, depends_on=(2,))

    result1 = await executor.execute((milk, eggs), {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""})
    assert result1.success_count == 2

    result2 = await executor.execute(
        (milk, eggs, pizza, bread),
        {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""},
    )

    assert [a.metadata.get("resumed_from_checkpoint", False) for a in result2.actions] == [True, True, False, False]
    assert all(a.success for a in result2.actions)
    # Pizza's own dependency (step 1 = eggs) was only ever satisfied via
    # the checkpoint replay, not a fresh execution -- if depends_on gating
    # didn't honor a resumed step's success, pizza would have been
    # incorrectly blocked here.
    assert result2.actions[2].error == ""


@pytest.mark.asyncio
async def test_recovery003_resumed_result_counts_every_step_once():
    """The resumed call's own ExecutionResult must reflect BOTH the
    replayed and the freshly executed steps as real successes -- neither
    double-counted (checkpoint replay + a fresh re-run of the same index)
    nor missing (only the newly executed ones counted)."""
    kg = KnowledgeGraph()
    milk_id, eggs_id, pizza_id, _ = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex

    milk = _sel("a0", 0, milk_id, execution_id)
    eggs = _sel("a1", 1, eggs_id, execution_id, depends_on=(0,))
    pizza = _sel("a2", 2, pizza_id, execution_id, depends_on=(1,))

    await executor.execute((milk,), {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""})
    result2 = await executor.execute(
        (milk, eggs, pizza),
        {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""},
    )

    assert len(result2.actions) == 3
    assert result2.success_count == 3
    assert result2.failure_count == 0
    assert result2.goal_achieved is True


@pytest.mark.asyncio
async def test_recovery004_checkpointed_plan_carries_a_real_goal_and_passes_validation():
    """Qualification Gap Closure, Phase 9: a real, live-only bug found by
    resuming a paused human approval end-to-end through the actual HTTP
    API (never exercised by RECOVERY-001..003 above, which -- like every
    executor-level test in this suite -- call ActionExecutor.execute()
    directly, bypassing belief_runtime.py's own planning stage entirely).

    ActionExecutor._plan_dict_from_actions used to hardcode "goal": "" in
    every checkpoint it saved. On resume, belief_runtime.py::
    _generate_plan reconstructs a real Plan from that stored dict via
    plan_from_dict -- an empty goal there means PlanValidator genuinely
    rejects the resumed plan ("plan_has_no_goal"), regardless of whether
    belief.goal (a separate, unrelated field of the SAME name) was ever
    set correctly for the resume request. Confirmed live before this fix:
    has_goal=True yet the resumed plan was still rejected outright.

    The real question text is available at every checkpoint-save call
    site (context["question"]) -- threading it through is what makes the
    reconstructed plan honest AND validator-passing, not a new
    fabrication."""
    from src.monkey_brain.kernel.pipeline.execution_checkpoint_store import (
        load_execution_checkpoint,
    )
    from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
        plan_from_dict,
    )
    from src.monkey_brain.kernel.pipeline.plan_validator import PlanValidator

    kg = KnowledgeGraph()
    milk_id, _, _, _ = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex
    real_question = "Buy milk; if unavailable, ask me before buying a substitute."

    milk = _sel("a0", 0, milk_id, execution_id)
    await executor.execute(
        (milk,),
        {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": real_question},
    )

    checkpoint = load_execution_checkpoint(execution_id)
    assert checkpoint is not None
    assert checkpoint.plan["goal"] == real_question

    reconstructed = plan_from_dict(checkpoint.plan)
    result = PlanValidator(max_cost=10.0, min_confidence=0.0, max_risk=1.0).validate(reconstructed)
    assert result.valid is True
    assert "plan_has_no_goal" not in result.violations
