"""TransitionGate — pre-commit negotiation/coordination gate for
shared-state transitions (kernel/society/transition_gate.py).

The bug this closes: OrderCreationCapability/PaymentCapability could go
straight from "actor wants X" to a real, irreversible inventory/budget
decrement with no step in between that asked "does this transition need
another actor's agreement first?" These tests exercise the real, wired
path -- ActionExecutor -> TransitionGate.evaluate() -> capability -- not a
mock of the gate. Every scenario below is deterministic: no LLM, no live
PlanetaryRuntime negotiation dialogue; the pause/resume mechanism (
kernel/pipeline/negotiation_store.py) is exercised directly, the same way
tests/scenarios/test_human_approval.py already exercises the sibling
approval pause/resume mechanism.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from src.monkey_brain.kernel.domains import (
    grocery,
)  # noqa: F401 -- registers the grocery vertical
from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.grocery import create_shared_budget
from src.monkey_brain.kernel.domains.vertical_router import build_execution_engine
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.pipeline.negotiation_store import (
    load_pending_negotiation,
    resolve_pending_negotiation,
)


def _seed(price: float, quantity: int = 10):
    kg = KnowledgeGraph()
    store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=0.0)["store_id"]
    product_id = list_product(kg, store, "merchant_a", "Milk", price=price, quantity=quantity)["product_id"]
    return kg, product_id


def _fund_wallet(kg, actor_id: str, balance: float) -> None:
    kg.add_entity(
        f"wallet_{actor_id}",
        EntityType.ACCOUNT,
        f"{actor_id} Wallet",
        {"owner": actor_id, "balance": balance},
    )


def _actions(product_id: str, execution_id: str = "", with_payment: bool = True) -> tuple[Action, ...]:
    steps = [
        Action(
            action_id="a0",
            capability="ProductSelection",
            step_index=0,
            depends_on=(),
            correlation_id=execution_id,
            parameters={"selection": [{"id": product_id, "qty": 1}]},
        ),
        Action(
            action_id="a1",
            capability="OrderCreation",
            step_index=1,
            depends_on=(0,),
            correlation_id=execution_id,
        ),
    ]
    if with_payment:
        steps.append(
            Action(
                action_id="a2",
                capability="Payment",
                step_index=2,
                depends_on=(1,),
                correlation_id=execution_id,
            )
        )
    return tuple(steps)


# ── Test 1: simple milk purchase — no contention, no unnecessary negotiation ──


@pytest.mark.asyncio
async def test_gate001_simple_milk_purchase_gate_evaluated_no_unnecessary_negotiation():
    """buy 1L milk: the gate is evaluated before OrderCreation AND before
    Payment, decides no negotiation is owed (nobody else has a claim), and
    the purchase completes exactly as it would have before this gate
    existed."""
    kg, product_id = _seed(price=3.99, quantity=10)
    _fund_wallet(kg, "buyer_a", balance=1000.0)
    executor = build_execution_engine("grocery")
    context = {"knowledge_graph": kg, "actor_id": "buyer_a", "question": "buy 1L milk"}

    result = await executor.execute(_actions(product_id), context)

    assert result.goal_achieved is True
    order_creation, payment = result.actions[1], result.actions[2]

    for outcome in (order_creation, payment):
        gate_decision = outcome.result["gate_decision"]
        assert gate_decision["allow"] is True
        assert gate_decision["requires_negotiation"] is False
        assert gate_decision["contention"] is False

    entity = kg.get_entity(product_id)
    assert entity.attributes["quantity"] == 9
    assert entity.attributes["reservations"] == []


# ── Test 2: contended milk — two buyers, last unit ──


@pytest.mark.asyncio
async def test_gate002a_a_live_claim_is_visible_before_the_second_buyer_commits():
    """Deterministic version of the last-unit race: actor A places a real
    hold (OrderCreation only — not yet paid/confirmed, still reversible).
    Actor B's proposal is then evaluated by the SAME gate while A's claim
    is still live. The gate must see it (contention=True, observable,
    referencing A's real reservation) — but must NOT fabricate a
    negotiation pause over a plain capacity race that the existing
    reservation CAS already arbitrates honestly (requires_negotiation
    stays False; B's own real, pre-existing backorder path — MB-3031's
    "an order still completes for whatever actually reserved, the rest is
    backordered" design — is unchanged: no CAS-loss capacity race must be
    turned into a fabricated negotiation). Inventory is never
    double-reserved or negative."""
    kg, product_id = _seed(price=5.00, quantity=1)
    _fund_wallet(kg, "buyer_a", balance=1000.0)
    _fund_wallet(kg, "buyer_b", balance=1000.0)
    executor = build_execution_engine("grocery")

    context_a = {"knowledge_graph": kg, "actor_id": "buyer_a", "question": ""}
    result_a = await executor.execute(_actions(product_id, with_payment=False), context_a)
    assert result_a.goal_achieved is True
    assert result_a.actions[1].result["gate_decision"]["contention"] is False

    context_b = {"knowledge_graph": kg, "actor_id": "buyer_b", "question": ""}
    result_b = await executor.execute(_actions(product_id, with_payment=False), context_b)

    gate_decision_b = result_b.actions[1].result["gate_decision"]
    assert gate_decision_b["contention"] is True
    assert gate_decision_b["requires_negotiation"] is False
    # B's proposal never got the resource -- try_reserve honestly failed
    # for B and OrderCreation's own existing MB-3031 logic backordered the
    # item rather than fabricating a second reservation.
    assert result_b.actions[1].result["backordered"]

    entity = kg.get_entity(product_id)
    assert len(entity.attributes["reservations"]) == 1  # only A's real hold
    assert entity.attributes["reservations"][0]["actor_id"] != "buyer_b"
    assert entity.attributes["quantity"] == 1  # not yet confirmed/decremented


@pytest.mark.asyncio
async def test_gate002b_concurrent_last_unit_never_oversold_or_double_committed():
    """Real concurrency (asyncio.gather), extending the same proven
    pattern as test_shared_budget.py::test_budget004. Whatever the actual
    scheduling interleaving, the gate + the unchanged reservation CAS
    together must never let both buyers commit the one real unit."""
    kg, product_id = _seed(price=5.00, quantity=1)
    _fund_wallet(kg, "buyer_a", balance=1000.0)
    _fund_wallet(kg, "buyer_b", balance=1000.0)
    executor = build_execution_engine("grocery")

    context_a = {"knowledge_graph": kg, "actor_id": "buyer_a", "question": ""}
    context_b = {"knowledge_graph": kg, "actor_id": "buyer_b", "question": ""}

    result_a, result_b = await asyncio.gather(
        executor.execute(_actions(product_id), context_a),
        executor.execute(_actions(product_id), context_b),
    )

    outcomes = [result_a, result_b]
    succeeded = [r for r in outcomes if r.goal_achieved]
    failed = [r for r in outcomes if not r.goal_achieved]
    assert len(succeeded) == 1
    assert len(failed) == 1

    entity = kg.get_entity(product_id)
    assert entity.attributes["quantity"] == 0  # exactly one unit committed, never negative
    assert entity.attributes["reservations"] == []


# ── Test 3: independent resources — no contention, no negotiation ──


@pytest.mark.asyncio
async def test_gate003_independent_resources_both_commit_no_negotiation():
    kg, product_a = _seed(price=5.00, quantity=5)
    product_b = list_product(
        kg,
        onboard_merchant(kg, "merchant_b", "Whole Foods", delivery_fee=0.0)["store_id"],
        "merchant_b",
        "Eggs",
        price=4.00,
        quantity=5,
    )["product_id"]
    _fund_wallet(kg, "buyer_a", 1000.0)
    _fund_wallet(kg, "buyer_b", 1000.0)
    executor = build_execution_engine("grocery")

    result_a = await executor.execute(
        _actions(product_a),
        {"knowledge_graph": kg, "actor_id": "buyer_a", "question": ""},
    )
    result_b = await executor.execute(
        _actions(product_b),
        {"knowledge_graph": kg, "actor_id": "buyer_b", "question": ""},
    )

    assert result_a.goal_achieved is True
    assert result_b.goal_achieved is True
    for result in (result_a, result_b):
        gate_decision = result.actions[1].result["gate_decision"]
        assert gate_decision["requires_negotiation"] is False
        assert gate_decision["contention"] is False


# ── Test 4: shared state, no conflict — co-spending within capacity ──


@pytest.mark.asyncio
async def test_gate004_shared_budget_compatible_spends_no_artificial_negotiation():
    """Regression pin of test_shared_budget.py::test_budget001 against
    the new gate: two actors legitimately co-spending within one shared
    budget is NOT contention (spec's own "shared state, no contention"
    example) — merely being a co-owner of shared state must never by
    itself trigger a negotiation pause."""
    kg, product_id = _seed(price=8.00)
    budget_id = create_shared_budget(kg, ceiling=20.00, owner_ids=("buyer_a", "buyer_b"))
    executor = build_execution_engine("grocery")

    context_a = {
        "knowledge_graph": kg,
        "actor_id": "buyer_a",
        "question": "",
        "shared_budget_id": budget_id,
    }
    context_b = {
        "knowledge_graph": kg,
        "actor_id": "buyer_b",
        "question": "",
        "shared_budget_id": budget_id,
    }

    result_a = await executor.execute(_actions(product_id, with_payment=False), context_a)
    result_b = await executor.execute(_actions(product_id, with_payment=False), context_b)

    assert result_a.goal_achieved is True
    assert result_b.goal_achieved is True
    for result in (result_a, result_b):
        gate_decision = result.actions[1].result["gate_decision"]
        assert gate_decision["requires_negotiation"] is False


# ── Test 5: conflicting constraints — real pause, real resume ──


@pytest.mark.asyncio
async def test_gate005_declared_incompatible_constraint_pauses_then_resolves():
    """Buyer declares max_price=10, the resource declares min_price=12 —
    a genuinely incompatible claim (spec's own worked example). The tick
    must pause BEFORE any reservation exists; capability.handle() is never
    invoked for OrderCreation while the negotiation is pending. Rejecting
    aborts with no mutation at all; a fresh accepted negotiation commits
    exactly once."""
    kg, product_id = _seed(price=13.00, quantity=5)
    kg.update_entity(
        product_id,
        attributes={
            "constraints": {"min_price": 12.0},
            "owner_id": "store_owner_1",
        },
    )
    _fund_wallet(kg, "buyer_a", balance=1000.0)
    executor = build_execution_engine("grocery")

    # -- Reject path: no mutation at all --
    execution_id_reject = uuid.uuid4().hex
    context_reject = {
        "knowledge_graph": kg,
        "actor_id": "buyer_a",
        "question": "",
        "max_price": 10.0,
    }
    first = await executor.execute(_actions(product_id, execution_id_reject, with_payment=False), context_reject)
    assert first.status == "waiting_for_negotiation"
    assert first.goal_achieved is False
    order_creation_outcome = first.actions[1]
    assert order_creation_outcome.result["requires_negotiation"] is True
    assert order_creation_outcome.result["gate_decision"]["contention"] is True

    pending = load_pending_negotiation(execution_id_reject)
    assert pending is not None
    assert pending.decided is None
    assert pending.capability == "OrderCreation"

    entity_before = kg.get_entity(product_id)
    assert entity_before.attributes.get("reservations", []) == []  # never reserved while pending

    resolve_pending_negotiation(execution_id_reject, False)
    context_reject_2 = {
        "knowledge_graph": kg,
        "actor_id": "buyer_a",
        "question": "",
        "max_price": 10.0,
    }
    second = await executor.execute(_actions(product_id, execution_id_reject, with_payment=False), context_reject_2)
    assert second.goal_achieved is False
    assert second.actions[1].result.get("negotiation_rejected") is True
    entity_after_reject = kg.get_entity(product_id)
    assert entity_after_reject.attributes.get("reservations", []) == []
    assert entity_after_reject.attributes["quantity"] == 5  # untouched

    # -- Accept path: commits exactly once, only after agreement --
    execution_id_accept = uuid.uuid4().hex
    context_accept = {
        "knowledge_graph": kg,
        "actor_id": "buyer_a",
        "question": "",
        "max_price": 10.0,
    }
    paused = await executor.execute(_actions(product_id, execution_id_accept, with_payment=False), context_accept)
    assert paused.status == "waiting_for_negotiation"

    resolve_pending_negotiation(execution_id_accept, True)
    context_accept_2 = {
        "knowledge_graph": kg,
        "actor_id": "buyer_a",
        "question": "",
        "max_price": 10.0,
    }
    resumed = await executor.execute(_actions(product_id, execution_id_accept, with_payment=False), context_accept_2)

    assert resumed.status == "completed"
    assert resumed.goal_achieved is True
    entity_final = kg.get_entity(product_id)
    assert len(entity_final.attributes["reservations"]) == 1  # exactly one hold, placed only now


# ── Test 6: stale local belief — no fabricated negotiation ──


@pytest.mark.asyncio
async def test_gate006_stale_belief_does_not_fabricate_negotiation():
    """Actor's plan assumes milk is in stock; real world state says
    quantity 0. No declared constraint, no other actor's active claim —
    the gate must not invent a negotiation for this. The capability's own
    existing honest-failure path is unchanged; no mutation occurs."""
    kg, product_id = _seed(price=3.99, quantity=0)
    _fund_wallet(kg, "buyer_a", balance=1000.0)
    executor = build_execution_engine("grocery")
    context = {"knowledge_graph": kg, "actor_id": "buyer_a", "question": ""}

    result = await executor.execute(_actions(product_id, with_payment=False), context)

    order_creation = result.actions[1]
    gate_decision = order_creation.result.get("gate_decision")
    if gate_decision is not None:
        assert gate_decision["requires_negotiation"] is False
        assert gate_decision["contention"] is False

    entity = kg.get_entity(product_id)
    assert entity.attributes["quantity"] == 0
    assert entity.attributes.get("reservations", []) == []


# ── Test 7: commit ordering ──


@pytest.mark.asyncio
async def test_gate007_commit_ordering_proposal_before_negotiation_before_commit(
    monkeypatch,
):
    """Instruments the real proposal/negotiation-store/reservation call
    sites and asserts: proposal_created < negotiation_started <
    negotiation_completed < state_commit."""
    kg, product_id = _seed(price=13.00, quantity=5)
    kg.update_entity(
        product_id,
        attributes={"constraints": {"min_price": 12.0}, "owner_id": "store_owner_1"},
    )
    _fund_wallet(kg, "buyer_a", balance=1000.0)
    executor = build_execution_engine("grocery")
    execution_id = uuid.uuid4().hex

    real_save = grocery.__dict__  # sentinel, not used directly
    from src.monkey_brain.kernel.pipeline import negotiation_store as neg_store_module

    timestamps: dict[str, float] = {}

    real_save_pending = neg_store_module.save_pending_negotiation

    def spy_save(pending):
        timestamps.setdefault("negotiation_started", time.time())
        time.sleep(0.005)
        return real_save_pending(pending)

    monkeypatch.setattr(neg_store_module, "save_pending_negotiation", spy_save)
    # action_executor.py imports save_pending_negotiation locally inside
    # the function body (`from ...negotiation_store import ... save_pending_negotiation`)
    # -- a fresh import each call, so patching the module attribute above
    # is what the local import actually resolves to.

    real_try_reserve = grocery.try_reserve

    def spy_try_reserve(kg_, entity_id, actor_id, qty, *args, **kwargs):
        timestamps.setdefault("state_commit", time.time())
        return real_try_reserve(kg_, entity_id, actor_id, qty, *args, **kwargs)

    monkeypatch.setattr(grocery, "try_reserve", spy_try_reserve)

    context = {
        "knowledge_graph": kg,
        "actor_id": "buyer_a",
        "question": "",
        "max_price": 10.0,
    }
    paused = await executor.execute(_actions(product_id, execution_id, with_payment=False), context)
    assert paused.status == "waiting_for_negotiation"

    proposal_created = paused.actions[1].result["proposed_transition"]["created_at"]
    assert "negotiation_started" in timestamps
    assert proposal_created <= timestamps["negotiation_started"]

    time.sleep(0.01)
    resolved = resolve_pending_negotiation(execution_id, True)
    negotiation_completed = resolved.decided_at
    assert negotiation_completed > timestamps["negotiation_started"]

    time.sleep(0.01)
    context2 = {
        "knowledge_graph": kg,
        "actor_id": "buyer_a",
        "question": "",
        "max_price": 10.0,
    }
    resumed = await executor.execute(_actions(product_id, execution_id, with_payment=False), context2)
    assert resumed.goal_achieved is True

    assert "state_commit" in timestamps
    state_commit = timestamps["state_commit"]

    assert proposal_created < timestamps["negotiation_started"] < negotiation_completed < state_commit


# ── Test 8: SocialSourcing (Doot audit BYPASS-01 regression) ──
# borrow_item()/buy_from_neighbor() mutate a DIFFERENT actor's owned
# resource (a loan list, or a debit/credit across two actors' wallets).
# Before this fix, SocialSourcingCapability wasn't recognized by
# _propose_transition at all, so it committed with zero gate evaluation
# and zero live counterparty consent -- only a stale, owner-set
# shareable/for_sale flag. These prove the SAME gate/negotiation_store
# path Order/Payment use now also covers both mutation functions: no
# commit before the resource owner accepts, exactly one commit after.


def _seed_shareable_asset(name: str, owner_id: str, quantity: int = 5) -> tuple[KnowledgeGraph, str]:
    kg = KnowledgeGraph()
    entity_id = f"asset_{name.lower()}"
    kg.add_entity(
        entity_id,
        EntityType.ASSET,
        name,
        {
            "owner_id": owner_id,
            "pantry": True,
            "shareable": True,
            "quantity": quantity,
            "loans": [],
        },
    )
    return kg, entity_id


def _social_action(execution_id: str) -> Action:
    return Action(
        action_id="a0",
        capability="SocialSourcing",
        step_index=0,
        depends_on=(),
        correlation_id=execution_id,
        parameters={},
    )


@pytest.mark.asyncio
async def test_gate008a_social_borrow_pauses_for_owner_consent_then_resolves():
    kg, item_id = _seed_shareable_asset("Milk", owner_id="neighbor_b")
    executor = build_execution_engine("grocery")

    # -- Reject path: no mutation at all --
    execution_id_reject = uuid.uuid4().hex
    context = {"knowledge_graph": kg, "actor_id": "buyer_a", "question": "milk"}
    first = await executor.execute((_social_action(execution_id_reject),), context)
    assert first.status == "waiting_for_negotiation"
    outcome = first.actions[0]
    assert outcome.result["requires_negotiation"] is True
    assert "neighbor_b" in outcome.result["counterparties"]

    pending = load_pending_negotiation(execution_id_reject)
    assert pending is not None
    assert pending.decided is None
    assert pending.capability == "SocialSourcing"
    assert kg.get_entity(item_id).attributes["loans"] == []  # never borrowed while pending

    resolve_pending_negotiation(execution_id_reject, False)
    context2 = {"knowledge_graph": kg, "actor_id": "buyer_a", "question": "milk"}
    second = await executor.execute((_social_action(execution_id_reject),), context2)
    assert second.actions[0].result.get("negotiation_rejected") is True
    assert kg.get_entity(item_id).attributes["loans"] == []  # still untouched

    # -- Accept path: commits exactly once, only after the owner agrees --
    execution_id_accept = uuid.uuid4().hex
    context3 = {"knowledge_graph": kg, "actor_id": "buyer_a", "question": "milk"}
    paused = await executor.execute((_social_action(execution_id_accept),), context3)
    assert paused.status == "waiting_for_negotiation"
    assert kg.get_entity(item_id).attributes["loans"] == []

    resolve_pending_negotiation(execution_id_accept, True)
    context4 = {"knowledge_graph": kg, "actor_id": "buyer_a", "question": "milk"}
    resumed = await executor.execute((_social_action(execution_id_accept),), context4)
    assert resumed.actions[0].success is True
    loans = kg.get_entity(item_id).attributes["loans"]
    assert len(loans) == 1
    assert loans[0]["borrower_id"] == "buyer_a"


@pytest.mark.asyncio
async def test_gate008b_social_purchase_pauses_for_seller_consent_then_resolves():
    kg = KnowledgeGraph()
    kg.add_entity(
        "asset_bread",
        EntityType.ASSET,
        "Bread",
        {
            "owner_id": "neighbor_c",
            "pantry": True,
            "for_sale": True,
            "price": 3.0,
            "min_price": 2.0,
            "quantity": 5,
        },
    )
    _fund_wallet(kg, "buyer_a", balance=1000.0)
    _fund_wallet(kg, "neighbor_c", balance=0.0)
    executor = build_execution_engine("grocery")

    execution_id = uuid.uuid4().hex
    context = {"knowledge_graph": kg, "actor_id": "buyer_a", "question": "bread"}
    paused = await executor.execute((_social_action(execution_id),), context)
    assert paused.status == "waiting_for_negotiation"
    assert "neighbor_c" in paused.actions[0].result["counterparties"]
    assert kg.get_entity("asset_bread").attributes["quantity"] == 5  # untouched
    buyer_wallet_before = next(
        e for e in kg.entities if e.entity_type == EntityType.ACCOUNT and e.attributes.get("owner") == "buyer_a"
    )
    assert buyer_wallet_before.attributes["balance"] == 1000.0

    resolve_pending_negotiation(execution_id, True)
    context2 = {"knowledge_graph": kg, "actor_id": "buyer_a", "question": "bread"}
    resumed = await executor.execute((_social_action(execution_id),), context2)
    assert resumed.actions[0].success is True
    assert kg.get_entity("asset_bread").attributes["quantity"] == 4
    buyer_wallet_after = next(
        e for e in kg.entities if e.entity_type == EntityType.ACCOUNT and e.attributes.get("owner") == "buyer_a"
    )
    assert buyer_wallet_after.attributes["balance"] < 1000.0  # real money moved, only after consent
