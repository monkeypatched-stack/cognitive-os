"""update_organization() — admin edit of any ORGANIZATION entity (store,
warehouse, supplier, factory, truck, bank, processor). Added so a real
disruption (a warehouse fire) can be injected live, over HTTP, against
the same real commerce KG open_products()/supply_chain_ok() actually
read — see PATCH /organizations/{id} in api/routes/commerce.py, which
wraps this directly, and test_world_mutation.py's
test_world008_warehouse_fire_mid_tick_self_heals_within_order_creation
for the resulting self-heal behavior this makes possible to trigger.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import update_organization
from src.monkey_brain.kernel.domains.supply_chain import supply_chain_ok
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph


def test_setting_a_warehouses_status_breaks_its_supply_chain():
    kg = KnowledgeGraph()
    kg.add_entity("store_a", EntityType.ORGANIZATION, "Trader Joe's", {"warehouse_id": "wh_a"})
    kg.add_entity("wh_a", EntityType.ORGANIZATION, "Warehouse A", {"status": "operational"})
    # A warehouse with zero trucks assigned is itself treated as
    # not-operational (supply_chain_status's own documented rule) --
    # needs at least one operational truck for the baseline "ok" case.
    kg.add_entity(
        "truck_1",
        EntityType.ORGANIZATION,
        "Truck 1",
        {"type": "truck", "assigned_warehouse_id": "wh_a", "status": "operational"},
    )
    all_orgs = {e.entity_id: e for e in kg.entities_by_type(EntityType.ORGANIZATION)}
    assert supply_chain_ok(all_orgs, "store_a") is True

    result = update_organization(kg, "wh_a", status="on_fire")

    assert result["success"] is True
    assert kg.get_entity("wh_a").attributes["status"] == "on_fire"
    all_orgs = {e.entity_id: e for e in kg.entities_by_type(EntityType.ORGANIZATION)}
    assert supply_chain_ok(all_orgs, "store_a") is False


def test_no_merchant_ownership_check_unlike_update_product():
    """Warehouses aren't merchant-owned at all (only stores are, via
    onboard_merchant's owner_id) -- this is gated purely by the route's
    own permission, not a per-org owner claim."""
    kg = KnowledgeGraph()
    kg.add_entity("wh_a", EntityType.ORGANIZATION, "Warehouse A", {"status": "operational"})

    result = update_organization(kg, "wh_a", status="on_fire")

    assert result["success"] is True


def test_a_new_org_id_is_created_not_rejected():
    """Upsert, not edit-only: onboard_merchant only ever creates a
    Store -- a warehouse/truck/supplier/factory a demo wants to set up
    first has no dedicated creation route of its own, so PATCHing a new
    id creates it (same overwrite/create-on-reuse convention
    KnowledgeGraph.add_entity already has for every other entity type)."""
    kg = KnowledgeGraph()

    result = update_organization(kg, "wh_new", name="New Warehouse", status="operational")

    assert result["success"] is True
    entity = kg.get_entity("wh_new")
    assert entity is not None
    assert entity.entity_type == EntityType.ORGANIZATION
    assert entity.name == "New Warehouse"
    assert entity.attributes["status"] == "operational"


def test_partial_update_only_touches_named_attributes():
    kg = KnowledgeGraph()
    kg.add_entity(
        "wh_a",
        EntityType.ORGANIZATION,
        "Warehouse A",
        {
            "status": "operational",
            "robot_status": "operational",
            "supplied_by": "supplier_1",
        },
    )

    update_organization(kg, "wh_a", status="on_fire")

    attrs = kg.get_entity("wh_a").attributes
    assert attrs["status"] == "on_fire"
    assert attrs["robot_status"] == "operational"
    assert attrs["supplied_by"] == "supplier_1"
