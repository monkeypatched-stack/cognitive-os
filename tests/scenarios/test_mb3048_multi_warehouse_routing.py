"""MB-3048 Multi-Warehouse Routing — route order to alternate warehouse
scenario.

Route order to alternate warehouse.

MB-3047 established that a down primary warehouse correctly excludes a
store's products from the catalog — but there was no way to route
around it: supply_chain.py modeled exactly one warehouse per store, no
concept of an alternate. Built
kernel/domains/supply_chain.py::route_to_available_warehouse() for
this: when the primary (attributes["warehouse_id"]) is down, it checks
the store's named backups (attributes["backup_warehouse_ids"], tried in
listed order) and returns the first one actually operational. Read-only
like supply_chain_ok()/trace_supply_chain() — it decides which
warehouse THIS routing request should use, it never rewrites the
store's own primary warehouse_id.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.supply_chain import (
    SupplyChainCapability,
    route_to_available_warehouse,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

STORE_ID = "store_1"


def _seed(primary_status: str = "down", backup_status: str = "operational") -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(
        "warehouse_primary",
        EntityType.ORGANIZATION,
        "Primary Warehouse",
        {"status": primary_status},
    )
    kg.add_entity(
        "warehouse_backup",
        EntityType.ORGANIZATION,
        "Backup Warehouse",
        {"status": backup_status},
    )
    kg.add_entity(
        "truck_1",
        EntityType.ORGANIZATION,
        "Truck 1",
        {
            "type": "truck",
            "assigned_warehouse_id": "warehouse_primary",
            "status": "operational",
        },
    )
    kg.add_entity(
        STORE_ID,
        EntityType.ORGANIZATION,
        "Corner Store",
        {
            "warehouse_id": "warehouse_primary",
            "backup_warehouse_ids": ["warehouse_backup"],
        },
    )
    return kg


def test_mb3048_operational_primary_is_used_directly():
    kg = _seed(primary_status="operational")

    result = route_to_available_warehouse(kg, STORE_ID)

    assert result["success"] is True
    assert result["warehouse_id"] == "warehouse_primary"
    assert result["routed"] == "primary"


def test_mb3048_down_primary_routes_to_an_operational_backup():
    kg = _seed(primary_status="down", backup_status="operational")

    result = route_to_available_warehouse(kg, STORE_ID)

    assert result["success"] is True
    assert result["warehouse_id"] == "warehouse_backup"
    assert result["routed"] == "backup"


def test_mb3048_primary_and_backup_both_down_is_an_honest_failure():
    kg = _seed(primary_status="down", backup_status="down")

    result = route_to_available_warehouse(kg, STORE_ID)

    assert result["success"] is False
    assert "no operational warehouse" in result["error"]


def test_mb3048_store_with_no_warehouse_dependency_has_nothing_to_route():
    kg = KnowledgeGraph()
    kg.add_entity("store_no_warehouse", EntityType.ORGANIZATION, "No-Warehouse Store", {})

    result = route_to_available_warehouse(kg, "store_no_warehouse")

    assert result["success"] is True
    assert result["routed"] == "none"


def test_mb3048_routing_never_mutates_the_stores_primary_warehouse():
    kg = _seed(primary_status="down", backup_status="operational")

    route_to_available_warehouse(kg, STORE_ID)

    assert kg.get_entity(STORE_ID).attributes["warehouse_id"] == "warehouse_primary"


def test_mb3048_unknown_store_is_an_honest_failure():
    kg = _seed()

    result = route_to_available_warehouse(kg, "does-not-exist")

    assert result["success"] is False
    assert "no such store" in result["error"]


def test_mb3048_route_to_available_warehouse_via_capability():
    kg = _seed(primary_status="down", backup_status="operational")
    cap = SupplyChainCapability()

    assert cap.can_handle("route_to_available_warehouse")
    result = cap.invoke("route_to_available_warehouse", kg, STORE_ID)

    assert result["success"] is True
