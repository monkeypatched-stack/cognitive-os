"""MB-3035 Flash Sale — massive demand spike scenario.

Massive demand spike.

Like MB-3015, no new code was needed: a flash sale doesn't need a
separate concurrency mechanism of its own — it's MB-3034's
create_promotion() (a real discount) layered on top of try_reserve()'s
existing CAS-based reservation, which doesn't care what a product's
price is. This file reproduces the "massive demand spike" scenario
directly against a flash-sale-priced item: 500 real OS threads racing
for 100 units at try_reserve()'s level (no oversell), and 60 threads
racing for 20 units at the full OrderCreationCapability checkout level,
verifying MB-3031's auto-backorder means every request succeeds
honestly — exactly the flash sale's capacity reserved, the rest
correctly backordered, never a hard failure and never an oversell.
"""

from __future__ import annotations

import threading

from src.monkey_brain.kernel.domains.commerce import (
    create_promotion,
    list_product,
    onboard_merchant,
)
from src.monkey_brain.kernel.domains.grocery import OrderCreationCapability, try_reserve
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph

MERCHANT_ID = "merchant_bob"


def _run_concurrent(fn, count: int) -> list:
    results = [None] * count

    def worker(i: int) -> None:
        results[i] = fn(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_mb3035_no_oversell_under_500_actors_racing_for_100_flash_sale_units():
    kg = KnowledgeGraph()
    store_id = onboard_merchant(kg, MERCHANT_ID, "Bob's Store")["store_id"]
    product_id = list_product(kg, store_id, MERCHANT_ID, "Hot Item", price=50.0, quantity=100)["product_id"]
    create_promotion(kg, product_id, MERCHANT_ID, sale_price=10.0)

    results = _run_concurrent(
        lambda i: try_reserve(kg, product_id, f"actor_{i}", qty=1, hold_seconds=30.0)[0],
        count=500,
    )

    succeeded = sum(1 for ok in results if ok)
    assert succeeded == 100, f"expected exactly 100 successful reservations, got {succeeded}"

    entity = kg.get_entity(product_id)
    active = [r for r in entity.attributes.get("reservations", []) if r.get("until", 0) > 0]
    assert len(active) == 100
    assert sum(r["qty"] for r in active) == 100


def test_mb3035_checkout_spike_reserves_exact_capacity_and_backorders_the_rest():
    kg = KnowledgeGraph()
    store_id = onboard_merchant(kg, MERCHANT_ID, "Bob's Store")["store_id"]
    product_id = list_product(kg, store_id, MERCHANT_ID, "Hot Item", price=50.0, quantity=20)["product_id"]
    create_promotion(kg, product_id, MERCHANT_ID, sale_price=10.0)
    cap = OrderCreationCapability()

    def place(i: int) -> tuple[bool, bool]:
        result = cap.handle(
            {
                "context": {
                    "knowledge_graph": kg,
                    "actor_id": f"actor_{i}",
                    "selected_product": [
                        {
                            "id": product_id,
                            "name": "Hot Item",
                            "price": 10.0,
                            "qty": 1,
                            "store_id": store_id,
                            "store_name": "Bob's Store",
                        }
                    ],
                }
            }
        )
        reserved = len(result.get("backordered", [])) == 0
        return result["success"], reserved

    results = _run_concurrent(place, count=60)

    # MB-3031: a demand spike never hard-fails an order — every request
    # succeeds, either genuinely reserved or honestly backordered.
    assert all(ok for ok, _reserved in results)
    reserved_count = sum(1 for _ok, reserved in results if reserved)
    backordered_count = sum(1 for _ok, reserved in results if not reserved)
    assert reserved_count == 20
    assert backordered_count == 40
