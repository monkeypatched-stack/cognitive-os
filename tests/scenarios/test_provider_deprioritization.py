"""Level 17 write-side — a store that repeatedly fails real orders gets
automatically deprioritized out of the candidate pool, without anything
being hand-set on the store entity.

has_learned_to_avoid() (grocery.py) and open_products()'s use of it were
already real, pre-existing, and covered by direct unit-level tests
elsewhere — trust/fulfilled_count/cancelled_count read off the store
entity, min_attempts=10/trust_threshold=0.5. What was missing: nothing in
the real order pipeline ever WROTE those fields. record_order_outcome()
(also pre-existing, with real CAS concurrency handling) was fully built
but called from nowhere.

This wires the two clearest existing real-outcome signals: cancel_order()
(a store's order didn't work out) and OrderConfirmationCapability's real
success (it did). These tests exercise that wiring end to end — real
cancellations/confirmations changing a real store's trust score, changing
what open_products() actually offers next time — not the read-side logic
itself, which is already covered.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.grocery import (
    OrderConfirmationCapability,
    cancel_order,
    has_learned_to_avoid,
    open_products,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

ACTOR_ID = "deprioritization_test_actor"


def _seed_two_stores():
    """Store A will be driven into learned avoidance; Store B is an
    unrelated, always-reliable alternative selling the same item, so
    exclusion can be verified as SPECIFIC to Store A, not a side effect
    that silently emptied the whole catalog."""
    kg = KnowledgeGraph()
    store_a = onboard_merchant(kg, "merchant_a", "Unreliable Mart", delivery_fee=1.99)["store_id"]
    product_a = list_product(
        kg,
        store_a,
        "merchant_a",
        "Milk",
        price=3.99,
        quantity=50,
        store_name="Unreliable Mart",
    )["product_id"]
    store_b = onboard_merchant(kg, "merchant_b", "Reliable Mart", delivery_fee=1.99)["store_id"]
    product_b = list_product(
        kg,
        store_b,
        "merchant_b",
        "Milk",
        price=4.29,
        quantity=50,
        store_name="Reliable Mart",
    )["product_id"]
    kg.add_entity(
        "wallet_shared",
        EntityType.ACCOUNT,
        "Shared Wallet",
        {
            "account_type": "debit",
            "balance": 10_000.0,
            "owner": ACTOR_ID,
        },
    )
    return kg, store_a, product_a, store_b, product_b


def _place_and_cancel_order(kg, order_id: str, product_id: str):
    kg.add_entity(
        order_id,
        EntityType.EVENT,
        "Grocery Order",
        {
            "items": [{"product_id": product_id, "qty": 1}],
            "total": 3.99,
            "status": "confirmed",
            "paid_wallet_id": "wallet_shared",
            "paid_amount": 3.99,
            "payment_status": "paid",
        },
    )
    result = cancel_order(kg, order_id, actor_id=ACTOR_ID)
    assert result["success"], result


def test_ten_real_cancellations_deprioritize_the_store_out_of_open_products():
    kg, store_a, product_a, store_b, product_b = _seed_two_stores()

    for i in range(10):
        _place_and_cancel_order(kg, f"ORD-cancel-{i}", product_a)

    store = kg.get_entity(store_a)
    assert store.attributes["cancelled_count"] == 10
    assert store.attributes["trust"] == 0.0
    assert has_learned_to_avoid(store) is True

    catalog_ids = {p.entity_id for p in open_products(kg, item_phrase="Milk")}
    assert product_a not in catalog_ids, "the repeatedly-failing store must be excluded"
    assert product_b in catalog_ids, "the unrelated, reliable store must be unaffected"


def test_fewer_than_min_attempts_does_not_yet_deprioritize():
    """min_attempts=10 is what makes this LEARNED, not reactive to one or
    two bad outcomes — real early failures must not blacklist a store."""
    kg, store_a, product_a, _store_b, _product_b = _seed_two_stores()

    for i in range(2):
        _place_and_cancel_order(kg, f"ORD-cancel-{i}", product_a)

    store = kg.get_entity(store_a)
    assert store.attributes["cancelled_count"] == 2
    assert has_learned_to_avoid(store) is False
    catalog_ids = {p.entity_id for p in open_products(kg, item_phrase="Milk")}
    assert product_a in catalog_ids


def test_order_confirmation_records_a_real_positive_outcome():
    kg, store_a, product_a, _store_b, _product_b = _seed_two_stores()
    product = kg.get_entity(product_a)
    cart = [
        {
            "id": product_a,
            "name": product.name,
            "price": product.attributes["price"],
            "qty": 1,
            **product.attributes,
        }
    ]
    context = {
        "knowledge_graph": kg,
        "actor_id": ACTOR_ID,
        "question": "buy milk",
        "order": {"order_id": "ORD-1", "total": 3.99},
        "selected_product": cart,
    }

    result = OrderConfirmationCapability().handle({"context": context})

    assert result["success"] is True
    store = kg.get_entity(store_a)
    assert store.attributes["fulfilled_count"] == 1
    assert store.attributes["trust"] == 1.0
