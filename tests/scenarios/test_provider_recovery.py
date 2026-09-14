"""RECOVERY-001..004 — same-tick cross-provider recovery qualification
tests (Qualification Gap Closure, Phase 4).

Generic, opt-in contract shared with Phase 3's approval mechanism: a
failed ActionOutcome whose own result carries {"recoverable": True} gets
exactly ONE re-attempt inside ActionExecutor.execute()'s existing
per-action loop (kernel/pipeline/action_executor.py) — re-grounded via a
real KnowledgeGraph.refresh() and a retry-flagged copy of the SAME
action (kernel/pipeline/action_executor.py::_build_recovery_action). No
capability name is hardcoded in the executor; ProductSelectionCapability
(kernel/domains/grocery.py) is the first real capability to recognize the
retry_after_failure/excluded_ids convention, reusing the same
open_products() re-query every other real search path in this module
already uses. fault_injection.py's register_forced_failure gained an
opt-in `recoverable` flag (default False) so FAULT-001/002's existing,
unmodified honest-failure assertions are completely unaffected.
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

ACTOR_ID = "recovery_test_actor"


@pytest.fixture(autouse=True)
def _clean_fault_registry():
    clear_forced_failures()
    yield
    clear_forced_failures()


def _sel(action_id, step_index, product_id, depends_on=()):
    return Action(
        action_id=action_id,
        capability="ProductSelection",
        step_index=step_index,
        depends_on=depends_on,
        parameters={"selection": [{"id": product_id, "qty": 1}]},
    )


@pytest.mark.asyncio
async def test_recovery001_forced_failure_recovers_to_a_real_alternative_same_execution():
    """A forced, recoverable failure on the ONLY provider the planner
    picked must not end the tick -- a real second, valid product exists,
    and the SAME execution recovers to it and completes."""
    kg = KnowledgeGraph()
    store_a = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    store_b = onboard_merchant(kg, "merchant_b", "Whole Foods", delivery_fee=2.49)["store_id"]
    milk_a = list_product(kg, store_a, "merchant_a", "Milk", price=3.49, quantity=10)["product_id"]
    milk_b = list_product(kg, store_b, "merchant_b", "Milk", price=3.99, quantity=10)["product_id"]

    executor = build_execution_engine("grocery")
    context = {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}

    register_forced_failure(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_a for s in a.parameters.get("selection", []))
        ),
        error="Simulated provider outage for Trader Joe's",
        recoverable=True,
    )

    actions = (
        _sel("a0", 0, milk_a),
        Action(action_id="a1", capability="OrderCreation", step_index=1, depends_on=(0,)),
    )
    result = await executor.execute(actions, context)

    assert result.goal_achieved is True
    assert result.actions[0].success is True
    assert result.actions[0].result.get("recovered") is True
    recovered_ids = {p["id"] for p in result.actions[0].result["selected"]}
    assert recovered_ids == {milk_b}
    assert result.actions[1].success is True


@pytest.mark.asyncio
async def test_recovery002_only_the_affected_step_retries():
    """Two-item plan; only milk's provider fails. Eggs must execute once,
    normally, untouched by the recovery machinery."""
    kg = KnowledgeGraph()
    store_a = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    store_b = onboard_merchant(kg, "merchant_b", "Whole Foods", delivery_fee=2.49)["store_id"]
    milk_a = list_product(kg, store_a, "merchant_a", "Milk", price=3.49, quantity=10)["product_id"]
    milk_b = list_product(kg, store_b, "merchant_b", "Milk", price=3.99, quantity=10)["product_id"]
    eggs_id = list_product(kg, store_a, "merchant_a", "Eggs", price=4.79, quantity=10)["product_id"]

    executor = build_execution_engine("grocery")
    context = {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}

    register_forced_failure(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_a for s in a.parameters.get("selection", []))
        ),
        error="Simulated provider outage for Trader Joe's",
        recoverable=True,
    )

    actions = (
        _sel("a0", 0, milk_a),
        _sel("a1", 1, eggs_id),
        Action(action_id="a2", capability="OrderCreation", step_index=2, depends_on=(0, 1)),
    )
    result = await executor.execute(actions, context)

    assert result.goal_achieved is True
    assert result.actions[0].result.get("recovered") is True
    assert {p["id"] for p in result.actions[0].result["selected"]} == {milk_b}
    # The eggs step ran exactly once, normally -- no recovery marker.
    assert result.actions[1].success is True
    assert result.actions[1].result.get("recovered") is None
    assert result.actions[1].result["selected"][0]["id"] == eggs_id


@pytest.mark.asyncio
async def test_recovery003_dependency_ordering_respected_around_a_recovered_step():
    """Milk's first attempt fails and recovers; pizza (which depends on
    milk succeeding) must still execute afterward, in order -- recovery
    does not break dependency gating."""
    kg = KnowledgeGraph()
    store_a = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    store_b = onboard_merchant(kg, "merchant_b", "Whole Foods", delivery_fee=2.49)["store_id"]
    milk_a = list_product(kg, store_a, "merchant_a", "Milk", price=3.49, quantity=10)["product_id"]
    milk_b = list_product(kg, store_b, "merchant_b", "Milk", price=3.99, quantity=10)["product_id"]
    pizza_id = list_product(kg, store_a, "merchant_a", "Pizza", price=8.99, quantity=10)["product_id"]

    executor = build_execution_engine("grocery")
    context = {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}

    register_forced_failure(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_a for s in a.parameters.get("selection", []))
        ),
        error="Simulated provider outage for Trader Joe's",
        recoverable=True,
    )

    actions = (
        _sel("a0", 0, milk_a),
        _sel("a1", 1, pizza_id, depends_on=(0,)),
        Action(action_id="a2", capability="OrderCreation", step_index=2, depends_on=(0, 1)),
    )
    result = await executor.execute(actions, context)

    assert result.goal_achieved is True
    assert result.actions[0].result.get("recovered") is True
    assert result.actions[1].success is True
    assert result.actions[1].result.get("blocked_by_dependency") is None
    assert result.actions[1].result["selected"][0]["id"] == pizza_id
    assert result.actions[2].success is True


@pytest.mark.asyncio
async def test_recovery004_no_real_alternative_is_an_honest_failure_no_infinite_retry():
    """Every real alternative is exhausted (only one provider exists at
    all) -- the retry must genuinely fail, honestly, exactly once, never
    fabricating success and never looping."""
    kg = KnowledgeGraph()
    store_a = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    milk_a = list_product(kg, store_a, "merchant_a", "Milk", price=3.49, quantity=10)["product_id"]

    executor = build_execution_engine("grocery")
    context = {"knowledge_graph": kg, "actor_id": ACTOR_ID, "question": ""}

    register_forced_failure(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_a for s in a.parameters.get("selection", []))
        ),
        error="Simulated provider outage for Trader Joe's",
        recoverable=True,
    )

    actions = (
        _sel("a0", 0, milk_a),
        Action(action_id="a1", capability="OrderCreation", step_index=1, depends_on=(0,)),
    )
    result = await executor.execute(actions, context)

    assert result.goal_achieved is False
    assert result.actions[0].success is False
    assert "no real alternative" in result.actions[0].result.get("error", "")
    assert result.actions[0].result.get("recoverable") is False
    assert result.actions[1].success is False
    assert result.actions[1].result.get("blocked_by_dependency") == 0
