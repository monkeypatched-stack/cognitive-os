"""MB-3007 Add To Cart — customer adds a product scenario.

Customer adds product.

Verify:
    - Cart updated.

No persistent "cart" concept existed anywhere in the codebase before this
(the word "cart" only ever appeared as an in-flight, ephemeral list of
selected products passed through checkout planning — never a durable,
per-actor object a customer could add to across separate requests).
Backed by kernel/domains/commerce.py::Cart/get_cart()/add_to_cart(),
following the same "persist as one KG entity, same as an Order" pattern
already established for MB-3005's product detail and MB-3006's
recommendations, so "cart updated" means a real, durable state change a
later request can read back — not an in-memory list that disappears
between calls.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    CommerceCapabilityBus,
    add_to_cart,
    get_cart,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

CUSTOMER_ID = "alice"
MILK = "prod_milk"
BREAD = "prod_bread"


def _seed_catalog() -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(MILK, EntityType.ASSET, "Milk", {"price": 3.99})
    kg.add_entity(BREAD, EntityType.ASSET, "Bread", {"price": 2.99})
    return kg


def test_mb3007_cart_starts_empty():
    kg = _seed_catalog()
    cart = get_cart(kg, CUSTOMER_ID)
    assert cart.lines == ()
    assert cart.item_count == 0
    assert cart.total == 0.0


def test_mb3007_add_product_updates_cart():
    kg = _seed_catalog()

    cart = add_to_cart(kg, CUSTOMER_ID, MILK, quantity=2)

    assert cart is not None
    assert cart.item_count == 2
    assert cart.total == 7.98
    assert cart.lines[0].product_id == MILK
    assert cart.lines[0].name == "Milk"

    # Cart updated durably — a later, independent read sees the same state.
    assert get_cart(kg, CUSTOMER_ID) == cart


def test_mb3007_adding_same_product_again_accumulates_quantity():
    kg = _seed_catalog()
    add_to_cart(kg, CUSTOMER_ID, MILK, quantity=1)
    cart = add_to_cart(kg, CUSTOMER_ID, MILK, quantity=2)

    assert len(cart.lines) == 1, "must accumulate onto the existing line, not duplicate it"
    assert cart.lines[0].quantity == 3


def test_mb3007_adding_a_different_product_adds_a_new_line():
    kg = _seed_catalog()
    add_to_cart(kg, CUSTOMER_ID, MILK, quantity=1)
    cart = add_to_cart(kg, CUSTOMER_ID, BREAD, quantity=1)

    assert {line.product_id for line in cart.lines} == {MILK, BREAD}
    assert cart.item_count == 2
    assert cart.total == 6.98


def test_mb3007_adding_nonexistent_product_leaves_cart_unchanged():
    kg = _seed_catalog()
    add_to_cart(kg, CUSTOMER_ID, MILK, quantity=1)
    before = get_cart(kg, CUSTOMER_ID)

    result = add_to_cart(kg, CUSTOMER_ID, "does-not-exist")

    assert result is None
    assert get_cart(kg, CUSTOMER_ID) == before


def test_mb3007_carts_are_independent_per_customer():
    kg = _seed_catalog()
    add_to_cart(kg, "alice", MILK, quantity=1)

    assert get_cart(kg, "bob").lines == ()


def test_mb3007_add_to_cart_via_capability_bus():
    kg = _seed_catalog()
    bus = CommerceCapabilityBus([CommerceCapability()])

    assert bus.discover_operation("add_to_cart") is not None
    assert bus.discover_operation("get_cart") is not None

    bus.invoke("add_to_cart", kg, CUSTOMER_ID, MILK, quantity=1)
    assert bus.invoke("get_cart", kg, CUSTOMER_ID) == get_cart(kg, CUSTOMER_ID)
