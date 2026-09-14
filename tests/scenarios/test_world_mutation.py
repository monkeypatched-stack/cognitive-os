"""WORLD-001..005 — mid-plan world-mutation qualification tests.

These exercise a real gap identified in a live qualification run: the
cognitive tick is fully synchronous (see kernel/pipeline/cognitive_policy.py
::run_stages) with no pause/resume hook, so nothing could previously test
"the world changed between two steps of the SAME plan, did the system
detect the staleness and replan rather than execute blindly."

kernel/testing/mutation_hooks.py closes that gap generically (a test
registers a (predicate, mutation) pair for an actor_id; ActionExecutor
checks it after every real action and applies the mutation against the
SAME live KnowledgeGraph if it matches) — no grocery-specific logic lives
in the hook itself. The actual staleness-detection/replanning behavior
these tests verify is the REAL, pre-existing OrderConfirmationCapability
logic (kernel/domains/grocery.py) — a mid-plan `kg.refresh()` + fresh
re-validation against the live catalog, not anything new added for these
tests.

Bypasses the LLM planner entirely by constructing Action tuples directly
and calling ActionExecutor.execute() (== CapabilityRuntime, see
kernel/pipeline/capability_runtime.py) — deterministic, no model
dependency, same in-process construction style as
test_mb3060_end_to_end_cognitive_os_prompt.py.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.domains import (
    grocery,
)  # noqa: F401 -- registers the grocery vertical
from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.grocery import PaymentConfirmationCapability
from src.monkey_brain.kernel.domains.vertical_router import build_execution_engine
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.testing.mutation_hooks import (
    clear_mutations,
    register_mutation,
)

ACTOR_ID = "world_test_actor"


@pytest.fixture(autouse=True)
def _clean_mutation_registry():
    clear_mutations()
    yield
    clear_mutations()


def _seed_catalog(*, second_milk_option: bool = False, second_store: bool = False):
    """A real KG: one store selling milk + pizza, a wallet, optionally a
    second milk option (same store) or a second store selling milk (for
    replan-to-alternative scenarios).

    Product naming matters here, not just as flavor text: the requested
    item is named plain "Milk" (a GENERIC request — no substitution-policy
    type keyword in it, see _SUBSTITUTION_POLICIES/_base_item in
    grocery.py) so OrderConfirmationCapability's own real replan logic
    walks its real fallback order (["reduced", "whole", "oat", "almond"])
    looking for a product whose name contains one of those literal
    keywords — this is real, pre-existing substitution-policy behavior,
    not something invented for this test. An alternative product must be
    named accordingly ("Oat Milk" / "Whole Milk") to actually be
    discoverable by that policy, exactly as a real merchant's catalog
    would need to be for the feature to work at all.
    """
    kg = KnowledgeGraph()
    store_a = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    milk_id = list_product(
        kg,
        store_a,
        "merchant_a",
        "Milk",
        price=3.99,
        quantity=5,
        store_name="Trader Joe's",
    )["product_id"]
    pizza_id = list_product(
        kg,
        store_a,
        "merchant_a",
        "Frozen Cheese Pizza",
        price=7.99,
        quantity=10,
        store_name="Trader Joe's",
    )["product_id"]

    milk_alt_id = None
    if second_milk_option:
        milk_alt_id = list_product(
            kg,
            store_a,
            "merchant_a",
            "Oat Milk",
            price=3.49,
            quantity=5,
            store_name="Trader Joe's",
        )["product_id"]

    store_b = None
    milk_store_b_id = None
    if second_store:
        store_b = onboard_merchant(kg, "merchant_b", "Whole Foods", delivery_fee=2.99)["store_id"]
        milk_store_b_id = list_product(
            kg,
            store_b,
            "merchant_b",
            "Whole Milk",
            price=4.29,
            quantity=5,
            store_name="Whole Foods",
        )["product_id"]

    kg.add_entity(
        "wallet_world_test",
        EntityType.ACCOUNT,
        "World Test Wallet",
        {
            "account_type": "debit",
            "balance": 200.0,
            "owner": ACTOR_ID,
        },
    )
    kg.add_entity(
        "proc_stripe",
        EntityType.ORGANIZATION,
        "Stripe",
        {"type": "payment_processor", "priority": 0},
    )

    return kg, store_a, milk_id, pizza_id, milk_alt_id, store_b, milk_store_b_id


def _base_context(kg):
    return {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "Buy milk and pizza.",
    }


def _pay_and_confirm(start_index: int, depends_on):
    """Canonical checkout tail: PaymentConfirmation → Payment → OrderConfirmation."""
    return (
        Action(
            action_id=f"a{start_index}",
            capability="PaymentConfirmation",
            step_index=start_index,
            depends_on=depends_on,
        ),
        Action(
            action_id=f"a{start_index + 1}",
            capability="Payment",
            step_index=start_index + 1,
            depends_on=(start_index,),
        ),
        Action(
            action_id=f"a{start_index + 2}",
            capability="OrderConfirmation",
            step_index=start_index + 2,
            depends_on=(start_index + 1,),
        ),
    )


def _selection_action(action_id: str, step_index: int, product_id: str, depends_on=()):
    return Action(
        action_id=action_id,
        capability="ProductSelection",
        step_index=step_index,
        depends_on=depends_on,
        parameters={"selection": [{"id": product_id, "qty": 1}]},
    )


@pytest.mark.asyncio
async def test_world001_unavailable_milk_triggers_replan_to_alternative():
    """After milk's ProductSelection completes, milk goes out of stock.
    OrderConfirmation must detect this (kg.refresh() + fresh re-check) and
    replan to the real alternative already in the catalog, not blindly
    confirm the stale pick."""
    kg, store_a, milk_id, pizza_id, milk_alt_id, _, _ = _seed_catalog(second_milk_option=True)
    context = _base_context(kg)
    executor = build_execution_engine("grocery")

    def mutate(kg):
        kg.update_entity(milk_id, attributes={"quantity": 0})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    actions = (
        _selection_action("a0", 0, milk_id),
        _selection_action("a1", 1, pizza_id, depends_on=(0,)),
        Action(action_id="a2", capability="OrderCreation", step_index=2, depends_on=(0, 1)),
        *_pay_and_confirm(3, (2,)),
    )

    result = await executor.execute(actions, context)

    # The mutation genuinely changed the world.
    assert kg.get_entity(milk_id).attributes["quantity"] == 0
    confirm_outcome = result.actions[5]
    assert confirm_outcome.success, f"OrderConfirmation should replan, not fail: {confirm_outcome.error}"
    confirmed_ids = {p["id"] for p in confirm_outcome.result["product"]}
    # Stale milk id must NOT be in the final confirmed cart...
    assert milk_id not in confirmed_ids
    # ...replaced by the real alternative that actually exists.
    assert milk_alt_id in confirmed_ids
    # Pizza (never went stale) must still be present, untouched.
    assert pizza_id in confirmed_ids


@pytest.mark.asyncio
async def test_world002_price_change_reprices_same_item_not_a_stockout():
    """A price change alone (still in stock, same store) must reprice the
    SAME item, not treat it like unavailability."""
    kg, store_a, milk_id, pizza_id, _, _, _ = _seed_catalog()
    context = _base_context(kg)
    executor = build_execution_engine("grocery")

    def mutate(kg):
        kg.update_entity(milk_id, attributes={"price": 5.99})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    actions = (
        _selection_action("a0", 0, milk_id),
        _selection_action("a1", 1, pizza_id, depends_on=(0,)),
        Action(action_id="a2", capability="OrderCreation", step_index=2, depends_on=(0, 1)),
        *_pay_and_confirm(3, (2,)),
    )

    result = await executor.execute(actions, context)

    assert kg.get_entity(milk_id).attributes["price"] == 5.99
    confirm_outcome = result.actions[5]
    assert confirm_outcome.success
    confirmed = {p["id"]: p for p in confirm_outcome.result["product"]}
    # Same item id (repriced in place), not swapped for a different product.
    assert milk_id in confirmed
    assert confirmed[milk_id]["price"] == 5.99


@pytest.mark.asyncio
async def test_world003_store_closed_excludes_it_and_replans_to_another_store():
    """Buy milk from the best available provider; that provider (store)
    becomes unavailable mid-plan. open_products()'s is_open filter must
    exclude it from the live catalog, and OrderConfirmation must replan to
    the real alternative at the other store rather than confirm a purchase
    from a closed store. Single-item (no pizza) so closing store_a can't
    incidentally strand an unrelated item too — matches the scenario's own
    "buy milk" prompt."""
    kg, store_a, milk_id, _pizza_id, _, store_b, milk_store_b_id = _seed_catalog(second_store=True)
    context = _base_context(kg)
    executor = build_execution_engine("grocery")

    def mutate(kg):
        kg.update_entity(store_a, attributes={"is_open": False})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    actions = (
        _selection_action("a0", 0, milk_id),
        Action(action_id="a1", capability="OrderCreation", step_index=1, depends_on=(0,)),
        *_pay_and_confirm(2, (1,)),
    )

    result = await executor.execute(actions, context)

    assert kg.get_entity(store_a).attributes["is_open"] is False
    confirm_outcome = result.actions[4]
    assert confirm_outcome.success, f"expected a replan to the other store, got: {confirm_outcome.error}"
    confirmed_ids = {p["id"] for p in confirm_outcome.result["product"]}
    assert milk_id not in confirmed_ids
    assert milk_store_b_id in confirmed_ids


@pytest.mark.asyncio
async def test_world004_last_available_removed_with_no_alternative_fails_closed():
    """The ONLY option for an item goes out of stock and no real
    alternative exists anywhere in the catalog. This must fail closed
    (an honest error), never silently confirm a phantom purchase of
    something that no longer exists."""
    kg, store_a, milk_id, pizza_id, _, _, _ = _seed_catalog()  # no alternative milk seeded
    context = _base_context(kg)
    executor = build_execution_engine("grocery")

    def mutate(kg):
        kg.update_entity(milk_id, attributes={"quantity": 0})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    actions = (
        _selection_action("a0", 0, milk_id),
        Action(action_id="a1", capability="OrderCreation", step_index=1, depends_on=(0,)),
        *_pay_and_confirm(2, (1,)),
    )

    result = await executor.execute(actions, context)

    assert kg.get_entity(milk_id).attributes["quantity"] == 0
    failed = [o for o in result.actions if not o.success]
    assert failed, "checkout must fail closed when the only option is gone"
    assert any(
        "no alternative" in (o.error or "")
        or "backorder" in (o.error or "").lower()
        or "blocked" in (o.error or "").lower()
        for o in failed
    )


@pytest.mark.asyncio
async def test_world005_stale_item_replanned_without_repeating_or_dropping_the_other_item():
    """Buy milk and pizza; milk goes stale mid-plan. The final confirmed
    cart must contain a replanned milk AND the untouched pizza — nothing
    duplicated, nothing silently dropped."""
    kg, store_a, milk_id, pizza_id, milk_alt_id, _, _ = _seed_catalog(second_milk_option=True)
    context = _base_context(kg)
    executor = build_execution_engine("grocery")

    def mutate(kg):
        kg.update_entity(milk_id, attributes={"quantity": 0})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    actions = (
        _selection_action("a0", 0, milk_id),
        _selection_action("a1", 1, pizza_id, depends_on=(0,)),
        Action(action_id="a2", capability="OrderCreation", step_index=2, depends_on=(0, 1)),
        *_pay_and_confirm(3, (2,)),
    )

    result = await executor.execute(actions, context)

    confirm_outcome = result.actions[5]
    assert confirm_outcome.success
    confirmed = confirm_outcome.result["product"]
    confirmed_ids = [p["id"] for p in confirmed]
    # Exactly two items confirmed — no duplication.
    assert len(confirmed_ids) == 2
    assert len(set(confirmed_ids)) == 2
    assert pizza_id in confirmed_ids
    assert milk_alt_id in confirmed_ids
    assert milk_id not in confirmed_ids


WALLET_ID = "wallet_world_test"


def _seed_payable_world(*, milk_qty: int, milk_alt_qty: int = 5) -> tuple[KnowledgeGraph, str, str]:
    """Trader Joe's selling low-stock Milk + Oat Milk (the same real
    substitution-policy alternative test_world001 etc. already rely on),
    plus a real wallet and payment processor — enough for the FULL
    canonical order (OrderCreation -> PaymentConfirmation -> Payment ->
    OrderConfirmation) to actually run to completion, not just OrderCreation
    in isolation like the tests above."""
    kg = KnowledgeGraph()
    store_a = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    milk_id = list_product(
        kg,
        store_a,
        "merchant_a",
        "Milk",
        price=3.99,
        quantity=milk_qty,
        store_name="Trader Joe's",
    )["product_id"]
    list_product(
        kg,
        store_a,
        "merchant_a",
        "Oat Milk",
        price=3.49,
        quantity=milk_alt_qty,
        store_name="Trader Joe's",
    )["product_id"]
    kg.add_entity(
        WALLET_ID,
        EntityType.ACCOUNT,
        "World Test Wallet",
        {
            "account_type": "debit",
            "balance": 100.0,
            "owner": ACTOR_ID,
        },
    )
    kg.add_entity(
        "proc_stripe",
        EntityType.ORGANIZATION,
        "Stripe",
        {"type": "payment_processor", "priority": 0},
    )
    return kg, store_a, milk_id


@pytest.mark.asyncio
async def test_world006_requested_quantity_exceeding_stock_self_heals_within_the_same_tick_before_payment():
    """No register_mutation here — this is a genuinely, realistically live
    trigger, not a simulated mid-tick world change: ProductSelection only
    checks quantity > 0 (grocery.py's own documented gap, "only an explicit
    non-positive quantity is rejected"), never quantity-vs-requested-qty,
    so a plain "buy 3 gallons of milk" when only 1 is actually on the
    shelf reaches OrderCreation's try_reserve() and genuinely fails there
    — in the SAME tick, no external actor or injected mutation involved.

    Regression for the payment-integrity bug found live this session:
    OrderCreation used to report success with a total that still priced
    the unavailable qty, and PaymentConfirmation/Payment blindly charged
    it — the real substitution in OrderConfirmation only ran afterward,
    too late to prevent the wrong charge. This proves the fix: OrderCreation
    itself finds and reserves a real substitute BEFORE PaymentConfirmation/
    Payment ever run, so the actor is charged for what they actually got.
    """
    kg, store_a, milk_id = _seed_payable_world(milk_qty=1)
    context = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "Buy 3 gallons of milk.",
    }
    executor = build_execution_engine("grocery")

    actions = (
        Action(
            action_id="a0",
            capability="ProductSelection",
            step_index=0,
            parameters={"selection": [{"id": milk_id, "qty": 3}]},
        ),
        Action(action_id="a1", capability="OrderCreation", step_index=1, depends_on=(0,)),
        Action(
            action_id="a2",
            capability="PaymentConfirmation",
            step_index=2,
            depends_on=(1,),
        ),
        Action(action_id="a3", capability="Payment", step_index=3, depends_on=(2,)),
        Action(
            action_id="a4",
            capability="OrderConfirmation",
            step_index=4,
            depends_on=(3,),
        ),
    )

    result = await executor.execute(actions, context)

    creation = result.actions[1]
    assert creation.success, f"OrderCreation should self-heal, not fail: {creation.error}"
    assert creation.result["backordered"] == [], "the shortfall must be substituted, not silently backordered"
    assert "replanned" in creation.result

    confirmation = result.actions[2]
    assert confirmation.success, (
        f"PaymentConfirmation must proceed once the substitution actually fixed the cart: {confirmation.error}"
    )

    payment = result.actions[3]
    assert payment.success, f"Payment should succeed against the corrected order: {payment.error}"

    order = kg.get_entity(creation.result["order_id"])
    # The persisted order must reflect the SUBSTITUTE, not the
    # unavailable original — this is what makes the charge correct.
    assert all(item["product_id"] != milk_id for item in order.attributes["items"])
    # Payment must have charged exactly the recomputed (substitute) total,
    # never the original milk-priced total.
    assert payment.result["amount"] == order.attributes["total"]

    wallet = kg.get_entity(WALLET_ID)
    assert wallet.attributes["balance"] == round(100.0 - order.attributes["total"], 2), (
        "the wallet must be charged the REAL (substitute) total, not a total computed "
        "against milk that was never actually reserved"
    )

    confirm_outcome = result.actions[4]
    assert confirm_outcome.success, f"OrderConfirmation should find nothing further stale: {confirm_outcome.error}"


def test_world007_payment_confirmation_refuses_when_a_real_backorder_remains():
    """The residual safety net: when no live substitute exists at all (the
    genuinely-nothing-else-available case test_world004 already covers for
    OrderConfirmation), OrderCreation's own backordered list is the only
    honest signal PaymentConfirmation has that the order's total still
    includes an item that was never actually reserved. It must fail
    closed rather than confirm a charge for stock that isn't there."""
    order = {
        "success": True,
        "order_id": "ORD-backordered-1",
        "total": 11.97,
        "items": [{"product": "Milk", "qty": 3, "unit_price": 3.99, "store": "Trader Joe's"}],
        "backordered": [
            {
                "product": "Milk",
                "product_id": "milk_1",
                "qty": 3,
                "reason": "insufficient stock",
                "backorder_id": "BO-1",
            }
        ],
    }
    result = PaymentConfirmationCapability().handle({"context": {"order": order, "total": order["total"]}})

    assert result["success"] is False
    assert "backordered" in result["error"]


@pytest.mark.asyncio
async def test_world008_warehouse_fire_mid_tick_self_heals_within_order_creation():
    """External world event, not a single product quietly running low:
    the STORE's whole upstream fulfillment chain goes down (a warehouse
    fire — attributes["status"] on the warehouse entity) between
    ProductSelection and OrderCreation. try_reserve alone can't see this
    (the product's own `quantity` is untouched), so OrderCreation now
    checks supply_chain_ok() directly — see its own comment for why this
    check exists as its own gate, separate from try_reserve. Must
    self-heal in this SAME step, exactly like a plain stockout, rather
    than waiting for OrderConfirmation to catch it one stage later."""
    kg = KnowledgeGraph()
    store_a = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99, warehouse_id="wh_a")["store_id"]
    kg.add_entity(
        "wh_a",
        EntityType.ORGANIZATION,
        "Trader Joe's Warehouse",
        {"status": "operational"},
    )
    milk_id = list_product(
        kg,
        store_a,
        "merchant_a",
        "Milk",
        price=3.99,
        quantity=5,
        store_name="Trader Joe's",
    )["product_id"]

    store_b = onboard_merchant(kg, "merchant_b", "Whole Foods", delivery_fee=2.99)["store_id"]
    milk_alt_id = list_product(
        kg,
        store_b,
        "merchant_b",
        "Whole Milk",
        price=4.29,
        quantity=5,
        store_name="Whole Foods",
    )["product_id"]
    kg.add_entity(
        "wallet_world_test",
        EntityType.ACCOUNT,
        "World Test Wallet",
        {
            "account_type": "debit",
            "balance": 200.0,
            "owner": ACTOR_ID,
        },
    )
    kg.add_entity(
        "proc_stripe",
        EntityType.ORGANIZATION,
        "Stripe",
        {"type": "payment_processor", "priority": 0},
    )

    context = _base_context(kg)
    executor = build_execution_engine("grocery")

    def mutate(kg):
        kg.update_entity("wh_a", attributes={"status": "on_fire"})

    register_mutation(
        ACTOR_ID,
        trigger=lambda a: (
            a.capability == "ProductSelection" and any(s["id"] == milk_id for s in a.parameters.get("selection", []))
        ),
        mutate=mutate,
    )

    actions = (
        _selection_action("a0", 0, milk_id),
        Action(action_id="a1", capability="OrderCreation", step_index=1, depends_on=(0,)),
        *_pay_and_confirm(2, (1,)),
    )

    result = await executor.execute(actions, context)

    assert kg.get_entity("wh_a").attributes["status"] == "on_fire"
    creation = result.actions[1]
    assert creation.success, f"OrderCreation should self-heal from a warehouse failure too: {creation.error}"
    assert creation.result["backordered"] == []
    assert "replanned" in creation.result
    assert "supply chain" in creation.result["replanned"][0]
    confirmed_ids = {item["product_id"] for item in kg.get_entity(creation.result["order_id"]).attributes["items"]}
    assert milk_id not in confirmed_ids
    assert milk_alt_id in confirmed_ids

    # OrderConfirmation must find nothing further stale -- the fire was
    # already handled a full stage earlier.
    confirm_outcome = result.actions[4]
    assert confirm_outcome.success
    assert not confirm_outcome.result.get("replanned")
