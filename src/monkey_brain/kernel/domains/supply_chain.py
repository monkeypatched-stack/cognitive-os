"""SupplyChain domain — upstream fulfillment chain health, shared across verticals.

Extracted from the grocery vertical (kernel/domains/grocery.py) on the
same premise as domain_security.py/logistics.py/finance.py: nothing here
reasons about groceries specifically. It walks a generic
warehouse -> trucks -> supplier -> factory chain over organization
entities — the Grocery vertical composes this domain the same way any
future vertical could.

This module also fixes a real, pre-existing drift: before this
extraction, Commerce's `open_products` (via a nested closure) and this
module's `trace_supply_chain` each independently re-implemented the SAME
chain walk, and a direct comparison found they'd actually disagreed for
years on two edge cases (see `supply_chain_status`'s docstring). Both
callers now share one real implementation.
"""

from __future__ import annotations

from typing import Any

from src.monkey_brain.kernel.domains.commerce import DomainCapability


class SupplyChainCapability(DomainCapability):
    """Discoverable upstream fulfillment competency."""

    name = "supply_chain"

    def __init__(self):
        super().__init__(
            {
                "trace_supply_chain": trace_supply_chain,
                "supply_chain_ok": supply_chain_ok,
                "shortage_options": shortage_options,
                "route_to_available_warehouse": route_to_available_warehouse,
            }
        )


def supply_chain_status(all_orgs: dict, store_id: str) -> dict:
    """The single real warehouse -> trucks -> supplier -> factory walk,
    shared by `supply_chain_ok` (the boolean gate `open_products` enforces)
    and `trace_supply_chain` (the read-only diagnostic). Returns
    {"warehouse", "trucks", "supplier", "factory", "broken_links", "ok"}.

    Reproduces the exact boolean outcome the pre-extraction `open_products`
    closure already enforced (verified case by case), while fixing two
    real discrepancies that comparison surfaced between it and the old,
    separate `trace_supply_chain` walk:

    1. A warehouse with ZERO trucks assigned: `open_products` already
       treated this as not-operational (`any()` over an empty generator
       is `False`); the old `trace_supply_chain` only flagged "trucks" as
       broken when at least one truck existed but none were operational,
       silently missing the zero-trucks-assigned case. Now both agree —
       zero trucks assigned counts as broken.
    2. A `warehouse_id` that references a warehouse entity which doesn't
       actually exist in the graph: `open_products`'s operational-warehouse
       set can never contain an entity that doesn't exist, so it always
       treated this as broken; the old `trace_supply_chain` simply skipped
       its entire warehouse/truck/supplier/factory section whenever the
       warehouse lookup came back None, silently reporting nothing broken.
       Now this is flagged as a broken "warehouse" link.

    Supplier/factory keep the original, deliberately more lenient
    treatment: a `supplied_by` reference to a supplier/factory that
    doesn't exist is NOT treated as broken (only a real, existing
    supplier/factory with a non-operational status is) — this matches
    the pre-extraction code's own opt-in convention ("every warehouse
    seeded before this level is unaffected" — most seed data never
    modeled suppliers/factories at all).
    """
    store = all_orgs.get(store_id)
    result = {
        "warehouse": None,
        "trucks": [],
        "supplier": None,
        "factory": None,
        "broken_links": [],
        "ok": True,
    }
    if store is None:
        return result
    warehouse_id = store.attributes.get("warehouse_id")
    if warehouse_id is None:
        return result  # no warehouse dependency declared at all -- nothing to check

    warehouse = all_orgs.get(warehouse_id)
    if warehouse is None:
        result["broken_links"].append("warehouse")
        result["ok"] = False
        return result

    w_status = warehouse.attributes.get("status", "operational")
    w_robot_status = warehouse.attributes.get("robot_status", "operational")
    result["warehouse"] = {
        "id": warehouse_id,
        "name": warehouse.name,
        "status": w_status,
        "robot_status": w_robot_status,
    }
    if w_status != "operational":
        result["broken_links"].append("warehouse")
    if w_robot_status != "operational":
        result["broken_links"].append("warehouse_robot")

    trucks = [
        e
        for e in all_orgs.values()
        if e.attributes.get("type") == "truck"
        and e.attributes.get("assigned_warehouse_id") == warehouse_id
    ]
    result["trucks"] = [
        {
            "id": t.entity_id,
            "name": t.name,
            "status": t.attributes.get("status", "operational"),
        }
        for t in trucks
    ]
    if not any(
        t.attributes.get("status", "operational") == "operational" for t in trucks
    ):
        result["broken_links"].append("trucks")

    supplier_id = warehouse.attributes.get("supplied_by")
    supplier = all_orgs.get(supplier_id) if supplier_id else None
    if supplier is not None:
        s_status = supplier.attributes.get("status", "operational")
        result["supplier"] = {
            "id": supplier_id,
            "name": supplier.name,
            "status": s_status,
        }
        if s_status != "operational":
            result["broken_links"].append("supplier")

        factory_id = supplier.attributes.get("supplied_by")
        factory = all_orgs.get(factory_id) if factory_id else None
        if factory is not None:
            f_status = factory.attributes.get("status", "operational")
            result["factory"] = {
                "id": factory_id,
                "name": factory.name,
                "status": f_status,
            }
            if f_status != "operational":
                result["broken_links"].append("factory")

    result["ok"] = not result["broken_links"]
    return result


def supply_chain_ok(all_orgs: dict, store_id: str) -> bool:
    """The boolean gate `open_products` enforces — a store can't be
    trusted to fulfill an order if its real upstream chain is broken
    anywhere between its warehouse and that warehouse's ultimate factory.
    """
    return supply_chain_status(all_orgs, store_id)["ok"]


def route_to_available_warehouse(kg, store_id: str) -> dict:
    """MB-3048 Multi-Warehouse Routing: when a store's primary warehouse
    (attributes["warehouse_id"]) is down, check its named backups
    (attributes["backup_warehouse_ids"], tried in listed order) and
    return the first one that's actually operational.

    Read-only, like supply_chain_ok()/trace_supply_chain() — it decides
    which warehouse THIS fulfillment should route through, it never
    rewrites the store's own attributes["warehouse_id"]; that stays the
    store's real primary for every other request. A store with no
    warehouse dependency declared at all (no warehouse_id) has nothing
    to route — success trivially, "routed": "none".
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    all_orgs = {e.entity_id: e for e in kg.entities_by_type(EntityType.ORGANIZATION)}
    store = all_orgs.get(store_id)
    if store is None:
        return {"success": False, "error": f"no such store {store_id!r}"}

    primary_id = store.attributes.get("warehouse_id")
    if primary_id is None:
        return {"success": True, "warehouse_id": None, "routed": "none"}

    if supply_chain_ok(all_orgs, store_id):
        return {"success": True, "warehouse_id": primary_id, "routed": "primary"}

    for backup_id in store.attributes.get("backup_warehouse_ids", []):
        backup = all_orgs.get(backup_id)
        if (
            backup is not None
            and backup.attributes.get("status", "operational") == "operational"
        ):
            return {
                "success": True,
                "warehouse_id": backup_id,
                "routed": "backup",
                "reason": f"primary warehouse {primary_id!r} unavailable",
            }

    return {
        "success": False,
        "error": f"no operational warehouse available for store {store_id!r} — primary and every backup are down",
    }


def shortage_options(
    item: str,
    store_offers: list[dict[str, Any]],
    substitutions: list[str] | None = None,
) -> dict[str, Any]:
    """Return grounded options for an unavailable item.

    This is an observation builder, not a selection policy. The LLM planner
    receives substitute, alternate-store, delayed, and split-order options
    and decides among them using the household's goals and constraints.
    """
    available = [o for o in store_offers if float(o.get("quantity", 0)) > 0]
    return {
        "item": item,
        "substitute_brands": tuple(substitutions or ()),
        "alternate_stores": tuple(o for o in available if o.get("store_id")),
        "delayed_delivery": tuple(o for o in store_offers if o.get("restock_at")),
        "split_orders": tuple(o for o in available if o.get("split_eligible", True)),
    }


def trace_supply_chain(kg, store_id: str) -> dict:
    """Level 31 (GS-3100) — "entire graph planned": traces the REAL full
    enterprise chain for a store (factory -> supplier -> warehouse ->
    trucks -> store, plus bank/payment processor and available riders) —
    from actual KG entities and their real relationships (supplied_by,
    warehouse_id, assigned_warehouse_id), not a static diagram. Reports
    exactly which real link (if any) is broken, so a disruption anywhere
    in the chain is visible in one place instead of only ever showing up
    indirectly as "this store has no products," with no explanation why.
    Read-only — genuinely the SAME chain `supply_chain_ok` enforces now
    (shared via `supply_chain_status`), not a second copy of the gating
    logic that could silently drift from it.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    all_orgs = {
        e.entity_id: e for e in kg.entities if e.entity_type == EntityType.ORGANIZATION
    }
    store = all_orgs.get(store_id)
    if store is None:
        return {"traced": False, "reason": "store not found"}

    chain = {
        "store": {
            "id": store_id,
            "name": store.name,
            "is_open": store.attributes.get("is_open", True),
        }
    }
    broken_links = []
    if not chain["store"]["is_open"]:
        broken_links.append("store")

    supply_status = supply_chain_status(all_orgs, store_id)
    if supply_status["warehouse"] is not None:
        chain["warehouse"] = supply_status["warehouse"]
        chain["trucks"] = supply_status["trucks"]
        if supply_status["supplier"] is not None:
            chain["supplier"] = supply_status["supplier"]
        if supply_status["factory"] is not None:
            chain["factory"] = supply_status["factory"]
    broken_links.extend(supply_status["broken_links"])

    bank = next(
        (e for e in all_orgs.values() if e.attributes.get("type") == "bank"), None
    )
    if bank is not None:
        chain["bank"] = {"id": bank.entity_id, "name": bank.name}

    processor = next(
        (
            e
            for e in all_orgs.values()
            if e.attributes.get("type") == "payment_processor"
        ),
        None,
    )
    if processor is not None:
        p_status = processor.attributes.get("status", "operational")
        chain["payment_processor"] = {
            "id": processor.entity_id,
            "name": processor.name,
            "status": p_status,
        }
        if p_status != "operational":
            broken_links.append("payment_processor")

    riders = [
        e
        for e in kg.entities
        if e.entity_type == EntityType.PERSON
        and e.attributes.get("status") == "available"
    ]
    chain["available_riders"] = len(riders)
    if not riders:
        broken_links.append("riders")

    return {
        "traced": True,
        "chain": chain,
        "fulfillable": not broken_links,
        "broken_links": broken_links,
    }
