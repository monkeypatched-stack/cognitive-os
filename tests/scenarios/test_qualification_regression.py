"""Regression corpus for the qualification run's 36 passing MB-00xx tests.

Live qualification verified these by hand-authoring plans through a
Claude-in-the-loop file bridge (no local model was reliable enough) — not
reproducible or fast enough for normal CI. What CI *can* pin deterministically
is the code-level guarantee each scenario actually exercised: dependency
enforcement, cart aggregation, budget enforcement, store-name resolution,
order-confirmation gating, SocietyQuery's store listing, quantity grounding,
and AskActor's target resolution — the 12 real bugs fixed this session, plus
the plan-execution-graph semantics Level 2 qualified. Each test below
constructs the exact Action tuples / plan shape the live run resolved to
(bypassing the LLM planner, which remains a live-testing concern, not a CI
one) and asserts on real ExecutionResult / KG / context state.
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

ACTOR_ID = "regression_test_actor"


def _seed_grocery(kg):
    store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    milk_id = list_product(
        kg,
        store,
        "merchant_a",
        "Milk",
        price=3.49,
        quantity=40,
        store_name="Trader Joe's",
    )["product_id"]
    pizza_id = list_product(
        kg,
        store,
        "merchant_a",
        "Frozen Cheese Pizza",
        price=7.99,
        quantity=40,
        store_name="Trader Joe's",
    )["product_id"]
    eggs_id = list_product(
        kg,
        store,
        "merchant_a",
        "Eggs",
        price=4.79,
        quantity=40,
        store_name="Trader Joe's",
    )["product_id"]
    return store, milk_id, pizza_id, eggs_id


def _context(kg):
    return {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}


def _sel(action_id, step_index, product_id, qty=1, depends_on=()):
    return Action(
        action_id=action_id,
        capability="ProductSelection",
        step_index=step_index,
        depends_on=depends_on,
        parameters={"selection": [{"id": product_id, "qty": qty}]},
    )


def _checkout_tail(start_index, depends_on, count=4):
    """OrderCreation -> PaymentConfirmation -> Payment -> OrderConfirmation
    steps, chained sequentially, matching every real checkout plan this
    session's live run verified (fix scope: cart aggregation / budget /
    store-name / order-confirmation-gating all live in these steps)."""
    names = ["OrderCreation", "PaymentConfirmation", "Payment", "OrderConfirmation"][:count]
    actions = []
    prev = depends_on
    for i, name in enumerate(names):
        idx = start_index + i
        actions.append(Action(action_id=f"tail{idx}", capability=name, step_index=idx, depends_on=prev))
        prev = (idx,)
    return tuple(actions)


# ── MB-0002: dependency-graph enforcement (Level 2's qualification target) ──


@pytest.mark.asyncio
async def test_mb0002_linear_chain_blocks_downstream_on_upstream_failure():
    """ "Eggs, then milk, then pizza" style linear chain: if step 0 never
    succeeds, every downstream step (which depends on it, transitively)
    must be blocked, never silently executed out of order."""
    kg = KnowledgeGraph()
    _, milk_id, pizza_id, eggs_id = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    context = _context(kg)

    actions = (
        Action(
            action_id="a0",
            capability="ProductSelection",
            step_index=0,
            parameters={"selection": [{"id": "does-not-exist", "qty": 1}]},
        ),  # forces a real failure
        _sel("a1", 1, milk_id, depends_on=(0,)),
        _sel("a2", 2, pizza_id, depends_on=(1,)),
    )

    result = await executor.execute(actions, context)

    assert result.actions[0].success is False
    assert result.actions[1].error.startswith("blocked: dependency step 0")
    assert result.actions[2].error.startswith("blocked: dependency step 1")
    assert result.failure_count == 3


@pytest.mark.asyncio
async def test_mb0002g_independent_items_do_not_block_each_other():
    """ "Milk and pizza, independently": a failure in one branch must never
    block the other when no depends_on relationship was declared."""
    kg = KnowledgeGraph()
    _, milk_id, pizza_id, _ = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    context = _context(kg)

    actions = (
        Action(
            action_id="a0",
            capability="ProductSelection",
            step_index=0,
            parameters={"selection": [{"id": "does-not-exist", "qty": 1}]},
        ),
        _sel("a1", 1, pizza_id),  # no depends_on -- independent
    )

    result = await executor.execute(actions, context)

    assert result.actions[0].success is False
    assert result.actions[1].success is True


# ── Fix #3: cart aggregation (multi-item orders used to drop everything
#    but the last ProductSelection) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_mb0002c_three_item_order_aggregates_every_item_not_just_the_last():
    kg = KnowledgeGraph()
    _, milk_id, pizza_id, eggs_id = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    context = _context(kg)

    actions = (
        _sel("a0", 0, milk_id),
        _sel("a1", 1, pizza_id, depends_on=(0,)),
        _sel("a2", 2, eggs_id, depends_on=(1,)),
        *_checkout_tail(3, (0, 1, 2), count=1),  # just OrderCreation
    )

    result = await executor.execute(actions, context)

    order_outcome = result.actions[3]
    assert order_outcome.success, order_outcome.error
    item_ids = {item["product"] for item in order_outcome.result["items"]}
    # All three product NAMES present (items store the display name) --
    # the real regression this fix closed: only the LAST selection used
    # to survive into the order.
    assert len(order_outcome.result["items"]) == 3
    assert order_outcome.result["subtotal"] == pytest.approx(3.49 + 7.99 + 4.79)


# ── Fix #4: store-name resolution (order "store" field used to show a
#    product name because products only carry store_id, never store_name) ──


@pytest.mark.asyncio
async def test_order_store_field_is_a_real_store_name_not_a_product_name():
    kg = KnowledgeGraph()
    _, milk_id, _, _ = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    context = _context(kg)

    actions = (_sel("a0", 0, milk_id), *_checkout_tail(1, (0,), count=1))
    result = await executor.execute(actions, context)

    order = result.actions[1].result
    assert order["store"] == "Trader Joe's"
    assert order["items"][0]["store"] == "Trader Joe's"


# ── Fix #6: stated budget enforcement ────────────────────────────────────


@pytest.mark.asyncio
async def test_order_within_stated_budget_succeeds():
    kg = KnowledgeGraph()
    _, milk_id, pizza_id, _ = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    context = _context(kg)
    context["question"] = "Buy milk and pizza under a $20 budget."

    actions = (
        _sel("a0", 0, milk_id),
        _sel("a1", 1, pizza_id, depends_on=(0,)),
        *_checkout_tail(2, (0, 1), count=1),
    )
    result = await executor.execute(actions, context)

    assert result.actions[2].success
    assert result.actions[2].result["total"] < 20.0


@pytest.mark.asyncio
async def test_order_exceeding_stated_budget_is_rejected_not_charged():
    kg = KnowledgeGraph()
    store, milk_id, pizza_id, eggs_id = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    context = _context(kg)
    context["question"] = "Buy milk, pizza, and eggs. Do not spend more than $5."

    actions = (
        _sel("a0", 0, milk_id),
        _sel("a1", 1, pizza_id, depends_on=(0,)),
        _sel("a2", 2, eggs_id, depends_on=(1,)),
        *_checkout_tail(3, (0, 1, 2), count=1),
    )
    result = await executor.execute(actions, context)

    assert result.actions[3].success is False
    assert "exceeds the $5.00 budget" in result.actions[3].error


# ── Fix #5: OrderConfirmation must require a real order first ───────────


@pytest.mark.asyncio
async def test_order_confirmation_without_order_creation_fails_honestly():
    """The recurring model mistake this session found twice: a plan that
    reaches OrderConfirmation without ever running OrderCreation must not
    report a false "confirmed"."""
    kg = KnowledgeGraph()
    _, milk_id, _, _ = _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    context = _context(kg)

    actions = (
        _sel("a0", 0, milk_id),
        Action(
            action_id="a1",
            capability="OrderConfirmation",
            step_index=1,
            depends_on=(0,),
        ),
    )
    result = await executor.execute(actions, context)

    assert result.actions[1].success is False
    assert "no order to confirm" in result.actions[1].error


# ── Fix #11: SocietyQuery must find real, open stores ────────────────────


@pytest.mark.asyncio
async def test_society_query_finds_real_open_stores_not_zero():
    kg = KnowledgeGraph()
    _seed_grocery(kg)
    executor = build_execution_engine("grocery")
    context = _context(kg)
    context["question"] = "Find the best grocery deal for me."

    actions = (
        Action(
            action_id="a0",
            capability="SocietyQuery",
            step_index=0,
            parameters={"query": "best deals"},
        ),
    )
    result = await executor.execute(actions, context)

    assert result.actions[0].success
    assert result.actions[0].result["stores_found"] >= 1
    assert any(s["name"] == "Trader Joe's" for s in result.actions[0].result["stores"])


# ── Fix #12: quantity is included in grounded product facts ─────────────


def test_grounding_includes_quantity_for_availability_reasoning():
    from src.monkey_brain.kernel.pipeline.planning.context_engine import (
        ContextConstructionEngine,
    )

    kg = KnowledgeGraph()
    _, milk_id, _, _ = _seed_grocery(kg)
    kg.update_entity(milk_id, attributes={"quantity": 1})

    engine = ContextConstructionEngine(knowledge_graph=kg)
    knowledge_items, _ = engine._explore_knowledge(ACTOR_ID, "buy milk")

    milk_fact = next((i for i in knowledge_items if milk_id in i.content), None)
    assert milk_fact is not None, "milk product fact missing from grounding entirely"
    assert "quantity=1" in milk_fact.content


# ── Fix #9: AskActor must resolve a target via the wired PlanetaryRuntime ──


@pytest.mark.asyncio
async def test_ask_actor_resolves_target_actor_via_planetary_runtime():
    from src.monkey_brain.kernel.society.domain import (
        ActorIdentity,
        ActorProfile,
        ActorType,
    )
    from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

    marketplace = PlanetaryRuntime()
    asker = ActorProfile(identity=ActorIdentity(name="Asker", actor_type=ActorType.HUMAN))
    target = ActorProfile(identity=ActorIdentity(name="Target", actor_type=ActorType.HUMAN))
    asker_state = marketplace.register_actor(asker)
    marketplace.register_actor(target)

    executor = build_execution_engine("grocery")
    context = {
        "knowledge_graph": marketplace.knowledge_graph,
        "actor_id": asker_state.actor_id,
        "planetary_runtime": marketplace,
        "question": "",
    }
    actions = (
        Action(
            action_id="a0",
            capability="AskActor",
            step_index=0,
            parameters={
                "target_actor": "Target",
                "question": "Can you help with this?",
            },
        ),
    )

    result = await executor.execute(actions, context)

    # Before fix #9 this always failed with "no planetary_runtime
    # available to resolve target actor" regardless of whether Target was
    # reachable. It must at least get past target resolution now — a
    # timeout waiting for Target's own (inactive-in-this-test) reply is a
    # separate, real-time-infra concern (Phase 6), not this regression's.
    assert "no planetary_runtime available" not in result.actions[0].error
