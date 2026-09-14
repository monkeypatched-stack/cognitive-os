"""MB-3051 Context Propagation — verify every business event appears in
ContextStream.

Investigation found this claim was false: every context_stream.publish()
call site in the codebase lives in kernel/society/* (geography/actor/
membership events) — none of the ~50 domain functions built across the
MB-30xx arc (grocery.py, commerce.py, logistics.py, finance.py,
supply_chain.py, support.py) ever touched ContextStream. A real request
through PlanetaryRuntime.execute_actor_request() published 11 events,
all generic geographic-traversal/cognitive-cycle events — nothing that
said "order created," "payment completed," "shipment delivered."

Per explicit design choice ("central executor instrumentation"): built
into kernel/pipeline/action_executor.py::ActionExecutor — every capability
call already funnels through one chokepoint (_execute_action()), so
publishing there covers every genuine business event with ZERO changes
to any domain function, and no new dependency from grocery.py/
commerce.py/etc. on the society layer. Purely SIMULATED outcomes (no
real capability_bus wired, or the stochastic-failure-rate path) are
skipped — those never touched real state, so publishing them as a
business event would be dishonest.

Known, documented boundary (not fixed here — a separate, larger gap):
the live LLM-driven request pipeline (execute_actor_request ->
CognitiveRuntime) currently constructs its ActionExecutor with no
capability_bus at all, so every one of ITS actions is "simulated" and
none reach ContextStream yet. This instruments the real chokepoint for
whenever a real bus is wired in (verified directly against
ActionExecutor with a real bus below) — it does not silently expand
scope to also wire a real bus into the live cognitive pipeline.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.domains.grocery import build_default_capability_bus
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph
from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.society.context_stream import (
    ContextEventType,
    SocietyContextStream,
)


def _seed_cancellable_order() -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(
        "wallet_1",
        EntityType.ACCOUNT,
        "Alice Wallet",
        {"account_type": "debit", "balance": 20.0},
    )
    kg.add_entity("prod_1", EntityType.ASSET, "Milk", {"price": 4.5, "quantity": 5})
    kg.add_entity(
        "ORD-1",
        EntityType.EVENT,
        "Grocery Order",
        {
            "status": "confirmed",
            "total": 9.0,
            "items": [{"product_id": "prod_1", "qty": 2}],
            "paid_wallet_id": "wallet_1",
            "paid_amount": 9.0,
            "payment_status": "paid",
        },
    )
    return kg


# ActionExecutor.execute() is `async def` (kernel/pipeline/action_executor.py) --
# every test below must be async and await it. Previously these were plain
# module-level functions calling execute() unawaited, silently asserting on
# a bare coroutine object -- a pre-existing regression, fixed here without
# changing what each test verifies.


@pytest.mark.asyncio
async def test_mb3051_no_context_stream_wired_is_fully_backward_compatible():
    kg = _seed_cancellable_order()
    bus = build_default_capability_bus()
    executor = ActionExecutor(capability_bus=bus)
    action = Action(
        action_id="a1",
        capability="CancelOrder",
        parameters={},
        source_step="CancelOrder",
    )

    result = await executor.execute((action,), {"knowledge_graph": kg, "order_id": "ORD-1", "actor_id": "alice"})

    assert result.success_count == 1


@pytest.mark.asyncio
async def test_mb3051_purely_simulated_outcomes_are_never_published():
    stream = SocietyContextStream()
    executor = ActionExecutor(capability_bus=None, context_stream=stream)
    action = Action(action_id="a2", capability="AnythingAtAll", parameters={}, source_step="x")

    await executor.execute((action,), {"actor_id": "alice"})

    assert stream.event_count == 0


@pytest.mark.asyncio
async def test_mb3051_a_real_successful_action_is_published_as_a_context_event():
    kg = _seed_cancellable_order()
    bus = build_default_capability_bus()
    stream = SocietyContextStream()
    executor = ActionExecutor(capability_bus=bus, context_stream=stream)
    action = Action(
        action_id="a3",
        capability="CancelOrder",
        parameters={},
        source_step="CancelOrder",
    )

    await executor.execute((action,), {"knowledge_graph": kg, "order_id": "ORD-1", "actor_id": "alice"})

    events = stream.events()
    assert len(events) == 1
    assert events[0].event_type is ContextEventType.ACTION
    assert events[0].actor_id == "alice"
    assert "CancelOrder" in events[0].description
    assert events[0].payload["success"] is True
    assert events[0].payload["capability"] == "CancelOrder"


@pytest.mark.asyncio
async def test_mb3051_a_real_failed_action_is_published_honestly():
    kg = KnowledgeGraph()
    bus = build_default_capability_bus()
    stream = SocietyContextStream()
    executor = ActionExecutor(capability_bus=bus, context_stream=stream)
    action = Action(
        action_id="a4",
        capability="CancelOrder",
        parameters={},
        source_step="CancelOrder",
    )

    await executor.execute(
        (action,),
        {"knowledge_graph": kg, "order_id": "does-not-exist", "actor_id": "alice"},
    )

    events = stream.events()
    assert len(events) == 1
    assert events[0].payload["success"] is False


@pytest.mark.asyncio
async def test_mb3051_capability_not_found_is_still_a_real_attempted_action():
    bus = build_default_capability_bus()
    stream = SocietyContextStream()
    executor = ActionExecutor(capability_bus=bus, context_stream=stream)
    action = Action(
        action_id="a5",
        capability="TotallyMadeUpCapability",
        parameters={},
        source_step="x",
    )

    await executor.execute((action,), {"actor_id": "alice"})

    events = stream.events()
    assert len(events) == 1
    assert events[0].payload["success"] is False
