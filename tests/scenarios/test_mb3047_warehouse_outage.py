"""MB-3047 Warehouse Outage — warehouse unavailable scenario.

Warehouse unavailable.

Like MB-3010/3012/3013/3015/3028/3032/3033/3046, no new code was
needed: supply_chain.py's supply_chain_status()/supply_chain_ok()
(Level 22, already fully wired into open_products()'s catalog filter)
already treat a non-operational warehouse as a broken link and exclude
every product from a store depending on it — a real warehouse outage
correctly makes that store's catalog disappear rather than silently
offering products it can't actually fulfill. trace_supply_chain()
reports exactly which link is broken. Once the warehouse recovers, the
store's products are browsable again automatically.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.grocery import open_products
from src.monkey_brain.kernel.domains.supply_chain import (
    supply_chain_ok,
    trace_supply_chain,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

PRODUCT_ID = "p1"
STORE_ID = "store_1"
WAREHOUSE_ID = "warehouse_1"


def _seed(warehouse_status: str = "down") -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(
        WAREHOUSE_ID,
        EntityType.ORGANIZATION,
        "Main Warehouse",
        {"status": warehouse_status},
    )
    kg.add_entity(
        "truck_1",
        EntityType.ORGANIZATION,
        "Truck 1",
        {
            "type": "truck",
            "assigned_warehouse_id": WAREHOUSE_ID,
            "status": "operational",
        },
    )
    kg.add_entity(
        STORE_ID,
        EntityType.ORGANIZATION,
        "Corner Store",
        {"warehouse_id": WAREHOUSE_ID},
    )
    kg.add_entity(
        PRODUCT_ID,
        EntityType.ASSET,
        "Milk",
        {
            "price": 3.0,
            "quantity": 10,
            "store_id": STORE_ID,
            "product": True,
        },
    )
    return kg


def test_mb3047_store_with_down_warehouse_excluded_from_catalog():
    kg = _seed(warehouse_status="down")

    catalog = open_products(kg)

    assert not any(p.entity_id == PRODUCT_ID for p in catalog)


def test_mb3047_supply_chain_ok_reports_the_warehouse_as_broken():
    kg = _seed(warehouse_status="down")
    all_orgs = {e.entity_id: e for e in kg.entities_by_type(EntityType.ORGANIZATION)}

    assert supply_chain_ok(all_orgs, STORE_ID) is False


def test_mb3047_trace_supply_chain_names_the_broken_link():
    kg = _seed(warehouse_status="down")

    trace = trace_supply_chain(kg, STORE_ID)

    assert trace["fulfillable"] is False
    assert "warehouse" in trace["broken_links"]


def test_mb3047_recovered_warehouse_restores_catalog_visibility():
    kg = _seed(warehouse_status="down")
    kg.update_entity(WAREHOUSE_ID, attributes={"status": "operational"})

    catalog = open_products(kg)

    assert any(p.entity_id == PRODUCT_ID for p in catalog)
