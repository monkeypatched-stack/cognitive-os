"""MB-3037 Merchant Onboarding — new merchant joins marketplace scenario.

New merchant joins marketplace.

"Merchant" (MB-3001) was an ActorType.ENTERPRISE actor in the society/
geography system, while "Store" (used throughout checkout —
DeliveryCapability, OrderCreationCapability, open_products) was a
completely separate KG ORGANIZATION entity — nothing linked the two.
Per explicit design choice ("store gets owner_id"): built
kernel/domains/commerce.py::onboard_merchant() to create a real Store
entity with attributes["owner_id"] set to the merchant's actor_id — the
same ownership-attribute convention already used elsewhere (finance.py's
wallet _owned_by, grocery.py's pantry _pantry_owned_by). A product is
owned transitively via its store_id -> store.owner_id (require_store_owner()),
the foundation MB-3033/3038/3039/3040 all build on.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    onboard_merchant,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

MERCHANT_ID = "merchant_bob"


def test_mb3037_onboarding_creates_a_real_owned_store():
    kg = KnowledgeGraph()

    result = onboard_merchant(kg, MERCHANT_ID, "Bob's Store", delivery_fee=2.5)

    assert result["success"] is True
    store = kg.get_entity(result["store_id"])
    assert store is not None
    assert store.entity_type == EntityType.ORGANIZATION
    assert store.name == "Bob's Store"
    assert store.attributes["owner_id"] == MERCHANT_ID
    assert store.attributes["delivery_fee"] == 2.5


def test_mb3037_two_merchants_get_two_distinct_stores():
    kg = KnowledgeGraph()

    bob_store = onboard_merchant(kg, "merchant_bob", "Bob's Store")
    eve_store = onboard_merchant(kg, "merchant_eve", "Eve's Shop")

    assert bob_store["store_id"] != eve_store["store_id"]
    assert kg.get_entity(bob_store["store_id"]).attributes["owner_id"] == "merchant_bob"
    assert kg.get_entity(eve_store["store_id"]).attributes["owner_id"] == "merchant_eve"


def test_mb3037_onboard_merchant_via_capability():
    kg = KnowledgeGraph()
    cap = CommerceCapability()

    assert cap.can_handle("onboard_merchant")
    result = cap.invoke("onboard_merchant", kg, MERCHANT_ID, "Bob's Store")

    assert result["success"] is True
