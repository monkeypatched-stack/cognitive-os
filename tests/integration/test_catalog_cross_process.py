"""Integration Test: Product Catalog Cross-Process Consistency.

Investigates a reported symptom: an edge-actor Pod's own PlanetaryRuntime
instance failed to resolve a product the main control-plane process had
created, with the planner reporting "unknown product". These tests prove
the ACTUAL cross-process behavior of the catalog write/read path — two
separate PlanetaryRuntime() instances sharing the real Redis this suite's
tests/conftest.py::_flush_shared_redis fixture flushes before every test
(same fixture/pattern as tests/integration/test_actor_persistence_
roundtrip.py::TestActorRestartRehydration, which proves the equivalent
guarantee for Actors) — not two views of one in-memory object.

Investigation finding (see this session's report for the full trace):
the catalog write path (kernel/domains/commerce.py::list_product/
onboard_merchant -> KnowledgeGraph.add_entity -> KnowledgeGraph._notify_
change -> PlanetaryRuntime._on_knowledge_graph_change -> Redis HSET on
monkeybrain:knowledge_graph:entities) and read path (PlanetaryRuntime.
__init__ -> _load_knowledge_graph() -> Redis HGETALL -> repopulate
self._knowledge_graph._entities/_index_add) both work correctly in a
clean, controlled reproduction — confirmed both via these tests and via
a real two-process run (control-plane + a real `uvicorn src.monkey_
brain.actor_runtime:app` edge Pod) that completed a full grocery
purchase with WORLD_VALIDATION_GATE_EXECUTE at its secure default
(true). The originally-observed "unknown product"/missing-catalog
symptom did not reproduce from a clean flush+seed — it is far more
likely explained by this session's own un-flushed, long-lived dev Redis
accumulating cross-experiment state, the same class of issue conftest.py's
_flush_shared_redis fixture already documents fixing for Actors/geography.
These tests exist to lock in the correct behavior as a regression guard,
not because a code defect was found and fixed here.
"""

from __future__ import annotations

import pytest


class _Helpers:
    @staticmethod
    def _seed_merchant_and_store(pr, merchant_actor_id: str, store_name: str):
        from src.monkey_brain.kernel.domains.commerce import onboard_merchant

        return onboard_merchant(pr.knowledge_graph, merchant_actor_id, store_name)


class TestCatalogCrossProcessReadAfterWrite(_Helpers):
    """Test 1 — process A writes, process B (a genuinely separate
    PlanetaryRuntime instance) reads."""

    def test_product_created_by_one_instance_visible_to_another(self):
        from src.monkey_brain.kernel.domains.commerce import list_product
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        pr_a = PlanetaryRuntime()
        store = self._seed_merchant_and_store(pr_a, "merchant-catalog-test-1", "Catalog Test Store")
        result = list_product(
            pr_a.knowledge_graph,
            store["store_id"],
            "merchant-catalog-test-1",
            "Cross-Process Widget",
            price=9.99,
            quantity=10,
        )
        assert result["success"], result
        product_id = result["product_id"]

        pr_b = PlanetaryRuntime()  # a real second instance, not pr_a again
        entity = pr_b.knowledge_graph.get_entity(product_id)
        assert entity is not None, (
            "a product created against one PlanetaryRuntime instance was not "
            "visible to a second, independently-constructed instance sharing "
            "the same Redis — the catalog write/read path is not actually "
            "cross-process shared"
        )
        assert entity.name == "Cross-Process Widget"
        assert entity.attributes["price"] == 9.99
        assert entity.attributes["store_id"] == store["store_id"]

    def test_store_created_by_one_instance_visible_to_another(self):
        """The store (ORGANIZATION entity) itself, not just products
        listed into it, must cross the same boundary — DeliveryCapability/
        OrderCreationCapability resolve stores by entity_id independent of
        their products."""
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        pr_a = PlanetaryRuntime()
        store = self._seed_merchant_and_store(pr_a, "merchant-catalog-test-2", "Second Test Store")

        pr_b = PlanetaryRuntime()
        entity = pr_b.knowledge_graph.get_entity(store["store_id"])
        assert entity is not None
        assert entity.name == "Second Test Store"
        assert entity.attributes["owner_id"] == "merchant-catalog-test-2"


class TestCatalogCrossProcessUpdateVisibility(_Helpers):
    """Test 2 — process A updates, process B sees the update.

    Consistency model this proves: KnowledgeGraph has NO cache-
    invalidation channel between live processes — a process only ever
    reads Redis at its own PlanetaryRuntime.__init__ (_load_knowledge_
    graph()). An already-running process's in-memory copy is NOT
    live-updated by another process's write; a NEW instance (the
    equivalent of a fresh request/Pod boot in production, which is what
    every real caller of PlanetaryRuntime() actually does) is what picks
    up the change. Both assertions below are intentional: same instance
    genuinely stale, new instance genuinely fresh.
    """

    def test_price_update_from_one_instance_seen_by_a_new_instance(self):
        from src.monkey_brain.kernel.domains.commerce import (
            list_product,
            update_product,
        )
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        pr_a = PlanetaryRuntime()
        store = self._seed_merchant_and_store(pr_a, "merchant-catalog-test-3", "Price Update Store")
        created = list_product(
            pr_a.knowledge_graph,
            store["store_id"],
            "merchant-catalog-test-3",
            "Priceable Widget",
            price=5.00,
            quantity=3,
        )
        product_id = created["product_id"]

        pr_b_before = PlanetaryRuntime()
        assert pr_b_before.knowledge_graph.get_entity(product_id).attributes["price"] == 5.00

        updated = update_product(pr_a.knowledge_graph, product_id, "merchant-catalog-test-3", price=7.50)
        assert updated["success"], updated

        # A pre-existing instance (pr_b_before) has no live-invalidation
        # channel — this is the documented consistency model, not a bug.
        assert pr_b_before.knowledge_graph.get_entity(product_id).attributes["price"] == 5.00

        # A NEW instance genuinely reflects the update.
        pr_c_after = PlanetaryRuntime()
        assert pr_c_after.knowledge_graph.get_entity(product_id).attributes["price"] == 7.50


class TestCatalogSurvivesRestart(_Helpers):
    """Test 3 — process A writes then goes out of scope (simulated
    restart); process B, booting cold, recovers the persisted catalog."""

    def test_catalog_recovers_after_simulated_restart(self):
        from src.monkey_brain.kernel.domains.commerce import list_product
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        product_id: str
        store_id: str

        def _process_a() -> tuple[str, str]:
            pr_a = PlanetaryRuntime()
            store = self._seed_merchant_and_store(pr_a, "merchant-catalog-test-4", "Restart Store")
            created = list_product(
                pr_a.knowledge_graph,
                store["store_id"],
                "merchant-catalog-test-4",
                "Restart-Surviving Widget",
                price=12.34,
                quantity=1,
            )
            return created["product_id"], store["store_id"]
            # pr_a falls out of scope here — nothing keeps it alive, same
            # as a real process exiting.

        product_id, store_id = _process_a()

        pr_b = PlanetaryRuntime()  # cold boot, process B
        entity = pr_b.knowledge_graph.get_entity(product_id)
        assert entity is not None, "catalog entry did not survive a simulated process restart"
        assert entity.attributes["price"] == 12.34
        assert pr_b.knowledge_graph.get_entity(store_id) is not None


class TestCatalogInitializationDoesNotOverwriteSharedState(_Helpers):
    """Definition-of-done requirement: "Catalog initialization cannot
    overwrite valid shared Redis state." A fresh PlanetaryRuntime()
    boot must never wipe or reset entities another process already
    persisted — _load_knowledge_graph() only ever ADDS into the local
    in-memory dict; nothing in __init__ clears the Redis hash."""

    def test_booting_a_second_instance_does_not_erase_first_instances_catalog(self):
        from src.monkey_brain.kernel.domains.commerce import list_product
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        pr_a = PlanetaryRuntime()
        store = self._seed_merchant_and_store(pr_a, "merchant-catalog-test-5", "Survives Boot Store")
        created = list_product(
            pr_a.knowledge_graph,
            store["store_id"],
            "merchant-catalog-test-5",
            "Should Not Vanish",
            price=1.00,
            quantity=1,
        )
        product_id = created["product_id"]

        # Boot several more instances in sequence, as multiple Pods would.
        for _ in range(3):
            PlanetaryRuntime()

        pr_last = PlanetaryRuntime()
        assert pr_last.knowledge_graph.get_entity(product_id) is not None, (
            "a product vanished from Redis after other PlanetaryRuntime "
            "instances booted — catalog initialization overwrote shared state"
        )


class TestCatalogOwnershipIsolation(_Helpers):
    """Test 4 equivalent — the catalog's real isolation boundary in this
    codebase is per-store ownership (require_store_owner), not a tenant/
    org field on the product itself. Proves that boundary holds across
    processes too: a merchant created and scoped in one process cannot
    write into a store owned by a different merchant, as seen by another
    process."""

    def test_cross_process_merchant_cannot_list_into_unowned_store(self):
        from src.monkey_brain.kernel.domains.commerce import list_product
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        pr_a = PlanetaryRuntime()
        store_a = self._seed_merchant_and_store(pr_a, "merchant-owner-a", "Owner A Store")

        pr_b = PlanetaryRuntime()  # different process, sees the same store
        denied = list_product(
            pr_b.knowledge_graph,
            store_a["store_id"],
            "merchant-owner-b",
            "Should Be Denied",
            price=1.00,
            quantity=1,
        )
        assert not denied["success"]
        assert "does not own" in denied["error"]
