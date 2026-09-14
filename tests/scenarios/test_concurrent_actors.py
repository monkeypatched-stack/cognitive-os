"""CONCUR-001/002 — concurrent multi-actor contention qualification tests.

Investigated first whether a "controlled interleaving" test harness (the
original sketch for this phase) was even needed, and found it would have
tested a race condition that can't happen in production: every real tick
goes through PlanetaryRuntime.execute_actor_request (kernel/society/
integration.py), which acquires a single, planet-wide asyncio.Lock
(self._tick_lock) plus a cross-process Redis lock before anything runs —
no two ticks, for any two actors, ever genuinely interleave through that
path.

The real, already-anticipated concurrency risk lives one layer down, in
shared capability code, and is already handled there: kernel/domains/
grocery.py::try_reserve/confirm_reservation are real, production,
optimistic-concurrency (compare-and-swap) primitives, built specifically
for "two actors racing for the last unit" (GS-0600) — defense-in-depth
that must hold even if the tick lock above is ever bypassed, weakened, or
racing another replica across its own distributed-lock boundary.
OrderCreationCapability (the same capability WORLD/FAULT/RECOVERY/LEARN's
tests already use) already calls try_reserve per line item and falls back
to place_backorder — a real, honest, no-oversell branch — on contention.

tests/scenarios/test_mb3015_inventory_reservation.py already qualifies
try_reserve itself under real OS threads. What's still untested is the
capability-CHAIN integration: two different actors' full (ProductSelection
-> OrderCreation) action sequences, run through the real, globally-shared
ActionExecutor (kernel/pipeline/action_executor.py), genuinely racing via
asyncio.gather. That interleaving is real, not fabricated: capability
handle() methods are plain sync functions (no await inside try_reserve's
own loop, so two calls can't interleave MID-try_reserve), but
ActionExecutor.execute()'s own per-action loop already has await points
BETWEEN actions -- exactly where two actors' chains genuinely interleave.

No new production code or test infrastructure was needed for this phase:
the reservation logic was already correct, just never qualification-tested
at this layer.

Scope, stated honestly (mirrors FAULT-002's convention): this exercises
ActionExecutor.execute() directly, bypassing PlanetaryRuntime's own
_tick_lock -- deliberately, since the CAS logic being qualified here is
the layer that must hold even when that lock doesn't apply.
"""

from __future__ import annotations

import asyncio

import pytest

from src.monkey_brain.kernel.domains import (
    grocery,
)  # noqa: F401 -- registers the grocery vertical
from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.vertical_router import build_execution_engine
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
from src.monkey_brain.kernel.pipeline.execution import Action


def _seed_scarce_product(quantity: int) -> tuple[KnowledgeGraph, str]:
    kg = KnowledgeGraph()
    store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    product_id = list_product(kg, store, "merchant_a", "Limited Edition Mug", price=9.99, quantity=quantity)[
        "product_id"
    ]
    return kg, product_id


def _order_actions(product_id: str) -> tuple[Action, ...]:
    return (
        Action(
            action_id="a0",
            capability="ProductSelection",
            step_index=0,
            depends_on=(),
            parameters={"selection": [{"id": product_id, "qty": 1}]},
        ),
        Action(action_id="a1", capability="OrderCreation", step_index=1, depends_on=(0,)),
    )


@pytest.mark.asyncio
async def test_concur001_two_actors_racing_for_the_last_unit():
    """Two different actors both try to buy the SAME single-unit product,
    fired concurrently (asyncio.gather, genuine scheduling, not sequential
    awaits) against the SAME live KnowledgeGraph. Exactly one must get a
    real reservation; the other must get an honest backorder -- never both
    succeeding (oversell) and never a crash or fabricated result.
    """
    kg, product_id = _seed_scarce_product(quantity=1)
    executor = build_execution_engine("grocery")

    context_a = {"knowledge_graph": kg, "actor_id": "concur_actor_a", "question": ""}
    context_b = {"knowledge_graph": kg, "actor_id": "concur_actor_b", "question": ""}

    result_a, result_b = await asyncio.gather(
        executor.execute(_order_actions(product_id), context_a),
        executor.execute(_order_actions(product_id), context_b),
    )

    def backordered_ids(result):
        order_outcome = result.actions[1]
        assert order_outcome.success is True, f"OrderCreation itself must not fail: {order_outcome.error}"
        return {b["product_id"] for b in order_outcome.result.get("backordered", [])}

    a_backordered = product_id in backordered_ids(result_a)
    b_backordered = product_id in backordered_ids(result_b)

    # Exactly one actor got the real reservation, the other was honestly
    # backordered -- never both (oversell) and never neither (a real unit
    # existed and nobody got it).
    assert a_backordered != b_backordered

    # World-state truth, not just each capability's self-reported result:
    # exactly one active reservation exists on the product itself.
    import time

    entity = kg.get_entity(product_id)
    active_reservations = [r for r in entity.attributes.get("reservations", []) if r.get("until", 0) > time.time()]
    assert len(active_reservations) == 1
    assert sum(r.get("qty", 0) for r in active_reservations) == 1


@pytest.mark.asyncio
async def test_concur002_scale_no_oversell_across_many_actors():
    """8 actors race concurrently for 3 units of the same product. Exactly
    3 real reservations must be granted and 5 honest backorders recorded --
    never more than 3 held at once (the oversell case try_reserve's CAS
    loop exists to prevent), mirroring test_mb3015's "many actors, fewer
    units" scale proof at the capability-CHAIN level instead of the raw
    try_reserve primitive.
    """
    kg, product_id = _seed_scarce_product(quantity=3)
    executor = build_execution_engine("grocery")

    actor_count = 8
    contexts = [
        {"knowledge_graph": kg, "actor_id": f"concur_scale_actor_{i}", "question": ""} for i in range(actor_count)
    ]

    results = await asyncio.gather(*(executor.execute(_order_actions(product_id), ctx) for ctx in contexts))

    granted = 0
    backordered = 0
    for result in results:
        order_outcome = result.actions[1]
        assert order_outcome.success is True, f"OrderCreation itself must not fail: {order_outcome.error}"
        product_ids_backordered = {b["product_id"] for b in order_outcome.result.get("backordered", [])}
        if product_id in product_ids_backordered:
            backordered += 1
        else:
            granted += 1

    assert granted == 3
    assert backordered == 5

    import time

    entity = kg.get_entity(product_id)
    active_reservations = [r for r in entity.attributes.get("reservations", []) if r.get("until", 0) > time.time()]
    # Never more than the real 3 units that exist -- the oversell case this
    # whole mechanism exists to prevent.
    assert sum(r.get("qty", 0) for r in active_reservations) == 3
    # OrderCreation only ever HOLDS (confirm_reservation, the real
    # decrement, happens later in PaymentCapability) -- quantity itself is
    # untouched by this test.
    assert entity.attributes.get("quantity") == 3
