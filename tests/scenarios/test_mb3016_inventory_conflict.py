"""MB-3016 Inventory Conflict — two customers, one item scenario.

Two customers buy last item.

Expected:
    - One succeeds.
    - One backordered.

"One succeeds" was already real (MB-3015: try_reserve's CAS-based
reservation guarantees exactly one winner, no oversell). "Backordered"
did not exist anywhere — the loser of a try_reserve race was simply
declined, with no way to record a queued claim for later fulfillment.
Built kernel/domains/grocery.py::place_backorder()/fulfill_backorders()
for this: a backorder is a real, persisted spot in line (not a second
reservation system) — fulfill_backorders() grants it by calling the SAME
try_reserve() every other reservation goes through, strictly FIFO by
placement time, whenever stock increases (a restock).
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    CommerceCapabilityBus,
)
from src.monkey_brain.kernel.domains.grocery import (
    confirm_reservation,
    fulfill_backorders,
    place_backorder,
    try_reserve,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

PRODUCT_ID = "prod_last"


def _seed_last_item() -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(PRODUCT_ID, EntityType.ASSET, "Last One", {"price": 9.99, "quantity": 1})
    return kg


def test_mb3016_one_succeeds_one_backordered():
    kg = _seed_last_item()

    won, _ = try_reserve(kg, PRODUCT_ID, "alice", qty=1)
    lost, message = try_reserve(kg, PRODUCT_ID, "bob", qty=1)

    assert won is True
    assert lost is False
    assert "insufficient stock" in message

    backorder = place_backorder(kg, PRODUCT_ID, "bob", qty=1)

    assert backorder["status"] == "pending"
    assert backorder["actor_id"] == "bob"
    assert backorder["qty"] == 1


def test_mb3016_backorder_fulfilled_on_restock():
    kg = _seed_last_item()
    try_reserve(kg, PRODUCT_ID, "alice", qty=1)
    place_backorder(kg, PRODUCT_ID, "bob", qty=1)

    # No stock yet -- nothing to fulfill.
    assert fulfill_backorders(kg, PRODUCT_ID) == []

    # Restock: one more unit becomes available.
    entity = kg.get_entity(PRODUCT_ID)
    kg.update_entity(PRODUCT_ID, attributes={"quantity": entity.attributes["quantity"] + 1})

    fulfilled = fulfill_backorders(kg, PRODUCT_ID)

    assert len(fulfilled) == 1
    assert fulfilled[0]["actor_id"] == "bob"
    # The backorder became a REAL reservation bob can confirm through the
    # ordinary checkout flow — not a separate, parallel commit.
    confirmed, _ = confirm_reservation(kg, PRODUCT_ID, "bob")
    assert confirmed is True


def test_mb3016_backorders_fulfilled_strictly_fifo():
    kg = _seed_last_item()
    try_reserve(kg, PRODUCT_ID, "alice", qty=1)

    first_in_line = place_backorder(kg, PRODUCT_ID, "bob", qty=1, now=1000.0)
    second_in_line = place_backorder(kg, PRODUCT_ID, "carol", qty=1, now=2000.0)

    # One restock, one unit -- must go to bob (earlier), not carol, even
    # though iteration/dict order could otherwise favor whichever was
    # added to the KG last.
    entity = kg.get_entity(PRODUCT_ID)
    kg.update_entity(PRODUCT_ID, attributes={"quantity": entity.attributes["quantity"] + 1})
    fulfilled = fulfill_backorders(kg, PRODUCT_ID)

    assert len(fulfilled) == 1
    assert fulfilled[0]["actor_id"] == "bob"
    assert kg.get_entity(second_in_line["backorder_id"]).attributes["status"] == "pending"

    # A second restock reaches carol.
    entity = kg.get_entity(PRODUCT_ID)
    kg.update_entity(PRODUCT_ID, attributes={"quantity": entity.attributes["quantity"] + 1})
    fulfilled_2 = fulfill_backorders(kg, PRODUCT_ID)

    assert len(fulfilled_2) == 1
    assert fulfilled_2[0]["actor_id"] == "carol"


def test_mb3016_backorder_never_skips_ahead_of_an_earlier_larger_request():
    kg = _seed_last_item()
    try_reserve(kg, PRODUCT_ID, "alice", qty=1)

    # bob backordered first, wants 2 units; carol backordered later,
    # wants only 1.
    place_backorder(kg, PRODUCT_ID, "bob", qty=2, now=1000.0)
    place_backorder(kg, PRODUCT_ID, "carol", qty=1, now=2000.0)

    # Restock by exactly 1 -- enough for carol's smaller request, NOT
    # enough for bob's. Strict FIFO means carol must still wait.
    entity = kg.get_entity(PRODUCT_ID)
    kg.update_entity(PRODUCT_ID, attributes={"quantity": entity.attributes["quantity"] + 1})

    fulfilled = fulfill_backorders(kg, PRODUCT_ID)

    assert fulfilled == []


def test_mb3016_backorder_and_fulfill_via_capability_bus():
    kg = _seed_last_item()
    try_reserve(kg, PRODUCT_ID, "alice", qty=1)
    bus = CommerceCapabilityBus([CommerceCapability()])

    assert bus.discover_operation("place_backorder") is not None
    assert bus.discover_operation("fulfill_backorders") is not None

    backorder = bus.invoke("place_backorder", kg, PRODUCT_ID, "bob", 1)
    assert backorder["status"] == "pending"

    entity = kg.get_entity(PRODUCT_ID)
    kg.update_entity(PRODUCT_ID, attributes={"quantity": entity.attributes["quantity"] + 1})
    fulfilled = bus.invoke("fulfill_backorders", kg, PRODUCT_ID)
    assert len(fulfilled) == 1
    assert fulfilled[0]["actor_id"] == "bob"
