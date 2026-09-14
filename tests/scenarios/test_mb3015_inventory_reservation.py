"""MB-3015 Inventory Reservation — concurrent reservation scenario.

Reserve inventory.

Expected:
    - No oversell.

Like MB-3010/3012/3013, no new code was needed: kernel/domains/
grocery.py's try_reserve()/confirm_reservation() already exist
specifically to guarantee this, via optimistic concurrency
(KnowledgeGraph.compare_and_swap keyed to the entity's version) rather
than a plain read-then-write — the docstring documents a real historical
bug this fixed (a single-slot reservation design let 151 of 300 concurrent
claims "succeed" against only 100 real units) and references a genuine
"300 actors racing for 100 units" scale test. This file reproduces that
scenario with real OS threads (not asyncio — the in-memory
KnowledgeGraph.compare_and_swap's own docstring is explicit that its
safety guarantee is scoped to asyncio's cooperative concurrency; real
threads are the honest, harder test of the CAS retry loop itself), plus
the deterministic edge cases (exact-tie race, expiry, over-request).
"""

from __future__ import annotations

import threading
import time

from src.monkey_brain.kernel.domains.grocery import confirm_reservation, try_reserve
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph


def _run_concurrent(fn, count: int) -> list:
    """Run fn(i) for i in range(count) on real OS threads, all started
    before any is joined, so they genuinely race rather than running
    sequentially."""
    results = [None] * count

    def worker(i):
        results[i] = fn(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_mb3015_two_actors_racing_for_the_last_unit():
    kg = KnowledgeGraph()
    kg.add_entity("prod_last", EntityType.ASSET, "Last One", {"price": 9.99, "quantity": 1})

    results = _run_concurrent(
        lambda i: try_reserve(kg, "prod_last", f"actor_{i}", qty=1, hold_seconds=30.0)[0],
        count=2,
    )

    assert sum(1 for ok in results if ok) == 1, "exactly one of the two racing actors must win"
    entity = kg.get_entity("prod_last")
    assert sum(r["qty"] for r in entity.attributes["reservations"]) == 1


def test_mb3015_no_oversell_under_concurrent_reservation_300_actors_100_units():
    kg = KnowledgeGraph()
    kg.add_entity(
        "prod_limited",
        EntityType.ASSET,
        "Limited Item",
        {"price": 9.99, "quantity": 100},
    )

    results = _run_concurrent(
        lambda i: try_reserve(kg, "prod_limited", f"actor_{i}", qty=1, hold_seconds=30.0)[0],
        count=300,
    )

    succeeded = sum(1 for ok in results if ok)
    assert succeeded == 100, f"expected exactly 100 successful reservations, got {succeeded}"

    entity = kg.get_entity("prod_limited")
    active = [r for r in entity.attributes.get("reservations", []) if r.get("until", 0) > 0]
    assert len(active) == 100, "reservation LIST must record every concurrent holder, not overwrite them"
    assert sum(r["qty"] for r in active) == 100


def test_mb3015_no_oversell_under_concurrent_reserve_and_confirm():
    """The harder version: not just holding a reservation, but actually
    committing it (the real stock decrement) — concurrently, for more
    claimants than available stock."""
    kg = KnowledgeGraph()
    kg.add_entity(
        "prod_limited",
        EntityType.ASSET,
        "Limited Item",
        {"price": 9.99, "quantity": 50},
    )

    def reserve_then_confirm(i: int) -> bool:
        ok, _ = try_reserve(kg, "prod_limited", f"actor_{i}", qty=1, hold_seconds=30.0)
        if not ok:
            return False
        confirmed, _ = confirm_reservation(kg, "prod_limited", f"actor_{i}")
        return confirmed

    results = _run_concurrent(reserve_then_confirm, count=150)

    confirmed_count = sum(1 for ok in results if ok)
    assert confirmed_count == 50

    final = kg.get_entity("prod_limited")
    assert final.attributes["quantity"] == 0, "stock decremented by exactly the confirmed count, never oversold"
    assert final.attributes["quantity"] >= 0, "stock must never go negative"


def test_mb3015_expired_reservation_frees_stock_for_a_new_claim():
    kg = KnowledgeGraph()
    kg.add_entity("prod_x", EntityType.ASSET, "X", {"price": 1.0, "quantity": 1})

    ok_alice, _ = try_reserve(kg, "prod_x", "alice", qty=1, hold_seconds=0.05)
    assert ok_alice is True

    # Immediately after: the unit is genuinely held, bob can't claim it.
    ok_bob_immediate, msg = try_reserve(kg, "prod_x", "bob", qty=1, hold_seconds=5.0)
    assert ok_bob_immediate is False
    assert "insufficient stock" in msg

    time.sleep(0.1)  # let alice's short hold lapse

    ok_bob_after_expiry, _ = try_reserve(kg, "prod_x", "bob", qty=1, hold_seconds=5.0)
    assert ok_bob_after_expiry is True

    # Alice's lapsed hold can no longer be confirmed — she must
    # try_reserve again, not assume her old hold still counts.
    confirmed, _ = confirm_reservation(kg, "prod_x", "alice")
    assert confirmed is False


def test_mb3015_reserving_more_than_available_fails_cleanly_no_partial_hold():
    kg = KnowledgeGraph()
    kg.add_entity("prod_y", EntityType.ASSET, "Y", {"price": 1.0, "quantity": 3})

    ok, msg = try_reserve(kg, "prod_y", "carol", qty=5)

    assert ok is False
    assert "insufficient stock" in msg
    # No partial reservation was recorded.
    assert kg.get_entity("prod_y").attributes.get("reservations", []) == []
