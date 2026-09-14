"""MB-3036 Black Friday — 10,000 simultaneous orders scenario.

10,000 simultaneous orders.

Expected: Scale verification.

Runs the real checkout path (OrderCreationCapability, MB-3031's
auto-backorder wiring) across a realistic multi-merchant catalog (2
merchants, 5 products, 500 units each) under 10,000 concurrent order
attempts via a bounded thread pool — genuine OS-thread concurrency
against the shared KnowledgeGraph, not simulated.

This surfaced a real bug: OrderCreationCapability's order_id used only
a 6-hex-char random suffix (~16.7M values), which collided often enough
under this scale (~5 collisions per 10,000 concurrent orders, verified
live) that one customer's order silently overwrote another's
(KnowledgeGraph.add_entity's documented overwrite-on-reuse behavior).
Fixed by widening the suffix to the full uuid4().hex (128 bits),
verified at the same 10,000-order scale before this test was written.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.grocery import OrderCreationCapability
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph

ORDER_COUNT = 10_000
STOCK_PER_PRODUCT = 500


def _seed_catalog() -> tuple[KnowledgeGraph, list[tuple[str, str, str]]]:
    kg = KnowledgeGraph()
    products = []
    for merchant_idx, merchant_id in enumerate(["merchant_bob", "merchant_eve"]):
        store_id = onboard_merchant(kg, merchant_id, f"Store {merchant_idx}")["store_id"]
        item_count = 3 if merchant_id == "merchant_bob" else 2
        for item_idx in range(item_count):
            product_id = list_product(
                kg,
                store_id,
                merchant_id,
                f"Item-{merchant_idx}-{item_idx}",
                price=20.0,
                quantity=STOCK_PER_PRODUCT,
            )["product_id"]
            products.append((product_id, store_id, f"Item-{merchant_idx}-{item_idx}"))
    return kg, products


def test_mb3036_10000_simultaneous_orders_scale_verification():
    kg, products = _seed_catalog()
    cap = OrderCreationCapability()

    def place_order(i: int) -> dict:
        product_id, store_id, name = products[i % len(products)]
        return cap.handle(
            {
                "context": {
                    "knowledge_graph": kg,
                    "actor_id": f"actor_{i}",
                    "selected_product": [
                        {
                            "id": product_id,
                            "name": name,
                            "price": 20.0,
                            "qty": 1,
                            "store_id": store_id,
                            "store_name": name,
                        }
                    ],
                }
            }
        )

    results: list[dict | None] = [None] * ORDER_COUNT
    with ThreadPoolExecutor(max_workers=200) as pool:
        futures = {pool.submit(place_order, i): i for i in range(ORDER_COUNT)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()

    # Every one of 10,000 simultaneous orders succeeds honestly (MB-3031:
    # a demand spike backorders excess demand, it never hard-fails).
    assert all(r["success"] for r in results)

    # Every order got a genuinely unique order_id — no silent overwrites.
    order_ids = [r["order_id"] for r in results]
    assert len(set(order_ids)) == ORDER_COUNT

    # No product oversold beyond its real stock, despite the concurrent spike.
    for product_id, _store_id, name in products:
        entity = kg.get_entity(product_id)
        reservations = entity.attributes.get("reservations", [])
        reserved_qty = sum(r["qty"] for r in reservations if r.get("until", 0) > 0)
        assert reserved_qty <= STOCK_PER_PRODUCT, f"{name} oversold: {reserved_qty} > {STOCK_PER_PRODUCT}"

    # Every order was accounted for — either genuinely reserved or
    # honestly backordered, nothing silently dropped.
    reserved_count = sum(1 for r in results if not r["backordered"])
    backordered_count = sum(1 for r in results if r["backordered"])
    assert reserved_count + backordered_count == ORDER_COUNT
    assert reserved_count == len(products) * STOCK_PER_PRODUCT
