"""NegotiatePrice -> OrderCreation wiring — a real, bounded negotiated
deal now actually changes what a purchase charges, not just a number
reported back and never used again.

Confirmed live this session: NegotiatePriceCapability computed a real,
mathematically-bounded deal (negotiate_price(), GS-1501) but nothing
downstream ever read the result — OrderCreationCapability priced every
item strictly off the product's own listed price regardless of any
negotiation step in the same plan. project_action_result_to_context
(grocery.py) now threads a product_id-linked agreed deal into
context["negotiated_prices"], which OrderCreationCapability applies to
that item's price before computing subtotal/tax/total — see both
functions' own comments for exactly where.

Same in-process Action-tuple + ActionExecutor style as
test_world_mutation.py — deterministic, no LLM planner dependency.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.domains import (
    grocery,
)  # noqa: F401 -- registers the grocery vertical
from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.grocery import negotiate_price
from src.monkey_brain.kernel.domains.vertical_router import build_execution_engine
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph
from src.monkey_brain.kernel.pipeline.execution import Action

ACTOR_ID = "negotiation_test_actor"
LISTED_PRICE = 3.99
MIN_SELLER_PRICE = 3.00
BUYER_TARGET_PRICE = 3.20


def _seed_world():
    kg = KnowledgeGraph()
    store_id = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    milk_id = list_product(
        kg,
        store_id,
        "merchant_a",
        "Milk",
        price=LISTED_PRICE,
        quantity=5,
        store_name="Trader Joe's",
    )["product_id"]
    kg.add_entity(
        "wallet_1",
        EntityType.ACCOUNT,
        "Wallet",
        {"account_type": "debit", "balance": 100.0, "owner": ACTOR_ID},
    )
    kg.add_entity(
        "proc_stripe",
        EntityType.ORGANIZATION,
        "Stripe",
        {"type": "payment_processor", "priority": 0},
    )
    return kg, milk_id


@pytest.mark.asyncio
async def test_agreed_negotiation_changes_what_order_creation_actually_charges():
    kg, milk_id = _seed_world()
    context = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "negotiate milk down, then buy it",
    }
    executor = build_execution_engine("grocery")

    expected_deal = negotiate_price(LISTED_PRICE, MIN_SELLER_PRICE, BUYER_TARGET_PRICE)
    assert expected_deal["agreed"] is True, "test setup must produce a real agreed deal"
    assert expected_deal["price"] < LISTED_PRICE, "test setup must produce a genuinely lower price"

    actions = (
        Action(
            action_id="a0",
            capability="ProductSelection",
            step_index=0,
            parameters={"selection": [{"id": milk_id, "qty": 1}]},
        ),
        Action(
            action_id="a1",
            capability="NegotiatePrice",
            step_index=1,
            depends_on=(0,),
            parameters={
                "listed_price": LISTED_PRICE,
                "min_seller_price": MIN_SELLER_PRICE,
                "buyer_target_price": BUYER_TARGET_PRICE,
                "product_id": milk_id,
            },
        ),
        Action(action_id="a2", capability="OrderCreation", step_index=2, depends_on=(0, 1)),
        Action(
            action_id="a3",
            capability="PaymentConfirmation",
            step_index=3,
            depends_on=(2,),
        ),
        Action(action_id="a4", capability="Payment", step_index=4, depends_on=(3,)),
    )

    result = await executor.execute(actions, context)

    negotiation = result.actions[1]
    assert negotiation.success
    assert negotiation.result["agreed"] is True
    assert negotiation.result["price"] == expected_deal["price"]

    creation = result.actions[2]
    assert creation.success, creation.error
    assert creation.result["items"][0]["unit_price"] == expected_deal["price"]
    expected_subtotal = round(expected_deal["price"] * 1, 2)
    assert creation.result["subtotal"] == expected_subtotal
    assert creation.result["subtotal"] != LISTED_PRICE, "must not have silently charged the listed price"

    payment = result.actions[4]
    assert payment.success, payment.error
    assert payment.result["amount"] == creation.result["total"]


@pytest.mark.asyncio
async def test_negotiation_without_product_id_is_reported_but_not_applied():
    """Omitting product_id is a real, supported standalone use (a
    negotiation with no purchase to follow) — it must not accidentally
    override an unrelated cart item's price."""
    kg, milk_id = _seed_world()
    context = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "just tell me the negotiated price",
    }
    executor = build_execution_engine("grocery")

    actions = (
        Action(
            action_id="a0",
            capability="ProductSelection",
            step_index=0,
            parameters={"selection": [{"id": milk_id, "qty": 1}]},
        ),
        Action(
            action_id="a1",
            capability="NegotiatePrice",
            step_index=1,
            depends_on=(0,),
            parameters={
                "listed_price": LISTED_PRICE,
                "min_seller_price": MIN_SELLER_PRICE,
                "buyer_target_price": BUYER_TARGET_PRICE,
            },
        ),
        Action(action_id="a2", capability="OrderCreation", step_index=2, depends_on=(0, 1)),
    )

    result = await executor.execute(actions, context)

    negotiation = result.actions[1]
    assert negotiation.success
    assert negotiation.result["agreed"] is True
    assert "product_id" not in negotiation.result

    creation = result.actions[2]
    assert creation.success
    assert creation.result["items"][0]["unit_price"] == LISTED_PRICE
