"""MB-3005 Product Detail — open-product scenario.

Open product.

Verify:
    - inventory
    - images
    - reviews
    - variants

Backed by kernel/domains/commerce.py::get_product_detail(), added for this
scenario: "images"/"reviews"/"variants" had no implementation anywhere in
the codebase before this (no fields, no query logic, no review system) —
"inventory" was already real (products already carry a quantity/stock
attribute open_products()/try_reserve() reason about). get_product_detail()
reuses that same attributes["quantity"] rather than introducing a second,
parallel stock number, and adds attributes["images"]/["reviews"]/
["variants"] as the new product-detail fields, normalized into typed
ProductReview/ProductVariant records.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    CommerceCapabilityBus,
    get_product_detail,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

PRODUCT_ID = "prod_milk_2pct"
VARIANT_ID = "prod_milk_whole"
GHOST_VARIANT_ID = "prod_milk_discontinued"  # referenced, but not in the catalog


def _seed_catalog() -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(
        PRODUCT_ID,
        EntityType.ASSET,
        "Store Brand 2% Milk",
        {
            "price": 3.99,
            "quantity": 24,
            "store_id": "store_001",
            "images": [
                "https://example.com/milk-2pct-front.jpg",
                "https://example.com/milk-2pct-nutrition.jpg",
            ],
            "reviews": [
                {"reviewer": "alice", "rating": 4.5, "comment": "Good value"},
                {"reviewer": "bob", "rating": 3.0, "comment": "A bit watery"},
            ],
            "variants": [
                {"entity_id": VARIANT_ID, "label": "Whole Milk"},
                {"entity_id": GHOST_VARIANT_ID, "label": "Discontinued"},
            ],
        },
    )
    kg.add_entity(
        VARIANT_ID,
        EntityType.ASSET,
        "Store Brand Whole Milk",
        {
            "price": 4.29,
            "quantity": 10,
            "store_id": "store_001",
        },
    )
    return kg


def test_mb3005_open_product_returns_inventory_images_reviews_variants():
    kg = _seed_catalog()

    detail = get_product_detail(kg, PRODUCT_ID)
    assert detail is not None

    # inventory — the same attributes["quantity"] checkout itself reads,
    # not a second, separately-tracked stock number.
    assert detail.inventory == 24

    # images.
    assert detail.images == (
        "https://example.com/milk-2pct-front.jpg",
        "https://example.com/milk-2pct-nutrition.jpg",
    )

    # reviews.
    assert len(detail.reviews) == 2
    assert {r.reviewer for r in detail.reviews} == {"alice", "bob"}
    assert detail.average_rating == 3.75

    # variants — resolved against the catalog: the real one is present,
    # the dangling reference to a product no longer in the catalog is
    # dropped rather than raising.
    assert len(detail.variants) == 1
    assert detail.variants[0].entity_id == VARIANT_ID
    assert detail.variants[0].label == "Whole Milk"


def test_mb3005_product_with_no_detail_data_returns_empty_not_error():
    kg = _seed_catalog()

    detail = get_product_detail(kg, VARIANT_ID)
    assert detail is not None
    assert detail.inventory == 10
    assert detail.images == ()
    assert detail.reviews == ()
    # None (not 0.0) — "no reviews yet", distinguishable from "reviews
    # exist and average to zero".
    assert detail.average_rating is None
    assert detail.variants == ()


def test_mb3005_open_product_via_capability_bus():
    kg = _seed_catalog()
    bus = CommerceCapabilityBus([CommerceCapability()])

    found = bus.discover_operation("open_product")
    assert found is not None, "catalog must expose an open_product operation"

    detail = bus.invoke("open_product", kg, PRODUCT_ID)
    assert detail == get_product_detail(kg, PRODUCT_ID)


def test_mb3005_open_nonexistent_product_returns_none():
    kg = _seed_catalog()
    assert get_product_detail(kg, "does-not-exist") is None
