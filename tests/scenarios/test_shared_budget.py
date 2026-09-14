"""BUDGET-001..004 — shared multi-agent budget qualification tests
(Qualification Gap Closure, Phase 5).

kernel/domains/grocery.py::create_shared_budget() creates a real KG
ACCOUNT entity whose "quantity" attribute is a dollar ceiling instead of
a physical unit count — deliberately NOT a new concurrency primitive.
OrderCreationCapability reserves against it via the SAME
try_reserve()/confirm_reservation()/release_reservation() CAS discipline
that already gives inventory its real, proven no-oversell guarantee
under concurrent contention (test_concurrent_actors.py's CONCUR-001/002,
test_mb3015_inventory_reservation.py) — a shared budget entity has no
"owner"/"owners"/"household" attribute, so finance.py::_find_wallet
naturally never mistakes it for a payable wallet.
"""

from __future__ import annotations

import asyncio

import pytest

from src.monkey_brain.kernel.domains import (
    grocery,
)  # noqa: F401 -- registers the grocery vertical
from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.grocery import (
    InventoryReleaseCapability,
    create_shared_budget,
)
from src.monkey_brain.kernel.domains.vertical_router import build_execution_engine
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.testing.fault_injection import (
    clear_forced_failures,
    register_forced_failure,
)


@pytest.fixture(autouse=True)
def _clean_fault_registry():
    clear_forced_failures()
    yield
    clear_forced_failures()


def _seed(price: float, quantity: int = 10):
    kg = KnowledgeGraph()
    store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=0.0)["store_id"]
    product_id = list_product(kg, store, "merchant_a", "Widget", price=price, quantity=quantity)["product_id"]
    return kg, product_id


def _fund_wallet(kg, actor_id: str, balance: float) -> None:
    kg.add_entity(
        f"wallet_{actor_id}",
        EntityType.ACCOUNT,
        f"{actor_id} Wallet",
        {"owner": actor_id, "balance": balance},
    )


def _actions(product_id: str, with_payment: bool = False) -> tuple[Action, ...]:
    steps = [
        Action(
            action_id="a0",
            capability="ProductSelection",
            step_index=0,
            depends_on=(),
            parameters={"selection": [{"id": product_id, "qty": 1}]},
        ),
        Action(action_id="a1", capability="OrderCreation", step_index=1, depends_on=(0,)),
    ]
    if with_payment:
        steps.append(Action(action_id="a2", capability="Payment", step_index=2, depends_on=(1,)))
    return tuple(steps)


@pytest.mark.asyncio
async def test_budget001_two_actors_both_spend_within_a_shared_ceiling():
    """Two real actors drawing against the SAME $20 shared budget, both
    within it -- both must genuinely succeed."""
    kg, product_id = _seed(price=8.00)
    budget_id = create_shared_budget(kg, ceiling=20.00, owner_ids=("actor_a", "actor_b"))
    executor = build_execution_engine("grocery")

    context_a = {
        "knowledge_graph": kg,
        "actor_id": "actor_a",
        "question": "",
        "shared_budget_id": budget_id,
    }
    context_b = {
        "knowledge_graph": kg,
        "actor_id": "actor_b",
        "question": "",
        "shared_budget_id": budget_id,
    }

    result_a = await executor.execute(_actions(product_id), context_a)
    result_b = await executor.execute(_actions(product_id), context_b)

    assert result_a.goal_achieved is True
    assert result_b.goal_achieved is True
    budget_entity = kg.get_entity(budget_id)
    active = [r for r in budget_entity.attributes["reservations"]]
    assert len(active) == 2
    assert round(sum(r["qty"] for r in active), 2) <= 20.00


@pytest.mark.asyncio
async def test_budget002_second_actor_honestly_fails_once_the_budget_is_exhausted():
    """Actor A reserves most of the shared budget; actor B's own,
    separate request must honestly fail -- no fabricated success, no
    silent overspend."""
    kg, product_id = _seed(price=13.50)
    budget_id = create_shared_budget(kg, ceiling=20.00, owner_ids=("actor_a", "actor_b"))
    executor = build_execution_engine("grocery")

    context_a = {
        "knowledge_graph": kg,
        "actor_id": "actor_a",
        "question": "",
        "shared_budget_id": budget_id,
    }
    result_a = await executor.execute(_actions(product_id), context_a)
    assert result_a.goal_achieved is True

    kg2, product_id_b = _seed(price=9.00)
    # Same shared budget entity, a second real product pool (kg2 is a
    # separate KnowledgeGraph instance so product_id_b resolves, but the
    # budget entity itself must live wherever try_reserve looks it up --
    # copy the budget entity's real live state across via the SAME kg
    # actor_a already used, matching how a real shared budget genuinely
    # lives in one shared KnowledgeGraph, not two).
    product_id_b = list_product(
        kg,
        onboard_merchant(kg, "merchant_b", "Whole Foods", delivery_fee=0.0)["store_id"],
        "merchant_b",
        "Gadget",
        price=9.00,
        quantity=10,
    )["product_id"]
    context_b = {
        "knowledge_graph": kg,
        "actor_id": "actor_b",
        "question": "",
        "shared_budget_id": budget_id,
    }
    result_b = await executor.execute(_actions(product_id_b), context_b)

    assert result_b.goal_achieved is False
    assert result_b.actions[1].error.startswith("shared budget:")
    assert "insufficient" in result_b.actions[1].error.lower()


@pytest.mark.asyncio
async def test_budget003_a_declined_payment_releases_the_real_reservation():
    """A reserves $14.58 of a $20 shared budget, then payment genuinely
    fails (PaymentDeclined). The real reactive release
    (InventoryReleaseCapability, generalized this phase to also cover
    budget entities) must restore the full ceiling -- not leave it
    silently locked forever."""
    kg, product_id = _seed(price=13.50)
    budget_id = create_shared_budget(kg, ceiling=20.00, owner_ids=("actor_a",))
    _fund_wallet(kg, "actor_a", balance=1000.0)
    executor = build_execution_engine("grocery")

    register_forced_failure(
        "actor_a",
        trigger=lambda a: a.capability == "Payment",
        error="Simulated processor decline",
    )

    context = {
        "knowledge_graph": kg,
        "actor_id": "actor_a",
        "question": "",
        "shared_budget_id": budget_id,
    }
    result = await executor.execute(_actions(product_id, with_payment=True), context)

    assert result.actions[1].success is True  # OrderCreation reserved successfully
    assert result.actions[2].success is False  # Payment genuinely declined
    before = kg.get_entity(budget_id)
    assert len(before.attributes["reservations"]) == 1
    assert before.attributes["quantity"] == 20.00  # not yet confirmed/decremented

    release_result = InventoryReleaseCapability().handle({"context": context})
    assert release_result["success"] is True
    assert budget_id in release_result["released"]

    after = kg.get_entity(budget_id)
    assert after.attributes["reservations"] == []
    assert after.attributes["quantity"] == 20.00


@pytest.mark.asyncio
async def test_budget004_concurrent_overcommit_never_exceeds_the_real_ceiling():
    """Two real actors, fired concurrently (asyncio.gather, genuine
    scheduling), each attempt a full purchase-and-pay pipeline whose
    combined cost exceeds the shared $20 ceiling. The real CAS resolves
    it: exactly one completes and its spend is permanently confirmed;
    the other honestly fails. Committed spend, verified against the real
    KG entity's own final state, never exceeds the ceiling."""
    kg, product_a = _seed(price=13.00, quantity=10)
    product_b = list_product(
        kg,
        onboard_merchant(kg, "merchant_b", "Whole Foods", delivery_fee=0.0)["store_id"],
        "merchant_b",
        "Gizmo",
        price=13.00,
        quantity=10,
    )["product_id"]
    budget_id = create_shared_budget(kg, ceiling=20.00, owner_ids=("actor_a", "actor_b"))
    _fund_wallet(kg, "actor_a", balance=1000.0)
    _fund_wallet(kg, "actor_b", balance=1000.0)
    executor = build_execution_engine("grocery")

    context_a = {
        "knowledge_graph": kg,
        "actor_id": "actor_a",
        "question": "",
        "shared_budget_id": budget_id,
    }
    context_b = {
        "knowledge_graph": kg,
        "actor_id": "actor_b",
        "question": "",
        "shared_budget_id": budget_id,
    }

    result_a, result_b = await asyncio.gather(
        executor.execute(_actions(product_a, with_payment=True), context_a),
        executor.execute(_actions(product_b, with_payment=True), context_b),
    )

    outcomes = [result_a, result_b]
    succeeded = [r for r in outcomes if r.goal_achieved]
    failed = [r for r in outcomes if not r.goal_achieved]
    assert len(succeeded) == 1
    assert len(failed) == 1
    assert failed[0].actions[1].error.startswith("shared budget:")
    assert "insufficient" in failed[0].actions[1].error.lower()

    final = kg.get_entity(budget_id)
    assert final.attributes["reservations"] == []
    # Exactly one $14.04 spend was permanently confirmed against the $20
    # ceiling -- never both, never a fabricated partial commit.
    assert final.attributes["quantity"] == 5.96


@pytest.mark.asyncio
async def test_budget005_real_prompt_text_creates_and_shares_a_real_budget_across_delegation():
    """Qualification Gap Closure, Phase 9: the real wiring gap this
    covers -- a live prompt ("Agent A buys milk while Agent B buys
    pizza. Shared $20 budget.") has no pre-existing budget entity id to
    reference. ActionExecutor's pre_execute_hook (wired to
    ensure_shared_budget_from_question via build_execution_engine, the
    same injection point context_projector/domain_event_resolver
    already use) must create ONE real shared budget entity from that
    text before any step runs, and DelegateTaskCapability must thread
    its id into the delegated actor's own sub-context -- so Alice's own
    purchase AND Bob's delegated purchase draw against the SAME real
    ceiling, with no explicit shared_budget_id set anywhere by the
    test itself."""
    from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
    from src.monkey_brain.kernel.pipeline.execution import Action
    from src.monkey_brain.kernel.society.domain import (
        ActorIdentity,
        ActorProfile,
        ActorType,
    )
    from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

    pr = PlanetaryRuntime()
    club = pr.create_society("Shared Budget Prompt Test", society_type="community")
    alice = pr.register_actor(
        ActorProfile(identity=ActorIdentity(name="Alice Budget5", actor_type=ActorType.HUMAN)),
        society_id=club.society.society_id,
    )
    pr.register_actor(
        ActorProfile(identity=ActorIdentity(name="Bob Budget5", actor_type=ActorType.HUMAN)),
        society_id=club.society.society_id,
    )

    kg = pr.knowledge_graph
    store = onboard_merchant(kg, "merchant_budget5", "Trader Joe's", delivery_fee=0.0)["store_id"]
    milk_id = list_product(kg, store, "merchant_budget5", "Milk", price=5.00, quantity=10)["product_id"]
    pizza_id = list_product(kg, store, "merchant_budget5", "Pizza", price=8.00, quantity=10)["product_id"]

    executor = build_execution_engine("grocery")
    question = "Agent A buys milk while Agent B buys pizza. Shared $20 budget."
    context = {
        "knowledge_graph": kg,
        "actor_id": alice.actor_id,
        "question": question,
        "planetary_runtime": pr,
    }
    actions = (
        Action(
            action_id="a0",
            capability="ProductSelection",
            step_index=0,
            depends_on=(),
            parameters={"selection": [{"id": milk_id, "qty": 1}]},
        ),
        Action(action_id="a1", capability="OrderCreation", step_index=1, depends_on=(0,)),
        Action(
            action_id="a2",
            capability="DelegateTask",
            step_index=2,
            depends_on=(),
            parameters={
                "target_actor": "Bob Budget5",
                "tasks": [
                    {
                        "capability": "ProductSelection",
                        "parameters": {"selection": [{"id": pizza_id, "qty": 1}]},
                    },
                    {
                        "capability": "OrderCreation",
                        "parameters": {},
                        "depends_on": [0],
                    },
                ],
            },
        ),
    )

    result = await executor.execute(actions, context)

    assert result.goal_achieved is True
    assert "shared_budget_id" in context
    budget_id = context["shared_budget_id"]
    budget_entity = kg.get_entity(budget_id)
    assert budget_entity is not None
    assert budget_entity.attributes["quantity"] == 20.00

    delegate_result = result.actions[2].result
    assert delegate_result["success"] is True

    # Both real reservations landed on the SAME entity, combined well
    # within the real $20 ceiling.
    active = budget_entity.attributes["reservations"]
    assert len(active) == 2
    assert round(sum(r["qty"] for r in active), 2) <= 20.00
